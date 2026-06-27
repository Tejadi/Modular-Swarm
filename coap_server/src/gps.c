/*
 * gps.c — see gps.h.
 *
 * NMEA arrives on the chosen swarm,gps-uart. The RX ISR only reassembles raw
 * lines on CR/LF and hands them to a small parser thread via a message queue,
 * so no parsing (or float math) runs in interrupt context. The parser validates
 * the NMEA checksum, decodes RMC + GGA in a minmea style, and commits a
 * normalized fix under a spinlock. read_json() serializes that fix for the
 * sensor registry — raw NMEA never leaves this file.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/logging/log.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "gps.h"
#include "sensor_registry.h"

LOG_MODULE_REGISTER(gps, CONFIG_COAP_SERVER_LOG_LEVEL);

#if DT_HAS_CHOSEN(swarm_gps_uart)
#define HAVE_GPS 1
static const struct device *const gps_uart = DEVICE_DT_GET(DT_CHOSEN(swarm_gps_uart));
#else
#define HAVE_GPS 0
#endif

#if HAVE_GPS

#define NMEA_MAX 96

struct nmea_line {
	char buf[NMEA_MAX];
};

/* ISR -> parser thread hand-off. Depth 4 absorbs a full 1 Hz sentence burst. */
K_MSGQ_DEFINE(gps_msgq, sizeof(struct nmea_line), 4, 4);

/* Latest committed fix, guarded by a spinlock (parser writes, readers copy). */
static struct k_spinlock fix_lock;
static struct gps_fix fix;

/* --- RX ISR: reassemble raw lines only --- */

static void gps_isr(const struct device *dev, void *user_data)
{
	static struct nmea_line cur;
	static uint8_t len;
	uint8_t c;

	ARG_UNUSED(user_data);

	if (!uart_irq_update(dev) || !uart_irq_rx_ready(dev)) {
		return;
	}
	while (uart_fifo_read(dev, &c, 1) == 1) {
		if (c == '\r' || c == '\n') {
			if (len > 6) {
				cur.buf[len] = '\0';
				(void)k_msgq_put(&gps_msgq, &cur, K_NO_WAIT);
			}
			len = 0;
		} else if (len < NMEA_MAX - 1) {
			cur.buf[len++] = (char)c;
		} else {
			len = 0; /* overrun — resync on next delimiter */
		}
	}
}

/* --- minmea-style parsing (parser thread context) --- */

static int split_fields(char *s, char **f, int max)
{
	int n = 0;

	f[n++] = s;
	for (char *p = s; *p && n < max; p++) {
		if (*p == ',') {
			*p = '\0';
			f[n++] = p + 1;
		}
	}
	return n;
}

/* ddmm.mmmm + hemisphere -> decimal degrees. */
static double nmea_coord(const char *val, const char *hemi)
{
	if (!val[0]) {
		return 0.0;
	}
	double v = atof(val);
	int deg = (int)(v / 100.0);
	double minutes = v - deg * 100.0;
	double dec = deg + minutes / 60.0;

	if (hemi[0] == 'S' || hemi[0] == 'W') {
		dec = -dec;
	}
	return dec;
}

static void nmea_time(const char *val, struct gps_fix *out)
{
	/* hhmmss.sss */
	if (strlen(val) < 6) {
		return;
	}
	out->hours   = (val[0] - '0') * 10 + (val[1] - '0');
	out->minutes = (val[2] - '0') * 10 + (val[3] - '0');
	out->seconds = (val[4] - '0') * 10 + (val[5] - '0');
	out->millis  = (val[6] == '.') ? (uint16_t)(atof(val + 6) * 1000.0) : 0;
}

/* $xxGGA,time,lat,N,lon,E,fixq,sats,hdop,alt,M,... */
static void parse_gga(char *s)
{
	char *f[16];
	int n = split_fields(s, f, 16);

	if (n < 10) {
		return;
	}
	int q = f[6][0] ? atoi(f[6]) : 0;
	int sats = f[7][0] ? atoi(f[7]) : 0;

	/* Log only on lock acquire/lose so RTT shows fix state without 1 Hz spam. */
	static int last_q = -1;
	if (q != last_q) {
		if (q > 0) {
			LOG_INF("GPS fix acquired: quality=%d sats=%d", q, sats);
		} else {
			LOG_INF("GPS fix lost (searching)");
		}
		last_q = q;
	}

	K_SPINLOCK(&fix_lock) {
		fix.fix_quality = (uint8_t)q;
		fix.satellites = (uint8_t)sats;
		fix.hdop = f[8][0] ? atof(f[8]) : 0.0;
		nmea_time(f[1], &fix);
		if (q > 0) {
			fix.latitude = nmea_coord(f[2], f[3]);
			fix.longitude = nmea_coord(f[4], f[5]);
			fix.altitude_m = f[9][0] ? atof(f[9]) : 0.0;
			fix.valid = true;
		} else {
			fix.valid = false;
		}
	}
}

/* $xxRMC,time,status,lat,N,lon,E,speed,course,date,... */
static void parse_rmc(char *s)
{
	char *f[14];
	int n = split_fields(s, f, 14);

	if (n < 8) {
		return;
	}
	bool active = (f[2][0] == 'A');

	K_SPINLOCK(&fix_lock) {
		nmea_time(f[1], &fix);
		if (active) {
			fix.latitude = nmea_coord(f[3], f[4]);
			fix.longitude = nmea_coord(f[5], f[6]);
			fix.valid = true;
		} else if (fix.fix_quality == 0) {
			/* No GGA fix either -> position is not trustworthy. */
			fix.valid = false;
		}
	}
}

static void parse_nmea(char *s)
{
	/* s is "$ttSSS,...*HH" without CR/LF. */
	if (s[0] != '$') {
		return;
	}
	char *star = strchr(s, '*');
	if (!star) {
		return;
	}
	*star = '\0';

	uint8_t want = (uint8_t)strtol(star + 1, NULL, 16);
	uint8_t have = 0;
	for (char *p = s + 1; *p; p++) {
		have ^= (uint8_t)*p;
	}
	if (have != want) {
		LOG_DBG("NMEA checksum mismatch");
		return;
	}

	const char *sentence = s + 3; /* skip '$' + 2-char talker id */
	if (!strncmp(sentence, "GGA", 3)) {
		parse_gga(s);
	} else if (!strncmp(sentence, "RMC", 3)) {
		parse_rmc(s);
	}
}

static void gps_thread_fn(void *a, void *b, void *c)
{
	struct nmea_line line;

	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	for (;;) {
		if (k_msgq_get(&gps_msgq, &line, K_FOREVER) == 0) {
			parse_nmea(line.buf);
		}
	}
}

/* 2 KB: NMEA parsing uses atof()/strtod() (picolibc double conv is stack-heavy). */
K_THREAD_STACK_DEFINE(gps_thread_stack, 2048);
static struct k_thread gps_thread;

/* --- PMTK configuration helpers --- */

static void gps_send_pmtk(const char *body)
{
	/* body is the payload between '$' and '*', e.g. "PMTK314,...". */
	uint8_t cs = 0;
	for (const char *p = body; *p; p++) {
		cs ^= (uint8_t)*p;
	}
	char line[80];
	int n = snprintf(line, sizeof(line), "$%s*%02X\r\n", body, cs);

	for (int i = 0; i < n && i < (int)sizeof(line); i++) {
		uart_poll_out(gps_uart, line[i]);
	}
}

/* --- sensor registry hook --- */

static int gps_read_json(char *buf, size_t cap)
{
	struct gps_fix f;

	/* Always report the current state so the link is never silent: until a
	 * lock is acquired this streams "fix":0 (lat/lon/etc. are 0). The
	 * consumer trusts the position only when "fix" > 0. */
	gps_get_fix(&f);
	return snprintf(buf, cap,
			"{\"fix\":%u,\"sats\":%u,\"lat\":%.7f,\"lon\":%.7f,"
			"\"alt_m\":%.1f,\"hdop\":%.1f,\"utc\":\"%02u%02u%02u.%03u\"}",
			f.fix_quality, f.satellites, f.latitude, f.longitude,
			f.altitude_m, f.hdop, f.hours, f.minutes, f.seconds,
			f.millis);
}

static const struct sensor_descriptor gps_desc = {
	.id = "gps0",
	.type = "gps",
	.units = "deg,m",
	.rate_hz = IS_ENABLED(CONFIG_APP_GPS_HIGH_RATE) ? 5 : 1,
	.transport = "uart",
	.read_json = gps_read_json,
};

bool gps_get_fix(struct gps_fix *out)
{
	bool valid;

	K_SPINLOCK(&fix_lock) {
		*out = fix;
		valid = fix.valid;
	}
	return valid;
}

int gps_init(void)
{
	if (!device_is_ready(gps_uart)) {
		LOG_ERR("GPS UART %s not ready", gps_uart->name);
		return -ENODEV;
	}

	/* (a) Restrict NMEA output to RMC + GGA only (PMTK314 fields:
	 *     GLL,RMC,VTG,GGA,GSA,GSV,... -> enable index 2 (RMC) and 4 (GGA)). */
	gps_send_pmtk("PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0");

	/* (b) Optionally raise to 57600 baud @ 5 Hz for a higher position rate. */
	if (IS_ENABLED(CONFIG_APP_GPS_HIGH_RATE)) {
		struct uart_config uc;

		gps_send_pmtk("PMTK251,57600"); /* tell the module to switch baud */
		k_msleep(150);
		if (uart_config_get(gps_uart, &uc) == 0) {
			uc.baudrate = 57600;
			if (uart_configure(gps_uart, &uc) != 0) {
				LOG_WRN("could not switch UART to 57600");
			}
		}
		gps_send_pmtk("PMTK220,200"); /* 200 ms fix interval = 5 Hz */
		LOG_INF("GPS reconfigured to 57600 baud @ 5 Hz");
	}

	uart_irq_callback_user_data_set(gps_uart, gps_isr, NULL);
	uart_irq_rx_enable(gps_uart);

	k_thread_create(&gps_thread, gps_thread_stack,
			K_THREAD_STACK_SIZEOF(gps_thread_stack), gps_thread_fn,
			NULL, NULL, NULL, K_PRIO_PREEMPT(7), 0, K_NO_WAIT);
	k_thread_name_set(&gps_thread, "gps");

	sensor_registry_register(&gps_desc);
	LOG_INF("GPS up on %s", gps_uart->name);
	return 0;
}

#else /* !HAVE_GPS */

int gps_init(void)
{
	LOG_INF("no GPS UART configured for this board");
	return -ENODEV;
}

bool gps_get_fix(struct gps_fix *out)
{
	ARG_UNUSED(out);
	return false;
}

#endif /* HAVE_GPS */
