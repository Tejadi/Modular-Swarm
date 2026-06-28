/*
 * Modular sensor layer + GPS/IMU EKF driver.
 *
 * A dedicated sensor thread owns the EKF (ekf.c): it polls the IMU at ~100 Hz
 * for the prediction step and folds in GPS GGA position + RMC course/speed as
 * they arrive (the UART ISR only assembles NMEA lines and hands them over a
 * queue, so no float math runs in interrupt context). The latest fused fix is
 * cached under a spinlock; swarm_sensors_read() snapshots it for telemetry.
 *
 * Bus-agnostic: the IMU/baro come from devicetree chosen{} nodes via the Zephyr
 * sensor API, so an I2C or SPI part works with no code change. Absent sensors
 * leave their HELLO bit clear and the EKF simply runs with fewer measurements.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/sensor.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/logging/log.h>
#include <dk_buttons_and_leds.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include <swarm_protocol.h>
#include "sensors.h"
#include "ekf.h"
#include "bno055.h"
#include "ranging.h"

LOG_MODULE_REGISTER(swarm_sensors, CONFIG_SWARM_NODE_LOG_LEVEL);

#define LOCATE_LED DK_LED2

#define IMU_PERIOD_MS    10          /* 100 Hz EKF prediction */
#define BARO_PERIOD_MS   1000
#define GPS_STALE_MS     3000        /* GPS_USED flag drops after this */
#define ZUPT_STILL_MS    500         /* stationary this long -> ZUPT */
#define KNOTS_TO_MPS     0.514444f

/* BNO055 absolute-heading update (leader / any BNO055 node). Rate-limited so the
 * correlated samples don't make the filter over-confident, and gated on a
 * calibrated magnetometer. MPU-6050 nodes never take this path — they keep
 * heading from gyro integration + GPS course. */
#define HDG_PERIOD_MS     200
#define HDG_STD_RAD       0.08727f   /* ~5 deg 1-sigma */
#define BNO_MAG_CALIB_MIN 2          /* 0..3; trust heading at >= this */

/* No GPS lock? Anchor at a placeholder so the node still reports a position and
 * dead-reckons from the IMU; the first real GPS fix re-anchors at the truth.
 * Philadelphia City Hall. */
#define GPS_WAIT_MS      8000        /* give GPS this long before the placeholder */
#define PLACEHOLDER_LAT  39.9526
#define PLACEHOLDER_LON  (-75.1652)

/* Decentralized peer-range fusion — runs HERE on the nRF (EKF #1), so even a
 * Jetson-less module folds in peers. Each in-range peer (<=50 m) is a soft
 * range constraint that nudges the fix. */
#define PEER_PERIOD_MS   1000        /* fuse the current peer set this often */
#define PEER_USED_TTL_MS 2500        /* report PEER_USED this long after a fusion */
#define MAX_FUSE_PEERS   8

/* --- optional devices, resolved from chosen{} when present --- */

#if DT_HAS_CHOSEN(swarm_imu)
static const struct device *const imu_dev = DEVICE_DT_GET(DT_CHOSEN(swarm_imu));
#else
static const struct device *const imu_dev = NULL;
#endif

#if DT_HAS_CHOSEN(swarm_baro)
static const struct device *const baro_dev = DEVICE_DT_GET(DT_CHOSEN(swarm_baro));
#else
static const struct device *const baro_dev = NULL;
#endif

#if DT_HAS_CHOSEN(swarm_gps_uart)
static const struct device *const gps_uart = DEVICE_DT_GET(DT_CHOSEN(swarm_gps_uart));
#else
static const struct device *const gps_uart = NULL;
#endif

static uint16_t present_mask;
static uint8_t  battery_pct = 100;

/* Which IMU the runtime auto-detect settled on. The MPU-6050 rides the Zephyr
 * sensor API (its devicetree node bound); the BNO055 is read over direct I2C
 * (bno055.c). Both feed the EKF the same (body horizontal accel + yaw rate). */
enum imu_backend { IMU_NONE = 0, IMU_MPU6050, IMU_BNO055 };
static enum imu_backend imu_backend;

/* --- GPS NMEA line assembly (ISR -> queue -> sensor thread) --- */

#define NMEA_MAX 96
K_MSGQ_DEFINE(gps_lines, NMEA_MAX, 6, 4);
static char nmea[NMEA_MAX];
static uint8_t nmea_len;

/* --- the EKF and its derived outputs, all touched on the sensor thread --- */

static struct ekf_state ekf;
static int32_t last_alt_cm;          /* altitude rides outside the EKF (GPS GGA) */
static int64_t last_gps_ms;
static int64_t still_since_ms;        /* monotonic time the unit went stationary */
static int64_t last_hdg_ms;           /* last BNO055 absolute-heading update */
static int64_t last_peer_ms;          /* last peer-range fusion cycle */
static int64_t peer_used_ms;          /* last time a peer range was fused */

/* Cached fused fix + a Jetson-injected pose; read by swarm_sensors_read(). */
static struct k_spinlock fix_lock;
static struct {
	bool     valid;
	int32_t  lat_e7, lon_e7, alt_cm;
	uint16_t heading_cdeg;
	int16_t  vel_n_cms, vel_e_cms;
	uint16_t pos_std_cm, hdg_std_cd;
	uint8_t  ekf_flags;
	uint8_t  pos_quality;
} g_fix;

static struct {
	bool     valid;
	int64_t  ts_ms;
	int32_t  lat_e7, lon_e7, alt_cm;
	uint16_t heading_cdeg;
	int16_t  vel_n_cms, vel_e_cms;
	uint16_t pos_std_cm, hdg_std_cd;
	uint8_t  src_flags;
} g_inject;

static float baro_pa;
static bool  baro_valid;

/* --- GPS UART ISR: assemble NMEA lines, hand each to the sensor thread --- */

static void gps_uart_isr(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);
	uint8_t c;

	if (!uart_irq_update(dev) || !uart_irq_rx_ready(dev)) {
		return;
	}
	while (uart_fifo_read(dev, &c, 1) == 1) {
		if (c == '\n' || c == '\r') {
			if (nmea_len > 6) {
				nmea[nmea_len] = '\0';
				/* Best-effort: drop the line if the queue is full. */
				(void)k_msgq_put(&gps_lines, nmea, K_NO_WAIT);
			}
			nmea_len = 0;
		} else if (nmea_len < NMEA_MAX - 1) {
			nmea[nmea_len++] = c;
		}
	}
}

/* --- NMEA parsing (thread context) --- */

static bool nmea_checksum_ok(const char *line)
{
	/* line starts at '$'; checksum is XOR of bytes between '$' and '*'. */
	if (line[0] != '$') {
		return false;
	}
	uint8_t sum = 0;
	const char *p = line + 1;
	while (*p && *p != '*') {
		sum ^= (uint8_t)*p++;
	}
	if (*p != '*') {
		return false;
	}
	uint8_t want = (uint8_t)strtol(p + 1, NULL, 16);
	return sum == want;
}

static int split_fields(char *line, char *fields[], int max)
{
	int n = 0;
	char *p = line;

	while (n < max && p) {
		fields[n++] = p;
		p = strchr(p, ',');
		if (p) {
			*p++ = '\0';
		}
	}
	return n;
}

static double nmea_deg(const char *field, char hemi)
{
	/* ddmm.mmmm -> decimal degrees. */
	double v = atof(field);
	int deg = (int)(v / 100.0);
	double dec = deg + (v - deg * 100.0) / 60.0;
	if (hemi == 'S' || hemi == 'W') {
		dec = -dec;
	}
	return dec;
}

static void parse_gga(char *line)
{
	char *f[15];
	int n = split_fields(line, f, 15);

	if (n < 10 || f[6][0] == '0' || f[2][0] == '\0') {
		return; /* no fix */
	}
	double lat = nmea_deg(f[2], f[3][0]);
	double lon = nmea_deg(f[4], f[5][0]);
	last_alt_cm = (int32_t)(atof(f[9]) * 100.0);
	last_gps_ms = k_uptime_get();
	ekf_update_gps_pos(&ekf, lat, lon, 4.0f); /* ~4 m horizontal std */
}

static void parse_rmc(char *line)
{
	char *f[15];
	int n = split_fields(line, f, 15);

	if (n < 9 || f[2][0] != 'A' || f[3][0] == '\0') {
		return; /* status != active */
	}
	double lat = nmea_deg(f[3], f[4][0]);
	double lon = nmea_deg(f[5], f[6][0]);
	last_gps_ms = k_uptime_get();
	ekf_update_gps_pos(&ekf, lat, lon, 5.0f);

	float speed = atof(f[7]) * KNOTS_TO_MPS;
	if (f[8][0] != '\0') {
		float course = atof(f[8]) * 0.017453292f; /* deg -> rad */
		ekf_update_gps_vel(&ekf, speed, course);
	}
}

static void handle_nmea(char *line)
{
	if (!nmea_checksum_ok(line)) {
		return;
	}
	/* line[1..2] = talker id, line[3..5] = sentence type. */
	if (strncmp(&line[3], "GGA", 3) == 0) {
		parse_gga(line);
	} else if (strncmp(&line[3], "RMC", 3) == 0) {
		parse_rmc(line);
	}
}

/* --- IMU prediction step --- */

/* One IMU read, normalised for the EKF. Body x=forward, y=left (identity
 * mounting; calibrate a static R_sb later). has_abs_heading is set only by a
 * BNO055 with a calibrated magnetometer. */
struct imu_reading {
	float a_body[2];
	float gyro_z;
	bool  has_abs_heading;
	float heading_rad;     /* compass heading, CW from North */
};

static bool imu_read(struct imu_reading *r)
{
	r->a_body[0] = r->a_body[1] = r->gyro_z = 0.0f;
	r->has_abs_heading = false;
	r->heading_rad = 0.0f;

	if (imu_backend == IMU_MPU6050) {
		struct sensor_value accel[3], gyro[3];

		if (sensor_sample_fetch(imu_dev) == 0 &&
		    sensor_channel_get(imu_dev, SENSOR_CHAN_ACCEL_XYZ, accel) == 0 &&
		    sensor_channel_get(imu_dev, SENSOR_CHAN_GYRO_XYZ, gyro) == 0) {
			r->a_body[0] = sensor_value_to_float(&accel[0]);
			r->a_body[1] = sensor_value_to_float(&accel[1]);
			r->gyro_z = sensor_value_to_float(&gyro[2]);
			return true;
		}
	} else if (imu_backend == IMU_BNO055) {
		struct bno055_sample s;

		/* BNO055 NDOF gives gravity-free linear accel directly — exactly what
		 * the EKF expects, no level-mount assumption. And once its mag is
		 * calibrated, an absolute heading the MPU-6050 path cannot provide. */
		if (bno055_read(&s)) {
			r->a_body[0] = s.ax;
			r->a_body[1] = s.ay;
			r->gyro_z = s.gyro_z;
			if (s.heading_valid && s.mag_calib >= BNO_MAG_CALIB_MIN) {
				r->has_abs_heading = true;
				r->heading_rad = s.heading_rad;
			}
			return true;
		}
	}
	return false;
}

static void imu_step(float dt)
{
	struct imu_reading r;
	bool stationary = false;
	int64_t now = k_uptime_get();

	if (imu_backend != IMU_NONE && imu_read(&r)) {
		stationary = (hypotf(r.a_body[0], r.a_body[1]) < 0.3f) &&
			     (fabsf(r.gyro_z) < 0.05f);
	} else {
		memset(&r, 0, sizeof(r));
	}

	ekf_predict(&ekf, r.a_body, r.gyro_z, dt);

	/* BNO055 absolute-heading update (rate-limited). MPU nodes skip this and
	 * keep heading from gyro integration + GPS course. */
	if (r.has_abs_heading && (now - last_hdg_ms) > HDG_PERIOD_MS) {
		ekf_update_heading(&ekf, r.heading_rad, HDG_STD_RAD);
		last_hdg_ms = now;
	}

	/* Zero-velocity update after staying still a while (bounds parked drift). */
	if (stationary) {
		if (still_since_ms == 0) {
			still_since_ms = now;
		} else if (now - still_since_ms > ZUPT_STILL_MS) {
			ekf_update_zupt(&ekf);
		}
	} else {
		still_since_ms = 0;
	}
}

/* --- decentralized peer-range fusion (EKF #1, on the nRF) --- */

static void peer_fusion_step(void)
{
	struct swarm_peer_fix peers[MAX_FUSE_PEERS];
	int n;

	if (!ekf_is_anchored(&ekf)) {
		return; /* need our own ENU anchor before a peer range means anything */
	}
	n = swarm_ranging_get_peers(peers, MAX_FUSE_PEERS);
	for (int i = 0; i < n; i++) {
		float range_m = peers[i].range_cm / 100.0f;
		/* Coarse RSSI/RTT ranges -> treat as SOFT (std ~ half the range,
		 * floored) so a peer nudges rather than snaps the fix and peer-of-peer
		 * correlation can't dominate. */
		float std_m = range_m * 0.5f < 5.0f ? 5.0f : range_m * 0.5f;

		ekf_update_peer_range(&ekf, (double)peers[i].lat_e7 / 1e7,
				      (double)peers[i].lon_e7 / 1e7, range_m, std_m);
		peer_used_ms = k_uptime_get();
	}
}

/* --- publish the latest fused fix into the read cache --- */

static uint8_t quality_from_std(float pos_std_m)
{
	float q = 255.0f / (1.0f + pos_std_m / 5.0f);
	if (q > 255.0f) {
		q = 255.0f;
	}
	return (uint8_t)(q < 1.0f ? 1.0f : q);
}

static void publish_fix(void)
{
	double lat, lon;
	float vn, ve, hdg, pos_std, hdg_std;

	if (!ekf_is_anchored(&ekf)) {
		K_SPINLOCK(&fix_lock) {
			g_fix.valid = false;
		}
		return;
	}
	ekf_get_fix(&ekf, &lat, &lon, &vn, &ve, &hdg, &pos_std, &hdg_std);

	int64_t now = k_uptime_get();
	uint8_t flags = 0;
	if (imu_backend != IMU_NONE) {
		flags |= SWARM_EKF_IMU_USED;
	}
	if (present_mask & SWARM_SENS_GPS && (now - last_gps_ms) < GPS_STALE_MS) {
		flags |= SWARM_EKF_GPS_USED;
	}
	if ((now - peer_used_ms) < PEER_USED_TTL_MS) {
		flags |= SWARM_EKF_PEER_USED;
	}
	if (ekf.converged) {
		flags |= SWARM_EKF_CONVERGED;
	}

	K_SPINLOCK(&fix_lock) {
		g_fix.valid = true;
		g_fix.lat_e7 = (int32_t)(lat * 1e7);
		g_fix.lon_e7 = (int32_t)(lon * 1e7);
		g_fix.alt_cm = last_alt_cm;
		g_fix.heading_cdeg = (uint16_t)(fmodf(hdg, 360.0f) * 100.0f);
		g_fix.vel_n_cms = (int16_t)(vn * 100.0f);
		g_fix.vel_e_cms = (int16_t)(ve * 100.0f);
		g_fix.pos_std_cm = (uint16_t)(pos_std * 100.0f > 65535.0f ? 65535.0f
								       : pos_std * 100.0f);
		g_fix.hdg_std_cd = (uint16_t)(hdg_std * 100.0f > 65535.0f ? 65535.0f
								       : hdg_std * 100.0f);
		g_fix.ekf_flags = flags;
		g_fix.pos_quality = quality_from_std(pos_std);
	}
}

/* --- the sensor thread --- */

static void sensor_thread_fn(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	char line[NMEA_MAX];
	int64_t start = k_uptime_get();
	int64_t last_imu = start;
	int64_t last_baro = 0;
	bool placeholder_logged = false;

	while (1) {
		int64_t now = k_uptime_get();

		/* Drain any complete GPS lines. */
		while (k_msgq_get(&gps_lines, line, K_NO_WAIT) == 0) {
			handle_nmea(line);
		}

		/* No GPS lock after the grace window: anchor at the placeholder so the
		 * node still reports a position and dead-reckons from the IMU. The
		 * first real GPS fix (parse_gga/rmc -> ekf_update_gps_pos) re-anchors. */
		if (!ekf_is_anchored(&ekf) && (now - start) > GPS_WAIT_MS) {
			ekf_set_anchor_provisional(&ekf, PLACEHOLDER_LAT, PLACEHOLDER_LON);
			if (!placeholder_logged) {
				LOG_WRN("no GPS lock; placeholder anchor (Philadelphia), "
					"IMU dead-reckoning until a real fix");
				placeholder_logged = true;
			}
		}

		/* IMU prediction at the loop rate (also runs GPS-only as constant
		 * velocity when no IMU is present). */
		float dt = (now - last_imu) / 1000.0f;
		last_imu = now;
		imu_step(dt);

		/* Decentralized peer-range fusion, rate-limited (ranges are sporadic). */
		if (now - last_peer_ms > PEER_PERIOD_MS) {
			peer_fusion_step();
			last_peer_ms = now;
		}

		if ((present_mask & SWARM_SENS_BARO) && now - last_baro > BARO_PERIOD_MS) {
			struct sensor_value press;
			if (sensor_sample_fetch(baro_dev) == 0 &&
			    sensor_channel_get(baro_dev, SENSOR_CHAN_PRESS, &press) == 0) {
				baro_pa = sensor_value_to_float(&press) * 1000.0f; /* kPa->Pa */
				baro_valid = true;
			}
			last_baro = now;
		}

		publish_fix();
		k_sleep(K_MSEC(IMU_PERIOD_MS));
	}
}

K_THREAD_STACK_DEFINE(sensor_stack, 4096);
static struct k_thread sensor_thread;

/* --- public API --- */

uint16_t swarm_sensors_init(void)
{
	present_mask = 0;
	ekf_init(&ekf);

	/* Auto-detect the IMU: prefer a Zephyr-driver part (MPU-6050) whose DT node
	 * actually bound; otherwise probe a BNO055 directly on the IMU I2C bus.
	 * Either one sets SWARM_SENS_IMU; with neither, the EKF runs GPS-only. */
	if (imu_dev && device_is_ready(imu_dev)) {
		imu_backend = IMU_MPU6050;
		present_mask |= SWARM_SENS_IMU;
		LOG_INF("IMU: MPU-6050 (%s)", imu_dev->name);
	} else if (bno055_init()) {
		imu_backend = IMU_BNO055;
		present_mask |= SWARM_SENS_IMU;
		LOG_INF("IMU: BNO055 (direct I2C)");
	} else {
		imu_backend = IMU_NONE;
		LOG_WRN("no IMU detected — EKF runs GPS-only");
	}
	if (baro_dev && device_is_ready(baro_dev)) {
		present_mask |= SWARM_SENS_BARO;
	}
	if (gps_uart && device_is_ready(gps_uart)) {
		present_mask |= SWARM_SENS_GPS;
		uart_irq_callback_user_data_set(gps_uart, gps_uart_isr, NULL);
		uart_irq_rx_enable(gps_uart);
		LOG_INF("GPS UART present: %s", gps_uart->name);
	}
	LOG_INF("sensor bitmap = 0x%04x", present_mask);

	k_thread_create(&sensor_thread, sensor_stack, K_THREAD_STACK_SIZEOF(sensor_stack),
			sensor_thread_fn, NULL, NULL, NULL, 6, 0, K_NO_WAIT);
	k_thread_name_set(&sensor_thread, "swarm_sensors");

	return present_mask;
}

void sensors_inject_pose(int32_t lat_e7, int32_t lon_e7, int32_t alt_cm,
			 uint16_t heading_cdeg, int16_t vel_n_cms, int16_t vel_e_cms,
			 uint16_t pos_std_cm, uint16_t hdg_std_cd, uint8_t src_flags)
{
	K_SPINLOCK(&fix_lock) {
		g_inject.valid = true;
		g_inject.ts_ms = k_uptime_get();
		g_inject.lat_e7 = lat_e7;
		g_inject.lon_e7 = lon_e7;
		g_inject.alt_cm = alt_cm;
		g_inject.heading_cdeg = heading_cdeg;
		g_inject.vel_n_cms = vel_n_cms;
		g_inject.vel_e_cms = vel_e_cms;
		g_inject.pos_std_cm = pos_std_cm;
		g_inject.hdg_std_cd = hdg_std_cd;
		g_inject.src_flags = src_flags;
	}
}

/* Fill the position fields from the on-board EKF fix (call under fix_lock). */
static void out_from_fix(struct swarm_tlm_snapshot *out)
{
	out->pos_source = SWARM_POS_FUSED;
	out->lat_e7 = g_fix.lat_e7;
	out->lon_e7 = g_fix.lon_e7;
	out->alt_cm = g_fix.alt_cm;
	out->heading_cdeg = g_fix.heading_cdeg;
	out->has_kinematics = true;
	out->vel_n_cms = g_fix.vel_n_cms;
	out->vel_e_cms = g_fix.vel_e_cms;
	out->pos_std_cm = g_fix.pos_std_cm;
	out->hdg_std_cd = g_fix.hdg_std_cd;
	out->ekf_flags = g_fix.ekf_flags;
	out->pos_quality = g_fix.pos_quality;
}

/* Fill the position fields from the Jetson-injected pose (call under fix_lock). */
static void out_from_inject(struct swarm_tlm_snapshot *out)
{
	out->pos_source = SWARM_POS_FUSED;
	out->lat_e7 = g_inject.lat_e7;
	out->lon_e7 = g_inject.lon_e7;
	out->alt_cm = g_inject.alt_cm;
	out->heading_cdeg = g_inject.heading_cdeg;
	out->has_kinematics = true;
	out->vel_n_cms = g_inject.vel_n_cms;
	out->vel_e_cms = g_inject.vel_e_cms;
	out->pos_std_cm = g_inject.pos_std_cm;
	out->hdg_std_cd = g_inject.hdg_std_cd;
	out->ekf_flags = g_inject.src_flags | SWARM_EKF_VIO_USED;
	out->pos_quality = 240;
}

static void read_base(struct swarm_tlm_snapshot *out)
{
	memset(out, 0, sizeof(*out));
	out->status = SWARM_ST_SCANNING;
	out->battery_pct = battery_pct;
	out->pos_source = SWARM_POS_NONE;
}

static void read_baro(struct swarm_tlm_snapshot *out)
{
	if (baro_valid && out->n_readings < SWARM_MAX_READINGS) {
		out->readings[out->n_readings].channel = SWARM_CH_PRESSURE;
		out->readings[out->n_readings++].value = baro_pa;
	}
}

/* Mesh-facing fix: the Jetson's authoritative injected pose while fresh, else the
 * on-board EKF. This is what the swarm and command station see. */
void swarm_sensors_read(struct swarm_tlm_snapshot *out)
{
	read_base(out);
	int64_t now = k_uptime_get();

	K_SPINLOCK(&fix_lock) {
		if (g_inject.valid && (now - g_inject.ts_ms) < SWARM_POSE_FRESH_MS) {
			out_from_inject(out);
		} else if (g_fix.valid) {
			out_from_fix(out);
		}
	}
	read_baro(out);
}

/* Jetson-facing fix: ALWAYS the on-board EKF (never the injected pose), so the
 * Jetson fuses an independent nRF estimate instead of reading back its own
 * injection (which would form a feedback loop). */
void swarm_sensors_read_own(struct swarm_tlm_snapshot *out)
{
	read_base(out);
	K_SPINLOCK(&fix_lock) {
		if (g_fix.valid) {
			out_from_fix(out);
		}
	}
	read_baro(out);
}

uint8_t sensors_battery_pct(void)
{
	return battery_pct;
}

void sensors_identify(void)
{
	for (int i = 0; i < 6; i++) {
		dk_set_led(LOCATE_LED, i & 1);
		k_sleep(K_MSEC(120));
	}
	dk_set_led_off(LOCATE_LED);
}
