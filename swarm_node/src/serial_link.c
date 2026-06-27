/*
 * USB-CDC serial link to the companion computer.
 *
 * Wraps swarm payloads in COBS + CRC16 (CCITT-FALSE) framing, byte-for-byte
 * compatible with proto/swarm_proto.py. TX is interrupt driven through a ring
 * buffer so a stalled host never blocks the mesh. RX reassembles frames on the
 * 0x00 delimiter, verifies the CRC, and dispatches: the gateway forwards route/
 * cmd downlink into the mesh; a plain node applies them locally.
 *
 * The data port is a dedicated CDC-ACM instance chosen as `swarm,serial`, kept
 * separate from the console/shell port. If no such node exists (e.g. a board
 * without USB), the link compiles to a no-op so the node still runs standalone.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/logging/log.h>
#include <string.h>

#include <swarm_protocol.h>
#include "serial_link.h"
#include "swarm_coap.h"

LOG_MODULE_REGISTER(swarm_serial, CONFIG_SWARM_NODE_LOG_LEVEL);

#if DT_HAS_CHOSEN(swarm_serial)
#define HAVE_SERIAL 1
static const struct device *const ser = DEVICE_DT_GET(DT_CHOSEN(swarm_serial));
#else
#define HAVE_SERIAL 0
#endif

#if HAVE_SERIAL

RING_BUF_DECLARE(tx_rb, 1024);

static uint8_t rx_frame[SWARM_MAX_FRAME * 2 + 4];
static uint16_t rx_len;

/* --- COBS decode (mirror of swarm_proto.cobs_decode) --- */

static int cobs_decode(const uint8_t *in, uint16_t in_len,
		       uint8_t *out, uint16_t out_cap)
{
	uint16_t i = 0, o = 0;

	while (i < in_len) {
		uint8_t code = in[i++];
		if (code == 0) {
			return -1;
		}
		for (uint8_t k = 1; k < code; k++) {
			if (i >= in_len || o >= out_cap) {
				return -1;
			}
			out[o++] = in[i++];
		}
		if (code < 0xFF && i < in_len) {
			if (o >= out_cap) {
				return -1;
			}
			out[o++] = 0;
		}
	}
	return o;
}

static void dispatch_frame(const uint8_t *frame, uint16_t flen)
{
	uint8_t decoded[SWARM_MAX_FRAME + 2];
	int dlen = cobs_decode(frame, flen, decoded, sizeof(decoded));

	if (dlen < (int)(SWARM_HDR_LEN + 2)) {
		return;
	}
	uint16_t payload_len = dlen - 2;
	uint16_t crc_rx = (uint16_t)decoded[payload_len] |
			  ((uint16_t)decoded[payload_len + 1] << 8);
	if (swarm_crc16(decoded, payload_len) != crc_rx) {
		LOG_DBG("serial CRC mismatch");
		return;
	}

#if defined(CONFIG_SWARM_GATEWAY)
	/* Gateway: downlink route/cmd from the Olympus host into the mesh. */
	swarm_coap_send_downlink(decoded, payload_len);
#else
	/* Node: apply a route/cmd (or sensor injection) from the Jetson. */
	swarm_handle_payload(decoded, payload_len, NULL);
#endif
}

static void serial_isr(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);
	uint8_t c;

	if (!uart_irq_update(dev)) {
		return;
	}

	while (uart_irq_rx_ready(dev)) {
		if (uart_fifo_read(dev, &c, 1) != 1) {
			break;
		}
		if (c == 0x00) {
			if (rx_len > 0) {
				dispatch_frame(rx_frame, rx_len);
			}
			rx_len = 0;
		} else if (rx_len < sizeof(rx_frame)) {
			rx_frame[rx_len++] = c;
		} else {
			rx_len = 0; /* overrun — resync on next delimiter */
		}
	}

	while (uart_irq_tx_ready(dev)) {
		uint8_t byte;
		if (ring_buf_get(&tx_rb, &byte, 1) != 1) {
			uart_irq_tx_disable(dev);
			break;
		}
		uart_fifo_fill(dev, &byte, 1);
	}
}

int serial_link_init(void)
{
	if (!device_is_ready(ser)) {
		LOG_WRN("serial data port not ready");
		return -1;
	}
	/* The modern USB device_next stack enables the CDC at boot (board config),
	 * so we don't call usb_enable() — just attach to the CDC-ACM UART. */
	uart_irq_callback_user_data_set(ser, serial_isr, NULL);
	uart_irq_rx_enable(ser);
	LOG_INF("serial data link up on %s", ser->name);
	return 0;
}

void serial_link_send(const uint8_t *payload, uint16_t len)
{
	uint8_t body[SWARM_MAX_FRAME + 2];
	uint8_t framed[SWARM_MAX_FRAME + 8];
	uint16_t code_idx, code, n = 0;

	if (len > SWARM_MAX_FRAME) {
		return;
	}
	/* body = payload || crc16_le */
	memcpy(body, payload, len);
	uint16_t crc = swarm_crc16(payload, len);
	body[len] = crc & 0xFF;
	body[len + 1] = crc >> 8;
	uint16_t body_len = len + 2;

	/* COBS encode (mirror of swarm_proto.cobs_encode). */
	code_idx = n;
	framed[n++] = 0;
	code = 1;
	for (uint16_t i = 0; i < body_len; i++) {
		if (body[i] == 0) {
			framed[code_idx] = code;
			code_idx = n;
			framed[n++] = 0;
			code = 1;
		} else {
			framed[n++] = body[i];
			if (++code == 0xFF) {
				framed[code_idx] = code;
				code_idx = n;
				framed[n++] = 0;
				code = 1;
			}
		}
	}
	framed[code_idx] = code;
	framed[n++] = 0x00; /* delimiter */

	if (ring_buf_put(&tx_rb, framed, n) < n) {
		LOG_DBG("serial TX overflow, dropping frame");
	}
	uart_irq_tx_enable(ser);
}

#else /* !HAVE_SERIAL */

int serial_link_init(void)
{
	LOG_INF("no serial data port configured (standalone build)");
	return 0;
}

void serial_link_send(const uint8_t *payload, uint16_t len)
{
	ARG_UNUSED(payload);
	ARG_UNUSED(len);
}

#endif /* HAVE_SERIAL */
