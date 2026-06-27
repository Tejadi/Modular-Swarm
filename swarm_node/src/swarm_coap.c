/*
 * CoAP overlay for the reconfigurable swarm.
 *
 * Built on the native OpenThread CoAP API (otCoap*) so a single instance both
 * serves resources and originates requests. Announce / telemetry / neighbor
 * messages are sent as non-confirmable PUTs to the realm-local all-nodes group
 * (ff03::1), which Thread floods mesh-wide, so the command-station gateway
 * hears every module regardless of hop count. Route / cmd are confirmable
 * unicast from the gateway down to a specific module.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/reboot.h>
#include <zephyr/sys/byteorder.h>
#include <string.h>

#include <openthread/coap.h>
#include <openthread/thread.h>
#include <openthread/message.h>
#include <openthread/link.h>

#include "swarm_node.h"
#include "swarm_coap.h"
#include "sensors.h"
#include "ranging.h"
#include "serial_link.h"

LOG_MODULE_REGISTER(swarm_coap, CONFIG_SWARM_NODE_LOG_LEVEL);

static otIp6Address mcast_all_nodes;

/* This node's standing header flags — the gateway tags its messages so the
 * command station can auto-discover which EUI is the gateway (tree root). */
static inline uint8_t self_flags(void)
{
	uint8_t f = IS_ENABLED(CONFIG_SWARM_GATEWAY) ? SWARM_FLAG_GATEWAY : 0;

	if (IS_ENABLED(CONFIG_SWARM_LEADER)) {
		f |= SWARM_FLAG_LEADER;
	}
	return f;
}

/* --- learned EUI64 -> IPv6 table (for gateway downlink + ranging) --- */

#define PEER_TABLE_SIZE 32
struct peer {
	uint8_t eui[SWARM_EUI64_LEN];
	otIp6Address ip;
	bool valid;
};
static struct peer peers[PEER_TABLE_SIZE];

static void peer_update(const uint8_t eui[8], const otIp6Address *ip)
{
	struct peer *free_slot = NULL;

	for (int i = 0; i < PEER_TABLE_SIZE; i++) {
		if (peers[i].valid && memcmp(peers[i].eui, eui, SWARM_EUI64_LEN) == 0) {
			peers[i].ip = *ip;
			return;
		}
		if (!peers[i].valid && free_slot == NULL) {
			free_slot = &peers[i];
		}
	}
	if (free_slot) {
		memcpy(free_slot->eui, eui, SWARM_EUI64_LEN);
		free_slot->ip = *ip;
		free_slot->valid = true;
	}
}

static bool peer_lookup(const uint8_t eui[8], otIp6Address *out)
{
	for (int i = 0; i < PEER_TABLE_SIZE; i++) {
		if (peers[i].valid && memcmp(peers[i].eui, eui, SWARM_EUI64_LEN) == 0) {
			*out = peers[i].ip;
			return true;
		}
	}
	return false;
}

/* --- low-level send --- */

static otError swarm_send(const char *uri, bool confirmable,
			  const otIp6Address *dest,
			  const uint8_t *payload, uint16_t len)
{
	otError error = OT_ERROR_NO_BUFS;
	otMessage *msg;
	otMessageInfo info;

	msg = otCoapNewMessage(g_node.ot, NULL);
	if (msg == NULL) {
		return error;
	}

	otCoapMessageInit(msg, confirmable ? OT_COAP_TYPE_CONFIRMABLE
					   : OT_COAP_TYPE_NON_CONFIRMABLE,
			  OT_COAP_CODE_PUT);
	error = otCoapMessageAppendUriPathOptions(msg, uri);
	if (error != OT_ERROR_NONE) {
		goto fail;
	}
	error = otCoapMessageSetPayloadMarker(msg);
	if (error != OT_ERROR_NONE) {
		goto fail;
	}
	error = otMessageAppend(msg, payload, len);
	if (error != OT_ERROR_NONE) {
		goto fail;
	}

	memset(&info, 0, sizeof(info));
	info.mPeerAddr = *dest;
	info.mPeerPort = SWARM_COAP_PORT;

	error = otCoapSendRequest(g_node.ot, msg, &info, NULL, NULL);
	if (error != OT_ERROR_NONE) {
		goto fail;
	}
	return OT_ERROR_NONE;

fail:
	otMessageFree(msg);
	return error;
}

/* --- periodic senders --- */

void swarm_coap_send_hello(void)
{
	uint8_t buf[SWARM_MAX_FRAME];
	uint32_t off;

	off = swarm_hdr_write(buf, SWARM_MSG_HELLO, self_flags(), g_node.eui, swarm_next_seq());
	buf[off++] = g_node.role;
	buf[off++] = g_node.mount;
	off += swarm_put_u16(&buf[off], g_node.sensors);
	off += swarm_put_u16(&buf[off], CONFIG_SWARM_FW_VERSION);
	buf[off++] = sensors_battery_pct();
	off += swarm_put_u32(&buf[off], (uint32_t)(k_uptime_get() / 1000));
	buf[off++] = g_node.name_len;
	memcpy(&buf[off], g_node.name, g_node.name_len);
	off += g_node.name_len;
	buf[off++] = g_node.attached_len;
	memcpy(&buf[off], g_node.attached_to, g_node.attached_len);
	off += g_node.attached_len;
	/* Capability/permission trailer — how others classify this node. */
	off += swarm_put_u16(&buf[off], g_node.capabilities);

	swarm_send(SWARM_URI_HELLO, false, &mcast_all_nodes, buf, off);
	/* Mirror to the companion computer (Jetson redundant IP path / gateway host). */
	serial_link_send(buf, off);
}

/* Serialize a telemetry snapshot into buf; returns the byte length. */
static uint32_t build_telemetry(uint8_t *buf, const struct swarm_tlm_snapshot *snap,
				uint32_t seq)
{
	uint32_t off = swarm_hdr_write(buf, SWARM_MSG_TELEMETRY, self_flags(),
				       g_node.eui, seq);
	buf[off++] = snap->status;
	buf[off++] = snap->pos_source;
	off += swarm_put_i32(&buf[off], snap->lat_e7);
	off += swarm_put_i32(&buf[off], snap->lon_e7);
	off += swarm_put_i32(&buf[off], snap->alt_cm);
	off += swarm_put_u16(&buf[off], snap->heading_cdeg);
	buf[off++] = snap->battery_pct;
	buf[off++] = snap->pos_quality;
	buf[off++] = snap->n_readings;
	for (int i = 0; i < snap->n_readings; i++) {
		buf[off++] = snap->readings[i].channel;
		memcpy(&buf[off], &snap->readings[i].value, sizeof(float));
		off += sizeof(float);
	}
	/* Fused-kinematics trailer (EKF velocity + uncertainty). */
	if (snap->has_kinematics) {
		off += swarm_put_u16(&buf[off], (uint16_t)snap->vel_n_cms);
		off += swarm_put_u16(&buf[off], (uint16_t)snap->vel_e_cms);
		off += swarm_put_u16(&buf[off], snap->pos_std_cm);
		off += swarm_put_u16(&buf[off], snap->hdg_std_cd);
		buf[off++] = snap->ekf_flags;
	}
	return off;
}

void swarm_coap_send_telemetry(void)
{
	uint8_t buf[SWARM_MAX_FRAME];
	struct swarm_tlm_snapshot snap;
	uint32_t seq = swarm_next_seq();
	uint32_t off;

	/* Mesh + command station: the authoritative fix (Jetson pose while fresh). */
	swarm_sensors_read(&snap);
	off = build_telemetry(buf, &snap, seq);
	swarm_send(SWARM_URI_TELEMETRY, false, &mcast_all_nodes, buf, off);

	/* Jetson (serial): the nRF's OWN EKF fix — an independent input for the
	 * Jetson EKF, never the pose the Jetson just injected. */
	swarm_sensors_read_own(&snap);
	off = build_telemetry(buf, &snap, seq);
	serial_link_send(buf, off);
}

void swarm_coap_send_neighbors(void)
{
	uint8_t buf[SWARM_MAX_FRAME];
	otNeighborInfoIterator it = OT_NEIGHBOR_INFO_ITERATOR_INIT;
	otNeighborInfo nbr;
	uint32_t off;
	uint8_t count = 0;
	uint32_t count_off;

	off = swarm_hdr_write(buf, SWARM_MSG_NEIGHBORS, self_flags(), g_node.eui, swarm_next_seq());
	count_off = off;
	buf[off++] = 0; /* patched with the real count below */

	/* OpenThread already maintains the link neighbor table — reuse it. */
	while (otThreadGetNextNeighborInfo(g_node.ot, &it, &nbr) == OT_ERROR_NONE) {
		if (off + 12 > sizeof(buf)) {
			break;
		}
		const uint8_t *eui = nbr.mExtAddress.m8;
		memcpy(&buf[off], eui, SWARM_EUI64_LEN);
		off += SWARM_EUI64_LEN;
		buf[off++] = (uint8_t)nbr.mAverageRssi;              /* i8 */
		off += swarm_put_u16(&buf[off], swarm_ranging_estimate_cm(eui));
		/* Map Thread link quality (0..3) onto the 0..255 field. */
		buf[off++] = (uint8_t)(nbr.mLinkQualityIn * 85);
		count++;
	}
	buf[count_off] = count;

	swarm_send(SWARM_URI_NEIGHBORS, false, &mcast_all_nodes, buf, off);
	/* Gateway: this is its own neighbor report (the tree roots) for olympus_link.
	 * Node: feeds the Jetson's redundant IP path. */
	serial_link_send(buf, off);
}

/* --- downlink (gateway -> module) --- */

void swarm_coap_send_downlink(const uint8_t *payload, uint16_t len)
{
	otIp6Address dest;
	const uint8_t *target_eui;
	const char *uri;
	uint8_t msg_type;

	if (len < SWARM_HDR_LEN) {
		return;
	}
	msg_type = payload[2];

	if (msg_type == SWARM_MSG_BROADCAST) {
		/* Fleet-wide multicast (leader override / command) — no unicast target. */
		swarm_send(SWARM_URI_BROADCAST, false, &mcast_all_nodes, payload, len);
		return;
	}

	target_eui = &payload[4]; /* header.eui = the module this is addressed to */

	if (!peer_lookup(target_eui, &dest)) {
		LOG_WRN("downlink: unknown target EUI, dropping");
		return;
	}

	uri = (msg_type == SWARM_MSG_ROUTE) ? SWARM_URI_ROUTE : SWARM_URI_CMD;
	swarm_send(uri, true, &dest, payload, len);
}

/* --- inbound dispatch --- */

static void apply_route(const uint8_t *body, uint16_t len)
{
	otIp6Address parent_ip;

	if (len < 16) {
		return;
	}
	/* body: primary(8) secondary(8) role_override(1) sub_count(1) subs... */
	const uint8_t *primary = &body[0];
	uint8_t all_ff = 0xFF;
	bool none = true;

	for (int i = 0; i < SWARM_EUI64_LEN; i++) {
		all_ff &= primary[i];
	}
	none = (all_ff == 0xFF);

	if (none) {
		g_node.have_parent = false;
	} else if (peer_lookup(primary, &parent_ip)) {
		memcpy(g_node.parent_eui, primary, SWARM_EUI64_LEN);
		g_node.parent = parent_ip;
		g_node.have_parent = true;
		LOG_INF("route: parent set");
	}

	if (len >= 17) {
		uint8_t role_override = body[16];
		if (role_override != 0xFF) {
			g_node.role = role_override;
		}
	}
}

static void apply_cmd(const uint8_t *body, uint16_t len)
{
	if (len < 2) {
		return;
	}
	uint8_t op = body[0];
	uint8_t plen = body[1];
	const uint8_t *params = &body[2];

	switch (op) {
	case SWARM_CMD_SET_ROLE:
		if (plen >= 1) {
			g_node.role = params[0];
			LOG_INF("cmd: role=0x%02x", g_node.role);
		}
		break;
	case SWARM_CMD_SET_RATE:
		if (plen >= 1) {
			g_node.tlm_period_ms = (uint16_t)params[0] * 100;
		}
		break;
	case SWARM_CMD_SET_MOUNT:
		if (plen >= 1) {
			g_node.mount = params[0];
			g_node.attached_len = (plen > 1) ? (plen - 1) : 0;
			if (g_node.attached_len > sizeof(g_node.attached_to)) {
				g_node.attached_len = sizeof(g_node.attached_to);
			}
			memcpy(g_node.attached_to, &params[1], g_node.attached_len);
			LOG_INF("cmd: mount=%d", g_node.mount);
		}
		break;
	case SWARM_CMD_SET_PERMISSIONS:
		if (plen >= 2) {
			g_node.capabilities = (uint16_t)params[0] |
					      ((uint16_t)params[1] << 8);
			LOG_INF("cmd: capabilities=0x%04x", g_node.capabilities);
		}
		break;
	case SWARM_CMD_IDENTIFY:
		sensors_identify();
		break;
	case SWARM_CMD_REBOOT:
		LOG_WRN("cmd: reboot requested");
		sys_reboot(SYS_REBOOT_WARM);
		break;
	default:
		break;
	}
}

/* Action ops are mission directives executed by the Jetson brain, not the nRF.
 * The nRF only gates them on this node's permissions and forwards them up. */
static bool is_action_op(uint8_t op)
{
	return op == SWARM_CMD_SET_WAYPOINT || op == SWARM_CMD_SET_MISSION ||
	       op == SWARM_CMD_OVERRIDE || op == SWARM_CMD_CLEAR_OVERRIDE;
}

/* Dispatch a CMD/BROADCAST body: forward action ops to the Jetson when this
 * node is overridable, otherwise apply config ops locally. `trusted` is true
 * when the frame came from the leader or the command-station gateway. */
static void handle_command(const uint8_t *buf, uint16_t len,
			   const uint8_t *body, uint16_t body_len, bool trusted)
{
	if (body_len < 1) {
		return;
	}
	if (is_action_op(body[0])) {
		bool overridable = g_node.capabilities & SWARM_CAP_OVERRIDABLE;
		if (overridable && trusted) {
			/* Hand the whole frame to the Jetson brain over serial. */
			serial_link_send(buf, len);
		} else {
			LOG_DBG("dropped action op 0x%02x (overridable=%d trusted=%d)",
				body[0], overridable, trusted);
		}
		return;
	}
	apply_cmd(body, body_len);
}

/* Jetson -> nRF: adopt an authoritative fused pose for broadcast. */
static void apply_pose_inject(const uint8_t *b, uint16_t len)
{
	if (len < 27) {
		return;
	}
	int32_t lat = (int32_t)sys_get_le32(&b[0]);
	int32_t lon = (int32_t)sys_get_le32(&b[4]);
	int32_t alt = (int32_t)sys_get_le32(&b[8]);
	uint16_t hdg = sys_get_le16(&b[12]);
	int16_t vn = (int16_t)sys_get_le16(&b[14]);
	int16_t ve = (int16_t)sys_get_le16(&b[16]);
	uint16_t ps = sys_get_le16(&b[18]);
	uint16_t hs = sys_get_le16(&b[20]);
	uint8_t sf = b[22];
	/* ts_ms (b[23..26]) is the Jetson clock; freshness is stamped on receipt. */
	sensors_inject_pose(lat, lon, alt, hdg, vn, ve, ps, hs, sf);
}

void swarm_handle_payload(const uint8_t *buf, uint16_t len,
			  const otIp6Address *src)
{
	if (len < SWARM_HDR_LEN || buf[0] != SWARM_MAGIC) {
		return;
	}
	uint8_t msg_type = buf[2];
	uint8_t hdr_flags = buf[3];
	const uint8_t *eui = &buf[4];
	const uint8_t *body = &buf[SWARM_HDR_LEN];
	uint16_t body_len = len - SWARM_HDR_LEN;
	bool trusted = hdr_flags & (SWARM_FLAG_LEADER | SWARM_FLAG_GATEWAY);

	if (src) {
		peer_update(eui, src);
	}

	switch (msg_type) {
	case SWARM_MSG_HELLO:
	case SWARM_MSG_TELEMETRY:
	case SWARM_MSG_NEIGHBORS:
		IF_ENABLED(CONFIG_SWARM_GATEWAY, (serial_link_send(buf, len);));
#if defined(CONFIG_SWARM_FORWARD_PEERS) && !defined(CONFIG_SWARM_GATEWAY)
		/* Plain node: mirror peer telemetry to our Jetson (tagged RELAYED so it
		 * isn't confused with our own messages) for the Jetson EKF peer fusion.
		 * `src` is set only for frames from the mesh, never our own. */
		if (src && len <= SWARM_MAX_FRAME) {
			uint8_t fwd[SWARM_MAX_FRAME];

			memcpy(fwd, buf, len);
			fwd[3] |= SWARM_FLAG_RELAYED;
			serial_link_send(fwd, len);
		}
#endif
		break;
	case SWARM_MSG_RANGE_REQ:
	case SWARM_MSG_RANGE_RESP:
		swarm_ranging_handle(buf, len, src);
		IF_ENABLED(CONFIG_SWARM_GATEWAY, (serial_link_send(buf, len);));
		break;
	case SWARM_MSG_ROUTE:
		apply_route(body, body_len);
		break;
	case SWARM_MSG_CMD:
		handle_command(buf, len, body, body_len, trusted);
		break;
	case SWARM_MSG_POSE_INJECT:
		/* Authoritative fused pose from this node's Jetson (over serial). */
		apply_pose_inject(body, body_len);
		break;
	case SWARM_MSG_MESH_SEND:
		/* Jetson asked to relay an opaque swarm frame onto the mesh. */
		if (body_len >= SWARM_HDR_LEN) {
			swarm_send(SWARM_URI_BROADCAST, false, &mcast_all_nodes,
				   body, body_len);
		}
		break;
	case SWARM_MSG_BROADCAST:
		/* Leader fleet command — permission-gated, action ops to the Jetson. */
		handle_command(buf, len, body, body_len, trusted);
		IF_ENABLED(CONFIG_SWARM_GATEWAY, (serial_link_send(buf, len);));
		break;
	default:
		break;
	}
}

/* --- CoAP resource handlers --- */

/* Acknowledge a confirmable request (the gateway's route/cmd downlink) so it is
 * not retransmitted. Non-confirmable multicast telemetry needs no response. */
static void send_ack(otMessage *request, const otMessageInfo *info)
{
	otMessage *resp;
	otError error;

	if (otCoapMessageGetType(request) != OT_COAP_TYPE_CONFIRMABLE) {
		return;
	}
	resp = otCoapNewMessage(g_node.ot, NULL);
	if (resp == NULL) {
		return;
	}
	error = otCoapMessageInitResponse(resp, request, OT_COAP_TYPE_ACKNOWLEDGMENT,
					  OT_COAP_CODE_CHANGED);
	if (error == OT_ERROR_NONE) {
		error = otCoapSendResponse(g_node.ot, resp, info);
	}
	if (error != OT_ERROR_NONE) {
		otMessageFree(resp);
	}
}

static void read_and_dispatch(otMessage *message, const otMessageInfo *info)
{
	uint8_t buf[SWARM_MAX_FRAME];
	uint16_t offset = otMessageGetOffset(message);
	uint16_t avail = otMessageGetLength(message) - offset;
	uint16_t n = avail < sizeof(buf) ? avail : sizeof(buf);

	if (otMessageRead(message, offset, buf, n) != n) {
		return;
	}
	swarm_ranging_on_message(&buf[4], &info->mPeerAddr, otMessageGetRss(message));
	swarm_handle_payload(buf, n, &info->mPeerAddr);
	send_ack(message, info);
}

#define DEFINE_HANDLER(name)                                                   \
	static void name(void *ctx, otMessage *message,                        \
			 const otMessageInfo *info)                            \
	{                                                                      \
		ARG_UNUSED(ctx);                                                \
		read_and_dispatch(message, info);                              \
	}

DEFINE_HANDLER(hello_handler)
DEFINE_HANDLER(telemetry_handler)
DEFINE_HANDLER(neighbors_handler)
DEFINE_HANDLER(range_handler)
DEFINE_HANDLER(route_handler)
DEFINE_HANDLER(cmd_handler)
DEFINE_HANDLER(broadcast_handler)

#define SWARM_RESOURCE(var, uri, handler)                                      \
	static otCoapResource var = {                                          \
		.mUriPath = uri, .mHandler = handler,                          \
		.mContext = NULL, .mNext = NULL }

SWARM_RESOURCE(res_hello, SWARM_URI_HELLO, hello_handler);
SWARM_RESOURCE(res_tlm, SWARM_URI_TELEMETRY, telemetry_handler);
SWARM_RESOURCE(res_nbr, SWARM_URI_NEIGHBORS, neighbors_handler);
SWARM_RESOURCE(res_rng, SWARM_URI_RANGE, range_handler);
SWARM_RESOURCE(res_rte, SWARM_URI_ROUTE, route_handler);
SWARM_RESOURCE(res_cmd, SWARM_URI_CMD, cmd_handler);
SWARM_RESOURCE(res_bc, SWARM_URI_BROADCAST, broadcast_handler);

int swarm_coap_init(void)
{
	otError error;

	g_node.ot = openthread_get_default_instance();
	if (!g_node.ot) {
		LOG_ERR("no OpenThread instance");
		return -1;
	}

	if (otIp6AddressFromString(SWARM_MCAST_ALL_NODES, &mcast_all_nodes) != OT_ERROR_NONE) {
		LOG_ERR("bad multicast address");
		return -1;
	}

	otCoapAddResource(g_node.ot, &res_hello);
	otCoapAddResource(g_node.ot, &res_tlm);
	otCoapAddResource(g_node.ot, &res_nbr);
	otCoapAddResource(g_node.ot, &res_rng);
	otCoapAddResource(g_node.ot, &res_rte);
	otCoapAddResource(g_node.ot, &res_cmd);
	otCoapAddResource(g_node.ot, &res_bc);

	error = otCoapStart(g_node.ot, SWARM_COAP_PORT);
	if (error != OT_ERROR_NONE) {
		LOG_ERR("otCoapStart failed: %d", error);
		return -1;
	}

	LOG_INF("swarm CoAP up on port %d (%s)", SWARM_COAP_PORT,
		IS_ENABLED(CONFIG_SWARM_GATEWAY) ? "gateway" : "node");
	return 0;
}
