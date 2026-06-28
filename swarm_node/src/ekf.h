/*
 * ekf.h — loosely-coupled GPS+IMU extended Kalman filter for the swarm module.
 *
 * Planar (2D) core, 3D-ready. State is a local ENU frame anchored at the first
 * GPS fix, using the SAME meters-per-degree constants as the host
 * (olympus_link/localization.py _to_xy/_to_ll), so firmware, host, and Jetson
 * all agree on one frame.
 *
 *   x = [pN, pE, vN, vE, psi]
 *       pN,pE  position  (m, north / east)
 *       vN,vE  velocity  (m/s)
 *       psi    yaw        (rad, math convention: CCW from the East axis)
 *
 * IMU accel (body x=forward, y=left, gravity-compensated) and yaw rate drive the
 * prediction; GPS position and GPS course/speed are the measurements. GPS course
 * makes heading observable without a magnetometer. Altitude rides outside the
 * filter for now (straight from GPS/baro); ekf 3D adds pD/vD behind EKF_3D.
 *
 * All single-precision for the Cortex-M4F FPU.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_EKF_H__
#define SWARM_EKF_H__

#include <stdbool.h>
#include <stdint.h>

#define EKF_NX 5
enum { EKF_PN = 0, EKF_PE = 1, EKF_VN = 2, EKF_VE = 3, EKF_PSI = 4 };

struct ekf_state {
	float   x[EKF_NX];
	float   P[EKF_NX * EKF_NX];   /* covariance, row-major */
	double  lat0, lon0;           /* ENU anchor (deg), set on first GPS fix */
	float   mpd_lon;              /* meters per degree of longitude at lat0  */
	bool    anchored;
	bool    provisional;          /* anchor is a placeholder (no GPS yet)     */
	bool    converged;            /* position 1-sigma below threshold        */
	int64_t last_ms;             /* last predict time, managed by the caller */
	/* tuning (process-noise PSDs) */
	float   q_accel;              /* (m/s^2)^2 per second                     */
	float   q_gyro;               /* (rad/s)^2 per second                     */
};

/* Initialise to an un-anchored state with default tuning. */
void ekf_init(struct ekf_state *s);

/* Force the ENU anchor (also done lazily by the first ekf_update_gps_pos). */
void ekf_set_anchor(struct ekf_state *s, double lat, double lon);

/* Anchor at a PLACEHOLDER origin (no GPS yet) so the node still reports a
 * position and dead-reckons from the IMU. The first real ekf_update_gps_pos
 * re-anchors at the true location. Position uncertainty is left large. */
void ekf_set_anchor_provisional(struct ekf_state *s, double lat, double lon);

/* Prediction: body horizontal accel (m/s^2), yaw rate (rad/s), dt (s). */
void ekf_predict(struct ekf_state *s, const float accel_b[2], float gyro_z, float dt);

/* GPS position update (deg, 1-sigma std in m). Auto-anchors on first call. */
void ekf_update_gps_pos(struct ekf_state *s, double lat, double lon, float std_m);

/* GPS velocity/course update (speed m/s, course rad = compass CW from North). */
void ekf_update_gps_vel(struct ekf_state *s, float speed_mps, float course_rad);

/* Absolute-heading update from a magnetometer-fused IMU (e.g. BNO055 NDOF).
 * heading is compass radians (CW from North); std is the 1-sigma in radians.
 * Makes heading observable even at a standstill — the MPU-6050 path cannot. */
void ekf_update_heading(struct ekf_state *s, float heading_compass_rad, float std_rad);

/* Zero-velocity update — call when the module is known to be stationary. */
void ekf_update_zupt(struct ekf_state *s);

/* Read out the fused fix: geodetic position, ENU velocity, compass heading
 * (deg, 0..360), and 1-sigma stds. Any out pointer may be NULL. */
void ekf_get_fix(const struct ekf_state *s, double *lat, double *lon,
		 float *vn, float *ve, float *heading_deg,
		 float *pos_std_m, float *hdg_std_deg);

static inline bool ekf_is_anchored(const struct ekf_state *s) { return s->anchored; }

#endif /* SWARM_EKF_H__ */
