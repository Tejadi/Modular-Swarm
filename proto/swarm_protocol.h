/*
 * Swarm overlay protocol — shared wire definition.
 *
 * Single source of truth for the messages exchanged between reconfigurable
 * swarm modules (nRF firmware), the per-agent Jetson companion, and the
 * command-station olympus_link host service.
 *
 * The same compact little-endian layout is used on the RF mesh (as the CoAP
 * payload) and over the USB-CDC serial link (wrapped in COBS + CRC16, see
 * swarm_proto.py / serial_link.c). The Python mirror lives in
 * proto/swarm_proto.py and MUST be kept in lockstep with this file.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_PROTOCOL_H__
#define SWARM_PROTOCOL_H__

#include <stdint.h>

/* CoAP transport (unchanged from the Nordic sample). */
#define SWARM_COAP_PORT 5683

/* Realm-local all-nodes multicast used for announce / discovery (ff03::1). */
#define SWARM_MCAST_ALL_NODES "ff03::1"

/* CoAP resource URIs (the upgraded swarm overlay). The legacy "light" and
 * "provisioning" resources are kept by the firmware for back-compat. */
#define SWARM_URI_HELLO     "swm/hello"     /* multicast descriptor / heartbeat */
#define SWARM_URI_TELEMETRY "swm/tlm"       /* position + sensors + status      */
#define SWARM_URI_NEIGHBORS "swm/nbr"       /* observed link table              */
#define SWARM_URI_RANGE     "swm/rng"       /* two-way RTT ranging              */
#define SWARM_URI_ROUTE     "swm/rte"       /* downlink route assignment        */
#define SWARM_URI_CMD       "swm/cmd"       /* downlink command                 */

/* Framing constants. */
#define SWARM_MAGIC   0x53u   /* 'S' */
#define SWARM_VERSION 0x01u
#define SWARM_HDR_LEN 16u     /* magic+ver+type+flags + eui64(8) + seq(4) */
#define SWARM_EUI64_LEN 8u
#define SWARM_MAX_FRAME 256u  /* fits a single 802.15.4 fragmented CoAP payload */

/* Message types (header.msg_type). */
enum swarm_msg_type {
	SWARM_MSG_HELLO      = 0x01,
	SWARM_MSG_TELEMETRY  = 0x02,
	SWARM_MSG_NEIGHBORS  = 0x03,
	SWARM_MSG_RANGE_REQ  = 0x04,
	SWARM_MSG_RANGE_RESP = 0x05,
	SWARM_MSG_ROUTE      = 0x10, /* downlink */
	SWARM_MSG_CMD        = 0x11, /* downlink */
};

/* Header flags bitfield. */
enum swarm_flags {
	SWARM_FLAG_GATEWAY = 1u << 0, /* sender is the command-station gateway */
	SWARM_FLAG_RELAYED = 1u << 1, /* gateway forwarded this from the mesh   */
};

/* Role bitfield (who the module is to the swarm). A pure consumer sets only
 * CONSUMER; a contributor sets PROVIDER (and usually RELAY). */
enum swarm_role {
	SWARM_ROLE_PROVIDER = 1u << 0, /* contributes sensor data to the swarm */
	SWARM_ROLE_CONSUMER = 1u << 1, /* uses swarm information                */
	SWARM_ROLE_RELAY    = 1u << 2, /* forwards traffic for neighbors        */
};

/* Mount state — the reconfigurable bit. */
enum swarm_mount {
	SWARM_MOUNT_STANDALONE = 0,
	SWARM_MOUNT_VEHICLE    = 1,
};

/* Which sensors the modular stack currently has populated (HELLO bitmap). */
enum swarm_sensor_bit {
	SWARM_SENS_GPS         = 1u << 0,
	SWARM_SENS_IMU         = 1u << 1,
	SWARM_SENS_MAG         = 1u << 2,
	SWARM_SENS_BARO        = 1u << 3,
	SWARM_SENS_TEMP        = 1u << 4,
	SWARM_SENS_HUMIDITY    = 1u << 5,
	SWARM_SENS_RANGEFINDER = 1u << 6,
	SWARM_SENS_CAMERA      = 1u << 7,
};

/* How the position carried in TELEMETRY was derived. */
enum swarm_pos_source {
	SWARM_POS_NONE   = 0,
	SWARM_POS_GPS    = 1,
	SWARM_POS_RANGED = 2, /* multilaterated from neighbor ranges          */
	SWARM_POS_IMU    = 3, /* IMU dead-reckoning only                      */
	SWARM_POS_FUSED  = 4, /* fusion of the above                          */
};

/* Status mirrors Olympus DroneStatus ordering so the dashboard maps it 1:1. */
enum swarm_status {
	SWARM_ST_IDLE = 0,
	SWARM_ST_SCANNING = 1,
	SWARM_ST_TRANSITING = 2,
	SWARM_ST_EXECUTING = 3,
	SWARM_ST_RETURNING = 4,
	SWARM_ST_CHARGING = 5,
	SWARM_ST_EMERGENCY = 6,
	SWARM_ST_OFFLINE = 7,
};

/* Per-reading telemetry channels (TLV channel id, value is always f32 LE). */
enum swarm_channel {
	SWARM_CH_TEMP       = 0x01,
	SWARM_CH_HUMIDITY   = 0x02,
	SWARM_CH_PRESSURE   = 0x03,
	SWARM_CH_ACCEL_X    = 0x10,
	SWARM_CH_ACCEL_Y    = 0x11,
	SWARM_CH_ACCEL_Z    = 0x12,
	SWARM_CH_GYRO_X     = 0x13,
	SWARM_CH_GYRO_Y     = 0x14,
	SWARM_CH_GYRO_Z     = 0x15,
	SWARM_CH_RANGEFINDER = 0x20,
	SWARM_CH_BATTERY_V  = 0x30,
};

/* Downlink command opcodes (CMD body). */
enum swarm_cmd_op {
	SWARM_CMD_NOOP        = 0,
	SWARM_CMD_SET_ROLE    = 1, /* param: role byte                        */
	SWARM_CMD_IDENTIFY    = 2, /* blink LED to locate the module          */
	SWARM_CMD_SET_RATE    = 3, /* param: telemetry period in deciseconds  */
	SWARM_CMD_REBOOT      = 4,
	SWARM_CMD_LIGHT       = 5, /* param: legacy light command byte        */
	SWARM_CMD_SET_MOUNT   = 6, /* param: mount byte + attached_to string  */
};

/* A sentinel EUI64 (all 0xFF) means "no parent / unassigned" in ROUTE. */
#define SWARM_EUI64_NONE { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF }

/*
 * Wire header. Packed so the layout is identical on every target. All
 * multi-byte fields are little-endian. body_len is NOT in the header — on RF
 * the CoAP layer carries the length; on serial the COBS frame bounds it.
 */
struct __attribute__((packed)) swarm_hdr {
	uint8_t  magic;     /* SWARM_MAGIC                */
	uint8_t  version;   /* SWARM_VERSION              */
	uint8_t  msg_type;  /* enum swarm_msg_type        */
	uint8_t  flags;     /* enum swarm_flags           */
	uint8_t  eui64[SWARM_EUI64_LEN];
	uint32_t seq;       /* monotonically increasing per source */
};

/* CRC16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over the raw frame, appended
 * little-endian inside the COBS wrapper on the serial link. */
static inline uint16_t swarm_crc16(const uint8_t *data, uint32_t len)
{
	uint16_t crc = 0xFFFFu;

	for (uint32_t i = 0; i < len; i++) {
		crc ^= (uint16_t)data[i] << 8;
		for (int b = 0; b < 8; b++) {
			if (crc & 0x8000u) {
				crc = (uint16_t)((crc << 1) ^ 0x1021u);
			} else {
				crc = (uint16_t)(crc << 1);
			}
		}
	}
	return crc;
}

/* Helpers shared by firmware encoders (little-endian writers). */
static inline uint32_t swarm_put_u16(uint8_t *p, uint16_t v)
{
	p[0] = (uint8_t)(v & 0xFF);
	p[1] = (uint8_t)(v >> 8);
	return 2;
}

static inline uint32_t swarm_put_u32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)(v & 0xFF);
	p[1] = (uint8_t)((v >> 8) & 0xFF);
	p[2] = (uint8_t)((v >> 16) & 0xFF);
	p[3] = (uint8_t)((v >> 24) & 0xFF);
	return 4;
}

static inline uint32_t swarm_put_i32(uint8_t *p, int32_t v)
{
	return swarm_put_u32(p, (uint32_t)v);
}

/* Fill a header in-place; returns SWARM_HDR_LEN. */
static inline uint32_t swarm_hdr_write(uint8_t *buf, uint8_t msg_type,
				       uint8_t flags, const uint8_t eui64[8],
				       uint32_t seq)
{
	buf[0] = SWARM_MAGIC;
	buf[1] = SWARM_VERSION;
	buf[2] = msg_type;
	buf[3] = flags;
	for (uint32_t i = 0; i < SWARM_EUI64_LEN; i++) {
		buf[4 + i] = eui64[i];
	}
	swarm_put_u32(&buf[12], seq);
	return SWARM_HDR_LEN;
}

#endif /* SWARM_PROTOCOL_H__ */
