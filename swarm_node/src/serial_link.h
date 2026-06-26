/*
 * USB-CDC serial link to the companion computer (Jetson on an agent, or the
 * Olympus host on the gateway). Frames are COBS + CRC16 wrapped swarm payloads,
 * matching proto/swarm_proto.py on the host side.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_SERIAL_LINK_H__
#define SWARM_SERIAL_LINK_H__

#include <stdint.h>

/* Bring up the CDC-ACM data port. Returns 0 on success. */
int serial_link_init(void);

/* Frame and transmit a raw protocol payload (header + body) to the host. */
void serial_link_send(const uint8_t *payload, uint16_t len);

#endif /* SWARM_SERIAL_LINK_H__ */
