/*
 * Coarse ranging between modules — see ranging.h.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <math.h>
#include <string.h>

#include <swarm_protocol.h>
#include "ranging.h"

LOG_MODULE_REGISTER(swarm_ranging, CONFIG_SWARM_NODE_LOG_LEVEL);

/* Log-distance path-loss model: rssi = tx_at_1m - 10*n*log10(d). Tuned loosely
 * for indoor/outdoor 2.4 GHz; the station treats these ranges as soft anyway. */
#define RSSI_AT_1M (-40.0)
#define PATH_LOSS_N 2.2

#define RANGE_TABLE_SIZE 32
struct range_entry {
	uint8_t eui[SWARM_EUI64_LEN];
	uint16_t rssi_range_cm;
	uint16_t rtt_range_cm;   /* 0 = none */
	int64_t updated_ms;
	bool valid;
};
static struct range_entry table[RANGE_TABLE_SIZE];

static struct range_entry *slot_for(const uint8_t eui[8], bool create)
{
	struct range_entry *free_slot = NULL;

	for (int i = 0; i < RANGE_TABLE_SIZE; i++) {
		if (table[i].valid && memcmp(table[i].eui, eui, SWARM_EUI64_LEN) == 0) {
			return &table[i];
		}
		if (!table[i].valid && free_slot == NULL) {
			free_slot = &table[i];
		}
	}
	if (create && free_slot) {
		memset(free_slot, 0, sizeof(*free_slot));
		memcpy(free_slot->eui, eui, SWARM_EUI64_LEN);
		free_slot->valid = true;
		return free_slot;
	}
	return NULL;
}

static uint16_t rssi_to_cm(int8_t rssi)
{
	double d = pow(10.0, (RSSI_AT_1M - (double)rssi) / (10.0 * PATH_LOSS_N));
	if (d < 0.1) {
		d = 0.1;
	}
	if (d > 600.0) {
		d = 600.0;
	}
	return (uint16_t)(d * 100.0);
}

void swarm_ranging_init(void)
{
	memset(table, 0, sizeof(table));
}

void swarm_ranging_on_message(const uint8_t eui[8], const otIp6Address *src,
			      int8_t rssi)
{
	ARG_UNUSED(src);
	struct range_entry *e = slot_for(eui, true);

	if (!e) {
		return;
	}
	uint16_t cm = rssi_to_cm(rssi);

	/* Exponential smoothing to tame RSSI noise. */
	if (e->rssi_range_cm == 0) {
		e->rssi_range_cm = cm;
	} else {
		e->rssi_range_cm = (uint16_t)((e->rssi_range_cm * 3 + cm) / 4);
	}
	e->updated_ms = k_uptime_get();
}

void swarm_ranging_handle(const uint8_t *payload, uint16_t len,
			  const otIp6Address *src)
{
	ARG_UNUSED(src);
	if (len < SWARM_HDR_LEN) {
		return;
	}
	uint8_t msg_type = payload[2];
	const uint8_t *body = &payload[SWARM_HDR_LEN];
	uint16_t body_len = len - SWARM_HDR_LEN;

	if (msg_type == SWARM_MSG_RANGE_RESP && body_len >= 20) {
		/* body: initiator(8) t1 t2 t3. Range = c * (rtt - proc)/2. With no
		 * hardware timestamping this is dominated by MCU/stack latency, so
		 * it is only used when it beats the RSSI estimate's plausibility. */
		const uint8_t *initiator = &body[0];
		uint32_t t1, t2, t3;
		memcpy(&t1, &body[8], 4);
		memcpy(&t2, &body[12], 4);
		memcpy(&t3, &body[16], 4);
		uint32_t now = k_cycle_get_32();
		/* round-trip minus responder processing, in microseconds */
		int64_t rtt_us = (int64_t)((now - t1)) - (int64_t)((t3 - t2));
		if (rtt_us <= 0) {
			return;
		}
		double d_m = 299.792458 * (rtt_us / 1e6) / 2.0; /* m, c in m/us */
		if (d_m < 0 || d_m > 600) {
			return;
		}
		struct range_entry *e = slot_for(initiator, true);
		if (e) {
			e->rtt_range_cm = (uint16_t)(d_m * 100.0);
			e->updated_ms = k_uptime_get();
		}
	}
	/* RANGE_REQ handling (sending a RESP) is driven by swarm_coap's sender; a
	 * full two-way exchange is left to the node's work queue. */
}

uint16_t swarm_ranging_estimate_cm(const uint8_t eui[8])
{
	struct range_entry *e = slot_for(eui, false);

	if (!e) {
		return 0;
	}
	if ((k_uptime_get() - e->updated_ms) > 30000) {
		return 0; /* stale */
	}
	/* Prefer a fresh RTT measurement; otherwise the smoothed RSSI range. */
	if (e->rtt_range_cm) {
		return e->rtt_range_cm;
	}
	return e->rssi_range_cm;
}
