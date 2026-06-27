/*
 * jetson_link.c — see jetson_link.h.
 *
 * One CDC-ACM instance (chosen swarm,jetson-link) is the upstream data link.
 * TX is interrupt-driven through a ring buffer so a stalled host never blocks
 * the rest of the firmware; RX reassembles newline-delimited JSON and looks for
 * the host's ack. A single link thread drives the connection state machine:
 *
 *   wait for DTR (host opens port)
 *     -> announce manifest, retry with timeout/backoff until ack or give up
 *        -> on ack: stream normalized sensor data until DTR drops
 *   on a fresh DTR re-assert (reconnect): re-announce from the top.
 *
 * The announce/stream paths are sensor-count-agnostic: they walk the sensor
 * registry, so adding a sensor never touches this file.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/hwinfo.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/logging/log.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "protocol.h"
#include "sensor_registry.h"
#include "jetson_link.h"

LOG_MODULE_REGISTER(jetson_link, CONFIG_COAP_SERVER_LOG_LEVEL);

#if DT_HAS_CHOSEN(swarm_jetson_link)
#define HAVE_LINK 1
static const struct device *const link_dev = DEVICE_DT_GET(DT_CHOSEN(swarm_jetson_link));
#else
#define HAVE_LINK 0
#endif

#if HAVE_LINK

/* --- handshake / streaming tunables --- */
#define DTR_POLL_MS        100
#define HS_MAX_ATTEMPTS    6
#define HS_ACK_TIMEOUT_MS  1000
#define HS_BACKOFF_MIN_MS  500
#define HS_BACKOFF_MAX_MS  8000
#define STREAM_PERIOD_MS   1000

#define LINE_MAX      256
#define MANIFEST_MAX  512
#define VALUE_MAX     192
#define RX_MAX        256

RING_BUF_DECLARE(tx_rb, 2048);

static K_SEM_DEFINE(ack_sem, 0, 1);

/* --- TX --- */

static void emit_raw(const uint8_t *data, size_t len)
{
	if (ring_buf_put(&tx_rb, data, len) < len) {
		LOG_DBG("link TX overflow, dropping bytes");
	}
	uart_irq_tx_enable(link_dev);
}

static void emit_line(const char *json)
{
	const char term = PROTO_LINE_TERM;

	emit_raw((const uint8_t *)json, strlen(json));
	emit_raw((const uint8_t *)&term, 1);
}

/* --- RX: reassemble newline-delimited JSON, watch for the host ack --- */

static void rx_on_byte(uint8_t c)
{
	static char line[RX_MAX];
	static uint16_t len;

	if (c == '\r' || c == '\n') {
		if (len) {
			line[len] = '\0';
			/* Minimal ack detection — the contract guarantees the host
			 * replies with a {"type":"ack",...} object (see protocol.h). */
			if (strstr(line, "\"" PROTO_TYPE_ACK "\"")) {
				k_sem_give(&ack_sem);
			}
			len = 0;
		}
	} else if (len < RX_MAX - 1) {
		line[len++] = (char)c;
	} else {
		len = 0; /* overrun — resync on next newline */
	}
}

static void link_isr(const struct device *dev, void *user_data)
{
	uint8_t c;

	ARG_UNUSED(user_data);

	if (!uart_irq_update(dev)) {
		return;
	}
	while (uart_irq_rx_ready(dev)) {
		if (uart_fifo_read(dev, &c, 1) != 1) {
			break;
		}
		rx_on_byte(c);
	}
	while (uart_irq_tx_ready(dev)) {
		uint8_t b;

		if (ring_buf_get(&tx_rb, &b, 1) != 1) {
			uart_irq_tx_disable(dev);
			break;
		}
		uart_fifo_fill(dev, &b, 1);
	}
}

/* --- helpers --- */

static const char *node_id(void)
{
	static char id_str[20];

	if (id_str[0]) {
		return id_str;
	}
	uint8_t id[8];
	ssize_t n = hwinfo_get_device_id(id, sizeof(id));

	if (n <= 0) {
		strcpy(id_str, "node");
		return id_str;
	}
	for (ssize_t i = 0; i < n && i < 8; i++) {
		snprintf(id_str + i * 2, 3, "%02x", id[i]);
	}
	return id_str;
}

static bool link_dtr(void)
{
	uint32_t dtr = 0;

	if (uart_line_ctrl_get(link_dev, UART_LINE_CTRL_DTR, &dtr) != 0) {
		return false;
	}
	return dtr != 0;
}

/* Sleep up to ms, but bail early if the host disconnects. Returns true if DTR
 * stayed asserted the whole time. */
static bool dtr_sleep(uint32_t ms)
{
	while (ms) {
		uint32_t step = MIN(ms, (uint32_t)DTR_POLL_MS);

		k_msleep(step);
		ms -= step;
		if (!link_dtr()) {
			return false;
		}
	}
	return true;
}

static void emit_manifest(void)
{
	char line[MANIFEST_MAX];
	int n = sensor_registry_build_manifest(line, sizeof(line), node_id());

	if (n > 0) {
		emit_line(line);
	} else {
		LOG_ERR("manifest did not fit in %d bytes", (int)sizeof(line));
	}
}

static void emit_sensor_data(const struct sensor_descriptor *d)
{
	char value[VALUE_MAX];
	int vn = d->read_json(value, sizeof(value));

	if (vn < 0) {
		return; /* no valid sample yet */
	}
	char line[LINE_MAX];
	int n = snprintf(line, sizeof(line),
			 "{\"type\":\"%s\",\"schema\":%d,\"id\":\"%s\","
			 "\"ts_ms\":%lld,\"value\":%s}",
			 PROTO_TYPE_DATA, PROTO_SCHEMA_VERSION, d->id,
			 (long long)k_uptime_get(), value);

	if (n > 0 && n < (int)sizeof(line)) {
		emit_line(line);
	}
}

/* Announce + wait for ack with timeout/backoff. Returns true once acked. */
static bool handshake(void)
{
	uint32_t backoff = HS_BACKOFF_MIN_MS;

	for (int attempt = 1; attempt <= HS_MAX_ATTEMPTS; attempt++) {
		if (!link_dtr()) {
			return false; /* host went away mid-handshake */
		}
		k_sem_reset(&ack_sem);
		emit_manifest();

		if (k_sem_take(&ack_sem, K_MSEC(HS_ACK_TIMEOUT_MS)) == 0) {
			LOG_INF("manifest acked by host (attempt %d)", attempt);
			return true;
		}
		LOG_WRN("no ack (attempt %d/%d), backoff %u ms",
			attempt, HS_MAX_ATTEMPTS, backoff);
		if (!dtr_sleep(backoff)) {
			return false;
		}
		backoff = MIN(backoff * 2, (uint32_t)HS_BACKOFF_MAX_MS);
	}
	LOG_ERR("handshake gave up after %d attempts", HS_MAX_ATTEMPTS);
	return false;
}

static void stream_until_disconnect(void)
{
	LOG_INF("streaming %d sensor(s) to host", sensor_registry_count());
	while (link_dtr()) {
		int count = sensor_registry_count();

		for (int i = 0; i < count; i++) {
			const struct sensor_descriptor *d = sensor_registry_get(i);

			if (d) {
				emit_sensor_data(d);
			}
		}
		if (!dtr_sleep(STREAM_PERIOD_MS)) {
			break;
		}
	}
	LOG_INF("host disconnected (DTR dropped)");
}

static void link_thread_fn(void *a, void *b, void *c)
{
	bool prev_dtr = false;

	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	for (;;) {
		bool dtr = link_dtr();

		/* Fresh DTR assert == host opened (or reopened) the port. */
		if (dtr && !prev_dtr) {
			LOG_INF("host opened port — announcing capabilities");
			if (handshake()) {
				stream_until_disconnect();
			}
			dtr = link_dtr(); /* re-sample after the session */
		}
		prev_dtr = dtr;
		k_msleep(DTR_POLL_MS);
	}
}

/* 4 KB: the data path formats doubles (%.7f lat/lon) via picolibc, which is
 * stack-hungry on top of the line/value buffers — 2 KB could overflow on the
 * first streamed sample (right after ACK). */
K_THREAD_STACK_DEFINE(link_thread_stack, 4096);
static struct k_thread link_thread;

int jetson_link_init(void)
{
	if (!device_is_ready(link_dev)) {
		LOG_ERR("Jetson link %s not ready", link_dev->name);
		return -ENODEV;
	}

	/* USB and this CDC ACM instance are brought up automatically by the
	 * board's device_next serial-backend init (CDC_ACM_SERIAL_*_AT_BOOT),
	 * so we only attach our IRQ handler and drive the link state machine. */
	uart_irq_callback_user_data_set(link_dev, link_isr, NULL);
	uart_irq_rx_enable(link_dev);

	k_thread_create(&link_thread, link_thread_stack,
			K_THREAD_STACK_SIZEOF(link_thread_stack), link_thread_fn,
			NULL, NULL, NULL, K_PRIO_PREEMPT(7), 0, K_NO_WAIT);
	k_thread_name_set(&link_thread, "jetson_link");

	LOG_INF("Jetson link up on %s (node %s)", link_dev->name, node_id());
	return 0;
}

#else /* !HAVE_LINK */

int jetson_link_init(void)
{
	LOG_INF("no Jetson data port configured for this board");
	return -ENODEV;
}

#endif /* HAVE_LINK */
