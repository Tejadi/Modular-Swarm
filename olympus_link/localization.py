"""Localization for modules without their own GPS fix.

Three sources are fused, in order of trust:
  1. GPS — modules that carry a GPS sensor are anchors with known lat/lon.
  2. Ranged — a non-GPS module is multilaterated from RTT/RSSI ranges to anchors
     (least squares). Needs >= 3 anchors in range for a 2D fix.
  3. IMU dead-reckoning — when a module reports an IMU-only position and no
     ranges are available, that estimate is kept (low quality); when ranges ARE
     available it is fused with the multilateration result.

The nRF radio cannot measure true time-of-flight, so a "range" here is the
coarse RTT/RSSI estimate the firmware reports in its neighbor table. Accuracy is
meters-to-tens-of-meters and is surfaced as a position quality score.

Pure Python (no numpy) so it runs anywhere the rest of olympus_link does.
"""

from __future__ import annotations

import math

import swarm_proto as sp
from model import Position, Registry

_M_PER_DEG_LAT = 111320.0


def _mpd_lon(lat0: float) -> float:
    return 111320.0 * math.cos(math.radians(lat0))


def _to_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Local ENU meters about a reference (equirectangular, fine at swarm scale)."""
    return ((lon - lon0) * _mpd_lon(lat0), (lat - lat0) * _M_PER_DEG_LAT)


def _to_ll(x: float, y: float, lat0: float, lon0: float) -> tuple[float, float]:
    return (lat0 + y / _M_PER_DEG_LAT, lon0 + x / _mpd_lon(lat0))


def multilaterate(anchors_xy: list[tuple[float, float]],
                  ranges_m: list[float],
                  guess: tuple[float, float] | None = None,
                  iters: int = 30) -> tuple[float, float, float] | None:
    """Least-squares position from anchor positions + measured ranges.

    Returns (x, y, rms_residual_m) or None if under-determined / non-convergent.
    Gauss-Newton on f_i = |p - a_i| - r_i.
    """
    n = len(anchors_xy)
    if n < 3 or n != len(ranges_m):
        return None

    if guess is None:
        gx = sum(a[0] for a in anchors_xy) / n
        gy = sum(a[1] for a in anchors_xy) / n
    else:
        gx, gy = guess
    px, py = gx, gy

    for _ in range(iters):
        # Build normal equations J^T J dp = -J^T f for the 2D unknown (px, py).
        jtj00 = jtj01 = jtj11 = 0.0
        jtf0 = jtf1 = 0.0
        for (ax, ay), r in zip(anchors_xy, ranges_m):
            dx, dy = px - ax, py - ay
            d = math.hypot(dx, dy)
            if d < 1e-6:
                d = 1e-6
            f = d - r
            j0, j1 = dx / d, dy / d
            jtj00 += j0 * j0
            jtj01 += j0 * j1
            jtj11 += j1 * j1
            jtf0 += j0 * f
            jtf1 += j1 * f
        # Solve the 2x2 system with a small Levenberg damping for stability.
        lam = 1e-3
        a = jtj00 + lam
        b = jtj01
        c = jtj11 + lam
        det = a * c - b * b
        if abs(det) < 1e-9:
            return None
        dpx = -(c * jtf0 - b * jtf1) / det
        dpy = -(a * jtf1 - b * jtf0) / det
        px += dpx
        py += dpy
        if math.hypot(dpx, dpy) < 1e-3:
            break

    # Residual RMS in meters.
    sq = 0.0
    for (ax, ay), r in zip(anchors_xy, ranges_m):
        sq += (math.hypot(px - ax, py - ay) - r) ** 2
    rms = math.sqrt(sq / n)
    return px, py, rms


def _quality_from_rms(rms_m: float, n_anchors: int) -> int:
    """Map fit residual + anchor count to a 0..255 confidence score."""
    base = 1.0 / (1.0 + rms_m / 5.0)          # 1.0 at perfect fit, decays with error
    boost = min(1.0, n_anchors / 5.0)          # more anchors -> better geometry
    return max(1, min(255, int(255 * base * (0.6 + 0.4 * boost))))


def _gather_ranges(reg: Registry, target_eui: str) -> dict[str, float]:
    """Collect best range (meters) from `target` to each anchor.

    A range can be observed from either side of a link, so we look at the
    target's neighbor table and at every anchor's neighbor table.
    """
    out: dict[str, float] = {}

    def consider(anchor_eui: str, range_cm: int) -> None:
        if range_cm <= 0:
            return
        r = range_cm / 100.0
        if anchor_eui not in out or r < out[anchor_eui]:
            out[anchor_eui] = r

    target = reg.modules.get(target_eui)
    if target:
        for nb_eui, nb in target.neighbors.items():
            anchor = reg.modules.get(nb_eui)
            if anchor and anchor.is_anchor:
                consider(nb_eui, nb.range_cm)
    for m in reg.modules.values():
        if m.is_anchor and target_eui in m.neighbors:
            consider(m.eui, m.neighbors[target_eui].range_cm)
    return out


def localize(reg: Registry) -> list[str]:
    """Fill in positions for non-anchor modules. Returns ids that got a fix."""
    anchors = [m for m in reg.online_modules() if m.is_anchor]
    if len(anchors) < 3:
        return []  # not enough references to multilaterate anybody

    lat0, lon0 = anchors[0].position.lat, anchors[0].position.lon
    updated: list[str] = []

    for m in reg.online_modules():
        if m.is_anchor:
            continue
        ranges = _gather_ranges(reg, m.eui)
        usable = {e: r for e, r in ranges.items() if e in reg.modules and reg.modules[e].is_anchor}
        if len(usable) < 3:
            continue  # leave IMU/none position as-is; not enough anchors

        anchors_xy, rs = [], []
        for anchor_eui, r in usable.items():
            ap = reg.modules[anchor_eui].position
            anchors_xy.append(_to_xy(ap.lat, ap.lon, lat0, lon0))
            rs.append(r)

        # Seed from the previous fix if we have one (faster, more stable).
        guess = None
        if m.position.valid:
            guess = _to_xy(m.position.lat, m.position.lon, lat0, lon0)

        sol = multilaterate(anchors_xy, rs, guess)
        if sol is None:
            continue
        x, y, rms = sol
        lat, lon = _to_ll(x, y, lat0, lon0)
        ranged_q = _quality_from_rms(rms, len(usable))

        # Fuse with an IMU dead-reckoning estimate if the module reported one.
        if m.position.source == sp.PosSource.IMU and m.position.valid:
            imu_q = max(1, m.position.quality)
            w_r, w_i = ranged_q, imu_q
            lat = (lat * w_r + m.position.lat * w_i) / (w_r + w_i)
            lon = (lon * w_r + m.position.lon * w_i) / (w_r + w_i)
            source = sp.PosSource.FUSED
            quality = min(255, ranged_q + imu_q // 4)
        else:
            source = sp.PosSource.RANGED
            quality = ranged_q

        m.position = Position(lat=lat, lon=lon, alt=m.position.alt,
                              heading=m.position.heading, source=source,
                              quality=quality, fixed_at=m.position.fixed_at)
        updated.append(m.eui)

    return updated
