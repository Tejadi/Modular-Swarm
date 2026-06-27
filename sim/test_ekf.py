"""Pure-Python mirror of swarm_node/src/ekf.c — de-risks the filter math before
flashing. The equations, indices, and constants here track ekf.c line for line;
if you change one, change both. Run:

    python3 sim/test_ekf.py        # prints PASS/FAIL, non-zero exit on failure

Scenarios: a straight east drive (accelerate then cruise) with 1 Hz noisy GPS at
100 Hz IMU, and a GPS-dropout dead-reckoning leg. Asserts the fused fix tracks
truth, heading locks to the GPS course, and dropout drift stays bounded.
"""

from __future__ import annotations

import math
import random
import sys

PN, PE, VN, VE, PSI = range(5)
MPD_LAT = 111320.0
DEG2RAD = math.pi / 180.0
RAD2DEG = 180.0 / math.pi

Q_ACCEL = 1.0
Q_GYRO = 0.05
R_GPS_VEL = 0.25
R_GPS_HDG = 100.0 * DEG2RAD * DEG2RAD
ZUPT_R = 0.01
GPS_VEL_HDG_MIN = 0.7
P_DIAG_MIN = 1e-6
CONVERGE_STD_M = 10.0


def _wrap_pi(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


class EKF:
    def __init__(self) -> None:
        self.x = [0.0] * 5
        self.P = [0.0] * 25
        self.lat0 = self.lon0 = 0.0
        self.mpd_lon = MPD_LAT
        self.anchored = False
        self.converged = False
        self.P[self._i(PN, PN)] = 1.0e6
        self.P[self._i(PE, PE)] = 1.0e6
        self.P[self._i(VN, VN)] = 25.0
        self.P[self._i(VE, VE)] = 25.0
        self.P[self._i(PSI, PSI)] = math.pi * math.pi

    @staticmethod
    def _i(r: int, c: int) -> int:
        return r * 5 + c

    def _condition(self) -> None:
        P = self.P
        for i in range(5):
            for j in range(i + 1, 5):
                avg = 0.5 * (P[self._i(i, j)] + P[self._i(j, i)])
                P[self._i(i, j)] = P[self._i(j, i)] = avg
            if P[self._i(i, i)] < P_DIAG_MIN:
                P[self._i(i, i)] = P_DIAG_MIN

    def set_anchor(self, lat: float, lon: float) -> None:
        self.lat0, self.lon0 = lat, lon
        self.mpd_lon = MPD_LAT * math.cos(lat * DEG2RAD)
        self.x[PN] = self.x[PE] = 0.0
        self.P[self._i(PN, PN)] = 25.0
        self.P[self._i(PE, PE)] = 25.0
        self.anchored = True

    def predict(self, ax: float, ay: float, gyro_z: float, dt: float) -> None:
        if dt <= 0.0 or dt > 0.5:
            return
        x = self.x
        psi = _wrap_pi(x[PSI] + gyro_z * dt)
        c, sn = math.cos(psi), math.sin(psi)
        aE = ax * c - ay * sn
        aN = ax * sn + ay * c
        x[PN] += x[VN] * dt + 0.5 * aN * dt * dt
        x[PE] += x[VE] * dt + 0.5 * aE * dt * dt
        x[VN] += aN * dt
        x[VE] += aE * dt
        x[PSI] = psi

        F = [0.0] * 25
        for i in range(5):
            F[self._i(i, i)] = 1.0
        F[self._i(PN, VN)] = dt
        F[self._i(PE, VE)] = dt
        F[self._i(PN, PSI)] = 0.5 * dt * dt * aE
        F[self._i(PE, PSI)] = -0.5 * dt * dt * aN
        F[self._i(VN, PSI)] = dt * aE
        F[self._i(VE, PSI)] = -dt * aN

        FP = self._mul(F, self.P)
        self.P = self._mul_abt(FP, F)

        P = self.P
        qpp = Q_ACCEL * dt ** 3 / 3.0
        qpv = Q_ACCEL * dt * dt * 0.5
        qvv = Q_ACCEL * dt
        P[self._i(PN, PN)] += qpp
        P[self._i(PN, VN)] += qpv
        P[self._i(VN, PN)] += qpv
        P[self._i(VN, VN)] += qvv
        P[self._i(PE, PE)] += qpp
        P[self._i(PE, VE)] += qpv
        P[self._i(VE, PE)] += qpv
        P[self._i(VE, VE)] += qvv
        P[self._i(PSI, PSI)] += Q_GYRO * dt
        self._condition()

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

    def _update1(self, idx, z, r, is_angle):
        P = self.P
        y = z - self.x[idx]
        if is_angle:
            y = _wrap_pi(y)
        S = P[self._i(idx, idx)] + r
        if S < 1e-9:
            return
        K = [P[self._i(k, idx)] / S for k in range(5)]
        for k in range(5):
            self.x[k] += K[k] * y
        hp = [P[self._i(idx, c)] for c in range(5)]
        for rr in range(5):
            for c in range(5):
                P[self._i(rr, c)] -= K[rr] * hp[c]
        if is_angle:
            self.x[PSI] = _wrap_pi(self.x[PSI])
        self._condition()

    def _update2(self, i, j, z0, z1, r0, r1):
        P = self.P
        y0 = z0 - self.x[i]
        y1 = z1 - self.x[j]
        s00 = P[self._i(i, i)] + r0
        s01 = P[self._i(i, j)]
        s10 = P[self._i(j, i)]
        s11 = P[self._i(j, j)] + r1
        det = s00 * s11 - s01 * s10
        if abs(det) < 1e-9:
            return
        invd = 1.0 / det
        si00, si01 = s11 * invd, -s01 * invd
        si10, si11 = -s10 * invd, s00 * invd
        K = []
        for k in range(5):
            a, b = P[self._i(k, i)], P[self._i(k, j)]
            K.append((a * si00 + b * si10, a * si01 + b * si11))
        for k in range(5):
            self.x[k] += K[k][0] * y0 + K[k][1] * y1
        hpi = [P[self._i(i, c)] for c in range(5)]
        hpj = [P[self._i(j, c)] for c in range(5)]
        for rr in range(5):
            for c in range(5):
                P[self._i(rr, c)] -= K[rr][0] * hpi[c] + K[rr][1] * hpj[c]
        self._condition()

    def update_gps_pos(self, lat, lon, std_m):
        if not self.anchored:
            self.set_anchor(lat, lon)
            return
        pN = (lat - self.lat0) * MPD_LAT
        pE = (lon - self.lon0) * self.mpd_lon
        r = std_m * std_m
        self._update2(PN, PE, pN, pE, r, r)
        pos_var = 0.5 * (self.P[self._i(PN, PN)] + self.P[self._i(PE, PE)])
        self.converged = pos_var < CONVERGE_STD_M ** 2

    def update_gps_vel(self, speed, course_rad):
        vN = speed * math.cos(course_rad)
        vE = speed * math.sin(course_rad)
        self._update2(VN, VE, vN, vE, R_GPS_VEL, R_GPS_VEL)
        if speed > GPS_VEL_HDG_MIN:
            self._update1(PSI, math.atan2(vN, vE), R_GPS_HDG, True)

    def update_zupt(self):
        self._update2(VN, VE, 0.0, 0.0, ZUPT_R, ZUPT_R)

    def get_fix(self):
        lat = self.lat0 + self.x[PN] / MPD_LAT
        lon = self.lon0 + self.x[PE] / self.mpd_lon
        compass = (90.0 - self.x[PSI] * RAD2DEG) % 360.0
        pos_std = math.sqrt(0.5 * (self.P[self._i(PN, PN)] + self.P[self._i(PE, PE)]))
        return lat, lon, self.x[VN], self.x[VE], compass, pos_std


def _enu_to_ll(lat0, lon0, pN, pE):
    mpd_lon = MPD_LAT * math.cos(lat0 * DEG2RAD)
    return lat0 + pN / MPD_LAT, lon0 + pE / mpd_lon


def _ll_err_m(lat0, lat, lon, tlat, tlon):
    mpd_lon = MPD_LAT * math.cos(lat0 * DEG2RAD)
    dN = (lat - tlat) * MPD_LAT
    dE = (lon - tlon) * mpd_lon
    return math.hypot(dN, dE)


def test_drive_east_then_dropout():
    random.seed(7)
    lat0, lon0 = 37.0, -122.0
    ekf = EKF()
    dt = 0.01
    gps_std = 3.0
    truth_pE = 0.0
    truth_vE = 0.0
    t = 0.0
    last_fix = None

    # 12 s: accelerate east at 1 m/s^2 for 2 s, then cruise at 2 m/s.
    n = int(12.0 / dt)
    for k in range(n):
        a = 1.0 if t < 2.0 else 0.0
        truth_vE += a * dt
        truth_pE += truth_vE * dt
        # IMU: forward = east (heading east), so accel_b = [a_forward, 0].
        ekf.predict(a, 0.0, 0.0, dt)
        if k % 100 == 0:  # 1 Hz GPS
            tlat, tlon = _enu_to_ll(lat0, lon0, 0.0, truth_pE)
            glat = tlat + random.gauss(0, gps_std) / MPD_LAT
            glon = tlon + random.gauss(0, gps_std) / (MPD_LAT * math.cos(lat0 * DEG2RAD))
            ekf.update_gps_pos(glat, glon, gps_std)
            if truth_vE > 0.1:
                ekf.update_gps_vel(truth_vE, math.radians(90.0))  # course east
            last_fix = (tlat, tlon)
        t += dt

    lat, lon, vn, ve, hdg, pos_std = ekf.get_fix()
    tlat, tlon = last_fix
    err = _ll_err_m(lat0, lat, lon, tlat, tlon)
    assert err < 5.0, f"converged position error {err:.2f} m too high"
    assert abs(ve - 2.0) < 0.6, f"east velocity {ve:.2f} != ~2"
    assert abs(vn) < 0.6, f"north velocity {vn:.2f} should be ~0"
    assert abs(((hdg - 90.0 + 180) % 360) - 180) < 12.0, f"heading {hdg:.1f} != ~90 (east)"
    assert ekf.converged and pos_std < CONVERGE_STD_M, f"not converged (std {pos_std:.2f})"

    # --- GPS dropout: dead-reckon 5 s at constant 2 m/s east, no GPS ---
    drift_start_pE = truth_pE
    fix0 = ekf.get_fix()
    for k in range(int(5.0 / dt)):
        truth_pE += truth_vE * dt
        ekf.predict(0.0, 0.0, 0.0, dt)
    lat, lon, vn, ve, hdg, pos_std = ekf.get_fix()
    tlat, tlon = _enu_to_ll(lat0, lon0, 0.0, truth_pE)
    drift_err = _ll_err_m(lat0, lat, lon, tlat, tlon)
    # With a good velocity estimate and zero true accel, DR error stays small;
    # mostly the leftover velocity error * 5 s. Bound generously.
    assert drift_err < 12.0, f"dropout drift {drift_err:.2f} m too high"
    moved = truth_pE - drift_start_pE
    assert moved > 8.0, "sanity: truth should have moved during dropout"


def test_static_converges_and_zupt():
    random.seed(3)
    lat0, lon0 = 47.6, -122.3
    ekf = EKF()
    dt = 0.01
    gps_std = 2.5
    for k in range(int(8.0 / dt)):
        ekf.predict(0.0, 0.0, 0.0, dt)
        if k % 100 == 0:
            glat = lat0 + random.gauss(0, gps_std) / MPD_LAT
            glon = lon0 + random.gauss(0, gps_std) / (MPD_LAT * math.cos(lat0 * DEG2RAD))
            ekf.update_gps_pos(glat, glon, gps_std)
            ekf.update_zupt()
    lat, lon, vn, ve, hdg, pos_std = ekf.get_fix()
    err = _ll_err_m(lat0, lat, lon, lat0, lon0)
    assert err < 3.0, f"static position error {err:.2f} m"
    assert abs(vn) < 0.3 and abs(ve) < 0.3, f"ZUPT failed: v=({vn:.2f},{ve:.2f})"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
