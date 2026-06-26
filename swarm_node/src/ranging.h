/*
 * Coarse ranging between modules. The nRF52840 802.15.4 radio cannot measure
 * true time-of-flight, so range is estimated two ways and the better is kept:
 *   - RSSI path-loss: cheap, always available from any received message.
 *   - RTT: a /swm/rng request/response round trip, when both ends are awake.
 * The command station turns these per-neighbor ranges into absolute positions
 * by multilateration against GPS anchors.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_RANGING_H__
#define SWARM_RANGING_H__

#include <stdint.h>
#include <openthread/ip6.h>

void swarm_ranging_init(void);

/* Fold a received message's RSSI into the RSSI-based range estimate for a peer. */
void swarm_ranging_on_message(const uint8_t eui[8], const otIp6Address *src,
			      int8_t rssi);

/* Handle an inbound RANGE_REQ/RANGE_RESP payload. */
void swarm_ranging_handle(const uint8_t *payload, uint16_t len,
			  const otIp6Address *src);

/* Best current range estimate to a peer, in centimeters (0 = unknown). */
uint16_t swarm_ranging_estimate_cm(const uint8_t eui[8]);

#endif /* SWARM_RANGING_H__ */
