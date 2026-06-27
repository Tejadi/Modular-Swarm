"""Jetson-side fusion EKF.

The nRF already runs a GPS+IMU EKF and streams a fused pose. The Jetson refines
it with two extra sources when available:

  * camera VIO  — a relative motion delta (better in GPS-degraded conditions)
  * swarm peers — range constraints to nearby modules, gated to within 50 m
                  (farther peers add nothing and cost CPU, so they are dropped)

This is a loose *fusion* filter (constant-velocity, ~1 Hz), not a strapdown
integrator — the heavy inertial work already happened on the nRF. Same 5-state
local ENU frame as ekf.c (pN, pE, vN, vE, psi), anchored at the first nRF fix,
using the same meters-per-degree constants so every layer shares one frame.

Pure Python (a 5-state filter at 1 Hz is trivial); no numpy dependency.
"""

from __future__ import annotations

import math

PN, PE, VN, VE, PSI = range(5)
MPD_LAT = 111320.0
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi

PEER_GATE_M = 50.0          # discard peer constraints beyond this range
P_DIAG_MIN = 1e-6
Q_VEL = 0.5                 # (m/s)^2/s process noise on velocity
Q_YAW = 0.05               # (rad/s)^2/s process noise on heading


def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class JetsonEKF:
    def __init__(self) -> None:
        self.x = [0.0] * 5
        self.P = [0.0] * 25
        self.lat0 = self.lon0 = 0.0
        self.mpd_lon = MPD_LAT
        self.anchored = False
        self.P[self._i(PN, PN)] = 1.0e6
        self.P[self._i(PE, PE)] = 1.0e6
        self.P[self._i(VN, VN)] = 25.0
        self.P[self._i(VE, VE)] = 25.0
        self.P[self._i(PSI, PSI)] = math.pi * math.pi

    @staticmethod
    def _i(r: int, c: int) -> int:
        return r * 5 + c

    # --- frame helpers ---

    def set_anchor(self, lat: float, lon: float) -> None:
        self.lat0, self.lon0 = lat, lon
        self.mpd_lon = MPD_LAT * math.cos(lat * DEG2RAD)
        self.x[PN] = self.x[PE] = 0.0
        self.P[self._i(PN, PN)] = self.P[self._i(PE, PE)] = 25.0
        self.anchored = True

    def to_enu(self, lat: float, lon: float) -> tuple[float, float]:
        return (lat - self.lat0) * MPD_LAT, (lon - self.lon0) * self.mpd_lon

    def to_ll(self, pN: float, pE: float) -> tuple[float, float]:
        return self.lat0 + pN / MPD_LAT, self.lon0 + pE / self.mpd_lon

    # --- prediction (constant velocity) ---

    def predict(self, dt: float) -> None:
        if dt <= 0.0 or dt > 5.0 or not self.anchored:
            return
        self.x[PN] += self.x[VN] * dt
        self.x[PE] += self.x[VE] * dt
        # F = I + dt couplings (pos depends on vel).
        F = [0.0] * 25
        for i in range(5):
            F[self._i(i, i)] = 1.0
        F[self._i(PN, VN)] = dt
        F[self._i(PE, VE)] = dt
        FP = self._mul(F, self.P)
        self.P = self._mul_abt(FP, F)
        P = self.P
        qpp = Q_VEL * dt ** 3 / 3.0
        qpv = Q_VEL * dt * dt * 0.5
        qvv = Q_VEL * dt
        P[self._i(PN, PN)] += qpp; P[self._i(PN, VN)] += qpv
        P[self._i(VN, PN)] += qpv; P[self._i(VN, VN)] += qvv
        P[self._i(PE, PE)] += qpp; P[self._i(PE, VE)] += qpv
        P[self._i(VE, PE)] += qpv; P[self._i(VE, VE)] += qvv
        P[self._i(PSI, PSI)] += Q_YAW * dt
        self._condition()

    # --- generic scalar EKF update (handles selection + range measurements) ---

    def _update_scalar(self, H: list[float], y: float, r: float, is_angle: bool = False) -> None:
        if is_angle:
            y = _wrap_pi(y)
        PHt = [sum(self.P[self._i(i, j)] * H[j] for j in range(5)) for i in range(5)]
        S = sum(H[i] * PHt[i] for i in range(5)) + r
        if S < 1e-9:
            return
        K = [PHt[i] / S for i in range(5)]
        for i in range(5):
            self.x[i] += K[i] * y
        if is_angle:
            self.x[PSI] = _wrap_pi(self.x[PSI])
        for i in range(5):
            for j in range(5):
                self.P[self._i(i, j)] -= K[i] * PHt[j]
        self._condition()

    def _sel(self, idx: int) -> list[float]:
        h = [0.0] * 5
        h[idx] = 1.0
        return h

    # --- measurement updates ---

    def update_nrf(self, lat: float, lon: float, heading_deg: float,
                   vel_n: float, vel_e: float, pos_std: float, hdg_std: float) -> None:
        """Fold in the nRF's authoritative fused pose (the primary source)."""
        if not self.anchored:
            self.set_anchor(lat, lon)
            self.x[VN], self.x[VE] = vel_n, vel_e
            return
        pN, pE = self.to_enu(lat, lon)
        rp = max(0.25, pos_std * pos_std)
        self._update_scalar(self._sel(PN), pN - self.x[PN], rp)
        self._update_scalar(self._sel(PE), pE - self.x[PE], rp)
        self._update_scalar(self._sel(VN), vel_n - self.x[VN], 0.25)
        self._update_scalar(self._sel(VE), vel_e - self.x[VE], 0.25)
        # nRF compass heading -> math yaw (CCW from East).
        psi = math.radians(90.0 - heading_deg)
        rh = max(1.0, hdg_std * hdg_std) * DEG2RAD * DEG2RAD
        self._update_scalar(self._sel(PSI), psi - self.x[PSI], rh, is_angle=True)

    def update_vio(self, d_north: float, d_east: float, d_yaw: float, dt: float,
                   cov: float = 0.04) -> None:
        """Fold a camera VIO motion delta in as a velocity + yaw-rate measurement."""
        if dt <= 0.0 or not self.anchored:
            return
        self._update_scalar(self._sel(VN), d_north / dt - self.x[VN], cov / (dt * dt))
        self._update_scalar(self._sel(VE), d_east / dt - self.x[VE], cov / (dt * dt))
        if abs(d_yaw) > 1e-4:
            self._update_scalar(self._sel(PSI),
                                _wrap_pi(self.x[PSI] + d_yaw) - self.x[PSI],
                                cov, is_angle=True)

    def update_range(self, peer_pN: float, peer_pE: float, range_m: float,
                     std_m: float = 3.0) -> bool:
        """Range constraint to a peer at a known ENU position (nonlinear scalar).

        Returns False if the peer is beyond the 50 m gate (dropped)."""
        if not self.anchored or range_m <= 0.0 or range_m > PEER_GATE_M:
            return False
        dN = self.x[PN] - peer_pN
        dE = self.x[PE] - peer_pE
        d = math.hypot(dN, dE)
        if d < 0.5:
            return False
        H = [dN / d, dE / d, 0.0, 0.0, 0.0]
        self._update_scalar(H, range_m - d, std_m * std_m)
        return True

    # --- readout ---

    def get_fix(self) -> dict:
        lat, lon = self.to_ll(self.x[PN], self.x[PE])
        compass = (90.0 - self.x[PSI] * RAD2DEG) % 360.0
        pos_std = math.sqrt(0.5 * (self.P[self._i(PN, PN)] + self.P[self._i(PE, PE)]))
        hdg_std = math.sqrt(max(0.0, self.P[self._i(PSI, PSI)])) * RAD2DEG
        return {
            "lat": lat, "lon": lon,
            "vel_n": self.x[VN], "vel_e": self.x[VE],
            "heading": compass, "pos_std": pos_std, "hdg_std": hdg_std,
            "converged": pos_std < 10.0,
        }

    # --- linear algebra ---

    def _condition(self) -> None:
        P = self.P
        for i in range(5):
            for j in range(i + 1, 5):
                a = 0.5 * (P[self._i(i, j)] + P[self._i(j, i)])
                P[self._i(i, j)] = P[self._i(j, i)] = a
            if P[self._i(i, i)] < P_DIAG_MIN:
                P[self._i(i, i)] = P_DIAG_MIN

    def _mul(self, a, b):
        o = [0.0] * 25
        for i in range(5):
            for j in range(5):
                o[self._i(i, j)] = sum(a[self._i(i, k)] * b[self._i(k, j)] for k in range(5))
        return o

    def _mul_abt(self, a, b):
        o = [0.0] * 25
        for i in range(5):
            for j in range(5):
                o[self._i(i, j)] = sum(a[self._i(i, k)] * b[self._i(j, k)] for k in range(5))
        return o
