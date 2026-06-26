/*
 * Internal context shared across the swarm_node firmware modules.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_NODE_H__
#define SWARM_NODE_H__

#include <stdint.h>
#include <stdbool.h>
#include <openthread/instance.h>
#include <openthread/ip6.h>

#include <swarm_protocol.h>

/* Periodic emission cadence (ms). Telemetry is rate-adapted at runtime. */
#define SWARM_HELLO_PERIOD_MS 3000
#define SWARM_TLM_PERIOD_MS   1000
#define SWARM_NBR_PERIOD_MS   2000

/* Live node state. Populated at boot from the factory EUI64 and devicetree,
 * then mutated by route/cmd messages and the serial link. */
struct swarm_node {
	otInstance *ot;
	uint8_t  eui[SWARM_EUI64_LEN];
	uint8_t  role;          /* enum swarm_role bitfield      */
	uint8_t  mount;         /* enum swarm_mount              */
	uint16_t sensors;       /* enum swarm_sensor_bit bitmap  */
	char     attached_to[32];
	uint8_t  attached_len;
	char     name[24];
	uint8_t  name_len;
	uint32_t seq;
	uint16_t tlm_period_ms; /* current telemetry period      */
	bool     connected;     /* attached to the Thread mesh   */

	/* Route assigned by the command station. */
	bool        have_parent;
	otIp6Address parent;
	uint8_t      parent_eui[SWARM_EUI64_LEN];
};

extern struct swarm_node g_node;

/* Next monotonically increasing sequence number for an outgoing message. */
static inline uint32_t swarm_next_seq(void)
{
	return ++g_node.seq;
}

#endif /* SWARM_NODE_H__ */
