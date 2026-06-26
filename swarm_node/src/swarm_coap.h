/*
 * CoAP overlay for the reconfigurable swarm: resource registration, the
 * periodic announce/telemetry/neighbor senders, and the unicast downlink
 * (route/cmd) used by the gateway build.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_COAP_H__
#define SWARM_COAP_H__

#include <stdint.h>
#include <openthread/ip6.h>

/* Register the swarm CoAP resources and start the CoAP service. */
int swarm_coap_init(void);

/* Periodic senders (called from the main work queue on timer expiry). */
void swarm_coap_send_hello(void);
void swarm_coap_send_telemetry(void);
void swarm_coap_send_neighbors(void);

/* Dispatch a raw protocol payload (header + body). `src` is the sender's IPv6
 * address (NULL when the payload arrived over the serial link). Shared by the
 * CoAP handlers and the gateway's serial RX path. */
void swarm_handle_payload(const uint8_t *buf, uint16_t len,
			  const otIp6Address *src);

/* Gateway downlink: send a route/cmd payload to a specific module by EUI64,
 * resolving the destination from the learned EUI->IPv6 table. */
void swarm_coap_send_downlink(const uint8_t *payload, uint16_t len);

#endif /* SWARM_COAP_H__ */
