/*
 * ekf.c — loosely-coupled GPS+IMU EKF (planar core, 3D-ready). See ekf.h.
 *
 * Covariance is propagated with generic 5x5 matrix products (two ~125-mul
 * passes per predict — trivial for the M4F at <=200 Hz, and far less bug-prone
 * than a hand-expanded sparse form). Measurement updates are specialised to the
 * "H selects one or two states" case, which every measurement here fits, so the
 * gain reduces to a closed-form 1x1 / 2x2 solve with no general inverse.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <math.h>
#include <string.h>

#include "ekf.h"

#define MPD_LAT        111320.0f      /* meters per degree of latitude */
#define DEG2RAD        0.017453292f
#define RAD2DEG        57.29578f
#define PI_F           3.14159265f
#define TWO_PI_F       6.28318531f

/* Default tuning. */
#define Q_ACCEL_DEF    1.0f           /* (m/s^2)^2/s */
#define Q_GYRO_DEF     0.05f          /* (rad/s)^2/s */
#define R_GPS_VEL      0.25f          /* (m/s)^2, GPS velocity meas var */
#define R_GPS_HDG_DEG2 100.0f         /* deg^2, heading-from-course meas var */
#define ZUPT_R         0.01f          /* (m/s)^2, zero-velocity meas var */
#define GPS_VEL_HDG_MIN 0.7f          /* m/s; below this course is meaningless */
#define P_DIAG_MIN     1e-6f
#define CONVERGE_STD_M 10.0f

#define IDX(r, c) ((r) * EKF_NX + (c))

/* --- small helpers --- */

static float wrap_pi(float a)
{
	while (a > PI_F) {
		a -= TWO_PI_F;
	}
	while (a < -PI_F) {
		a += TWO_PI_F;
	}
	return a;
}

/* Re-symmetrise P and floor its diagonal to keep it positive in single precision. */
static void p_condition(struct ekf_state *s)
{
	float *P = s->P;

	for (int i = 0; i < EKF_NX; i++) {
		for (int j = i + 1; j < EKF_NX; j++) {
			float avg = 0.5f * (P[IDX(i, j)] + P[IDX(j, i)]);
			P[IDX(i, j)] = avg;
			P[IDX(j, i)] = avg;
		}
		if (P[IDX(i, i)] < P_DIAG_MIN) {
			P[IDX(i, i)] = P_DIAG_MIN;
		}
	}
}

/* o = a * b (5x5). o must not alias a or b. */
static void mat5_mul(float *o, const float *a, const float *b)
{
	for (int i = 0; i < EKF_NX; i++) {
		for (int j = 0; j < EKF_NX; j++) {
			float sum = 0.0f;
			for (int k = 0; k < EKF_NX; k++) {
				sum += a[IDX(i, k)] * b[IDX(k, j)];
			}
			o[IDX(i, j)] = sum;
		}
	}
}

/* o = a * b^T (5x5). o must not alias a or b. */
static void mat5_mul_abt(float *o, const float *a, const float *b)
{
	for (int i = 0; i < EKF_NX; i++) {
		for (int j = 0; j < EKF_NX; j++) {
			float sum = 0.0f;
			for (int k = 0; k < EKF_NX; k++) {
				sum += a[IDX(i, k)] * b[IDX(j, k)];
			}
			o[IDX(i, j)] = sum;
		}
	}
}

/* EKF update where H selects a single state component `idx`. */
static void update1(struct ekf_state *s, int idx, float z, float r, bool is_angle)
{
	float *P = s->P;
	float y = z - s->x[idx];

	if (is_angle) {
		y = wrap_pi(y);
	}
	float S = P[IDX(idx, idx)] + r;
	if (S < 1e-9f) {
		return;
	}
	float K[EKF_NX];
	for (int k = 0; k < EKF_NX; k++) {
		K[k] = P[IDX(k, idx)] / S;
	}
	for (int k = 0; k < EKF_NX; k++) {
		s->x[k] += K[k] * y;
	}
	float hp[EKF_NX];
	for (int c = 0; c < EKF_NX; c++) {
		hp[c] = P[IDX(idx, c)];
	}
	for (int rr = 0; rr < EKF_NX; rr++) {
		for (int c = 0; c < EKF_NX; c++) {
			P[IDX(rr, c)] -= K[rr] * hp[c];
		}
	}
	if (is_angle) {
		s->x[EKF_PSI] = wrap_pi(s->x[EKF_PSI]);
	}
	p_condition(s);
}

/* EKF update where H selects two state components (i, j). */
static void update2(struct ekf_state *s, int i, int j,
		    float z0, float z1, float r0, float r1)
{
	float *P = s->P;
	float y0 = z0 - s->x[i];
	float y1 = z1 - s->x[j];

	/* S = H P H^T + R (2x2). */
	float s00 = P[IDX(i, i)] + r0;
	float s01 = P[IDX(i, j)];
	float s10 = P[IDX(j, i)];
	float s11 = P[IDX(j, j)] + r1;
	float det = s00 * s11 - s01 * s10;
	if (fabsf(det) < 1e-9f) {
		return;
	}
	float invd = 1.0f / det;
	float si00 =  s11 * invd, si01 = -s01 * invd;
	float si10 = -s10 * invd, si11 =  s00 * invd;

	/* K = P H^T S^-1 (5x2); P H^T = columns i,j of P. */
	float K[EKF_NX][2];
	for (int k = 0; k < EKF_NX; k++) {
		float a = P[IDX(k, i)], b = P[IDX(k, j)];
		K[k][0] = a * si00 + b * si10;
		K[k][1] = a * si01 + b * si11;
	}
	for (int k = 0; k < EKF_NX; k++) {
		s->x[k] += K[k][0] * y0 + K[k][1] * y1;
	}
	/* P -= K (H P); H P = rows i,j of P. Snapshot them first. */
	float hpi[EKF_NX], hpj[EKF_NX];
	for (int c = 0; c < EKF_NX; c++) {
		hpi[c] = P[IDX(i, c)];
		hpj[c] = P[IDX(j, c)];
	}
	for (int rr = 0; rr < EKF_NX; rr++) {
		for (int c = 0; c < EKF_NX; c++) {
			P[IDX(rr, c)] -= K[rr][0] * hpi[c] + K[rr][1] * hpj[c];
		}
	}
	p_condition(s);
}

/* --- public API --- */

void ekf_init(struct ekf_state *s)
{
	memset(s, 0, sizeof(*s));
	float *P = s->P;
	/* Large initial position/heading uncertainty, modest velocity. */
	P[IDX(EKF_PN, EKF_PN)] = 1.0e6f;
	P[IDX(EKF_PE, EKF_PE)] = 1.0e6f;
	P[IDX(EKF_VN, EKF_VN)] = 25.0f;
	P[IDX(EKF_VE, EKF_VE)] = 25.0f;
	P[IDX(EKF_PSI, EKF_PSI)] = PI_F * PI_F;
	s->q_accel = Q_ACCEL_DEF;
	s->q_gyro = Q_GYRO_DEF;
	s->anchored = false;
	s->converged = false;
}

void ekf_set_anchor(struct ekf_state *s, double lat, double lon)
{
	s->lat0 = lat;
	s->lon0 = lon;
	s->mpd_lon = MPD_LAT * cosf((float)lat * DEG2RAD);
	s->x[EKF_PN] = 0.0f;
	s->x[EKF_PE] = 0.0f;
	/* Position now known to ~GPS accuracy at the anchor. */
	s->P[IDX(EKF_PN, EKF_PN)] = 25.0f;
	s->P[IDX(EKF_PE, EKF_PE)] = 25.0f;
	s->anchored = true;
	s->provisional = false;
}

void ekf_set_anchor_provisional(struct ekf_state *s, double lat, double lon)
{
	ekf_set_anchor(s, lat, lon);
	s->provisional = true;
	/* It's a placeholder, not a measurement: keep position uncertainty large
	 * (~1 km) so the reported quality is honestly "rough" and the first real
	 * GPS fix dominates. Velocity/heading carry on dead-reckoning from the IMU. */
	s->P[IDX(EKF_PN, EKF_PN)] = 1.0e6f;
	s->P[IDX(EKF_PE, EKF_PE)] = 1.0e6f;
}

void ekf_predict(struct ekf_state *s, const float accel_b[2], float gyro_z, float dt)
{
	if (dt <= 0.0f || dt > 0.5f) {
		return;
	}
	float psi = wrap_pi(s->x[EKF_PSI] + gyro_z * dt);
	float c = cosf(psi), sn = sinf(psi);
	float ax = accel_b[0], ay = accel_b[1];
	/* Rotate body accel into ENU (psi = CCW from East). */
	float aE = ax * c - ay * sn;
	float aN = ax * sn + ay * c;

	s->x[EKF_PN] += s->x[EKF_VN] * dt + 0.5f * aN * dt * dt;
	s->x[EKF_PE] += s->x[EKF_VE] * dt + 0.5f * aE * dt * dt;
	s->x[EKF_VN] += aN * dt;
	s->x[EKF_VE] += aE * dt;
	s->x[EKF_PSI] = psi;

	/* Jacobian F = I + couplings. d(aN)/dpsi = aE, d(aE)/dpsi = -aN. */
	float F[EKF_NX * EKF_NX];
	memset(F, 0, sizeof(F));
	for (int i = 0; i < EKF_NX; i++) {
		F[IDX(i, i)] = 1.0f;
	}
	F[IDX(EKF_PN, EKF_VN)] = dt;
	F[IDX(EKF_PE, EKF_VE)] = dt;
	F[IDX(EKF_PN, EKF_PSI)] = 0.5f * dt * dt * aE;
	F[IDX(EKF_PE, EKF_PSI)] = -0.5f * dt * dt * aN;
	F[IDX(EKF_VN, EKF_PSI)] = dt * aE;
	F[IDX(EKF_VE, EKF_PSI)] = -dt * aN;

	/* P = F P F^T + Q. */
	float FP[EKF_NX * EKF_NX];
	mat5_mul(FP, F, s->P);
	mat5_mul_abt(s->P, FP, F);

	/* Discrete white-noise acceleration model per axis + gyro on psi. */
	float qpp = s->q_accel * dt * dt * dt / 3.0f;
	float qpv = s->q_accel * dt * dt * 0.5f;
	float qvv = s->q_accel * dt;
	float *P = s->P;
	P[IDX(EKF_PN, EKF_PN)] += qpp;
	P[IDX(EKF_PN, EKF_VN)] += qpv;
	P[IDX(EKF_VN, EKF_PN)] += qpv;
	P[IDX(EKF_VN, EKF_VN)] += qvv;
	P[IDX(EKF_PE, EKF_PE)] += qpp;
	P[IDX(EKF_PE, EKF_VE)] += qpv;
	P[IDX(EKF_VE, EKF_PE)] += qpv;
	P[IDX(EKF_VE, EKF_VE)] += qvv;
	P[IDX(EKF_PSI, EKF_PSI)] += s->q_gyro * dt;

	p_condition(s);
}

void ekf_update_gps_pos(struct ekf_state *s, double lat, double lon, float std_m)
{
	if (!s->anchored) {
		ekf_set_anchor(s, lat, lon);
		return;
	}
	if (s->provisional) {
		/* First real GPS fix after a placeholder anchor: snap the origin to
		 * the true location (resets position to 0, drops provisional). The
		 * IMU-derived velocity/heading carry over. */
		ekf_set_anchor(s, lat, lon);
		return;
	}
	float pN = (float)((lat - s->lat0) * MPD_LAT);
	float pE = (float)((lon - s->lon0) * (double)s->mpd_lon);
	float r = std_m * std_m;
	update2(s, EKF_PN, EKF_PE, pN, pE, r, r);

	float pos_var = 0.5f * (s->P[IDX(EKF_PN, EKF_PN)] + s->P[IDX(EKF_PE, EKF_PE)]);
	s->converged = (pos_var < CONVERGE_STD_M * CONVERGE_STD_M);
}

void ekf_update_gps_vel(struct ekf_state *s, float speed_mps, float course_rad)
{
	/* course_rad is compass (CW from North): N=cos, E=sin. */
	float vN = speed_mps * cosf(course_rad);
	float vE = speed_mps * sinf(course_rad);
	update2(s, EKF_VN, EKF_VE, vN, vE, R_GPS_VEL, R_GPS_VEL);

	if (speed_mps > GPS_VEL_HDG_MIN) {
		float psi_meas = atan2f(vN, vE); /* math yaw, CCW from East */
		update1(s, EKF_PSI, psi_meas, R_GPS_HDG_DEG2 * DEG2RAD * DEG2RAD, true);
	}
}

void ekf_update_heading(struct ekf_state *s, float heading_compass_rad, float std_rad)
{
	/* compass (CW from North) -> math yaw psi (CCW from East): psi = pi/2 - h. */
	float psi_meas = wrap_pi(PI_F * 0.5f - heading_compass_rad);
	update1(s, EKF_PSI, psi_meas, std_rad * std_rad, true);
}

void ekf_update_zupt(struct ekf_state *s)
{
	update2(s, EKF_VN, EKF_VE, 0.0f, 0.0f, ZUPT_R, ZUPT_R);
}

void ekf_get_fix(const struct ekf_state *s, double *lat, double *lon,
		 float *vn, float *ve, float *heading_deg,
		 float *pos_std_m, float *hdg_std_deg)
{
	if (lat) {
		*lat = s->lat0 + (double)s->x[EKF_PN] / MPD_LAT;
	}
	if (lon) {
		*lon = s->lon0 + (double)s->x[EKF_PE] / (double)s->mpd_lon;
	}
	if (vn) {
		*vn = s->x[EKF_VN];
	}
	if (ve) {
		*ve = s->x[EKF_VE];
	}
	if (heading_deg) {
		/* Math yaw (CCW from East) -> compass (CW from North). */
		float compass = 90.0f - s->x[EKF_PSI] * RAD2DEG;
		compass = fmodf(compass, 360.0f);
		if (compass < 0.0f) {
			compass += 360.0f;
		}
		*heading_deg = compass;
	}
	if (pos_std_m) {
		float v = 0.5f * (s->P[IDX(EKF_PN, EKF_PN)] + s->P[IDX(EKF_PE, EKF_PE)]);
		*pos_std_m = sqrtf(v);
	}
	if (hdg_std_deg) {
		*hdg_std_deg = sqrtf(s->P[IDX(EKF_PSI, EKF_PSI)]) * RAD2DEG;
	}
}
