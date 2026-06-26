/*
 * Modular sensor layer.
 *
 * The reconfigurable module may ship with any subset of {GPS, IMU, baro, ...}.
 * At boot we probe devicetree for each and set the corresponding sensor bit
 * only when its device is actually present and ready, so the HELLO descriptor
 * the command station receives reflects the real stack. Sensors that are absent
 * simply contribute nothing; the module still announces, relays, and (for
 * non-GPS builds) gets localized by the station from neighbor ranging.
 *
 * Devicetree hooks use chosen nodes so a board overlay wires the actual parts:
 *   chosen { swarm,imu = &lsm6dsl; swarm,gps-uart = &uart1; };
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

LOG_MODULE_REGISTER(swarm_sensors, CONFIG_SWARM_NODE_LOG_LEVEL);

#define LOCATE_LED DK_LED2

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

/* --- live sensor state --- */

static uint16_t present_mask;
static uint8_t battery_pct = 100;

/* Last valid GPS fix (degrees * 1e7) and validity. */
static struct {
	bool valid;
	int32_t lat_e7;
	int32_t lon_e7;
	int32_t alt_cm;
	uint16_t heading_cdeg;
} gps;

/* IMU dead-reckoning: integrate planar acceleration into a local displacement
 * the command station fuses with ranging. Coarse on purpose — it just keeps a
 * GPS-denied module from going dark between range fixes. */
static struct {
	float vx, vy;       /* m/s   */
	float dx, dy;       /* m, displacement since last reset */
	int64_t last_ms;
} dr;

/* --- GPS: minimal NMEA ($GxGGA) parsing over UART, interrupt-driven --- */

#define NMEA_MAX 96
static char nmea[NMEA_MAX];
static uint8_t nmea_len;

static int32_t nmea_deg_to_e7(const char *field, char hemi)
{
	/* ddmm.mmmm -> decimal degrees * 1e7. */
	double v = atof(field);
	int deg = (int)(v / 100.0);
	double minutes = v - deg * 100.0;
	double dec = deg + minutes / 60.0;
	if (hemi == 'S' || hemi == 'W') {
		dec = -dec;
	}
	return (int32_t)(dec * 1e7);
}

static void parse_gga(char *line)
{
	/* $GxGGA,time,lat,N,lon,E,fix,sats,hdop,alt,M,... */
	char *fields[12] = {0};
	int n = 0;
	char *p = line;

	while (n < 12 && p) {
		fields[n++] = p;
		p = strchr(p, ',');
		if (p) {
			*p++ = '\0';
		}
	}
	if (n < 10 || fields[6] == NULL || fields[6][0] == '0') {
		return; /* no fix */
	}
	gps.lat_e7 = nmea_deg_to_e7(fields[2], fields[3][0]);
	gps.lon_e7 = nmea_deg_to_e7(fields[4], fields[5][0]);
	gps.alt_cm = (int32_t)(atof(fields[9]) * 100.0);
	gps.valid = true;
}

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
				if (strncmp(&nmea[3], "GGA", 3) == 0) {
					parse_gga(nmea);
				}
			}
			nmea_len = 0;
		} else if (nmea_len < NMEA_MAX - 1) {
			nmea[nmea_len++] = c;
		}
	}
}

/* --- public API --- */

uint16_t swarm_sensors_init(void)
{
	present_mask = 0;
	dr.last_ms = k_uptime_get();

	if (imu_dev && device_is_ready(imu_dev)) {
		present_mask |= SWARM_SENS_IMU;
		LOG_INF("IMU present: %s", imu_dev->name);
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
	return present_mask;
}

static void integrate_imu(void)
{
	struct sensor_value accel[3];
	int64_t now = k_uptime_get();
	float dt = (now - dr.last_ms) / 1000.0f;

	dr.last_ms = now;
	if (!imu_dev || !device_is_ready(imu_dev) || dt <= 0.0f || dt > 1.0f) {
		return;
	}
	if (sensor_sample_fetch(imu_dev) < 0 ||
	    sensor_channel_get(imu_dev, SENSOR_CHAN_ACCEL_XYZ, accel) < 0) {
		return;
	}
	float ax = sensor_value_to_float(&accel[0]);
	float ay = sensor_value_to_float(&accel[1]);
	/* Naive strapdown: integrate horizontal accel, leak velocity to bound drift. */
	dr.vx = 0.9f * (dr.vx + ax * dt);
	dr.vy = 0.9f * (dr.vy + ay * dt);
	dr.dx += dr.vx * dt;
	dr.dy += dr.vy * dt;
}

void swarm_sensors_read(struct swarm_tlm_snapshot *out)
{
	memset(out, 0, sizeof(*out));
	out->status = SWARM_ST_SCANNING;
	out->battery_pct = battery_pct;

	if (present_mask & SWARM_SENS_IMU) {
		integrate_imu();
	}

	if ((present_mask & SWARM_SENS_GPS) && gps.valid) {
		out->pos_source = SWARM_POS_GPS;
		out->lat_e7 = gps.lat_e7;
		out->lon_e7 = gps.lon_e7;
		out->alt_cm = gps.alt_cm;
		out->heading_cdeg = gps.heading_cdeg;
		out->pos_quality = 220;
	} else if (present_mask & SWARM_SENS_IMU) {
		/* No GPS: hand the station our dead-reckoned displacement as a low
		 * quality IMU estimate. It refines this with neighbor ranging. */
		out->pos_source = SWARM_POS_IMU;
		out->lat_e7 = 0; /* station seeds absolute frame via ranging */
		out->lon_e7 = 0;
		out->alt_cm = 0;
		out->pos_quality = 60;
		out->n_readings = 0;
		out->readings[out->n_readings].channel = SWARM_CH_ACCEL_X;
		out->readings[out->n_readings++].value = dr.dx;
		out->readings[out->n_readings].channel = SWARM_CH_ACCEL_Y;
		out->readings[out->n_readings++].value = dr.dy;
	} else {
		out->pos_source = SWARM_POS_NONE;
	}

	/* Barometer reading, when present, as an extra telemetry channel. */
	if ((present_mask & SWARM_SENS_BARO) && baro_dev &&
	    device_is_ready(baro_dev) && out->n_readings < SWARM_MAX_READINGS) {
		struct sensor_value press;
		if (sensor_sample_fetch(baro_dev) == 0 &&
		    sensor_channel_get(baro_dev, SENSOR_CHAN_PRESS, &press) == 0) {
			out->readings[out->n_readings].channel = SWARM_CH_PRESSURE;
			out->readings[out->n_readings++].value = sensor_value_to_float(&press);
		}
	}
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
