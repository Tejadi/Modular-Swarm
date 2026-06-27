/*
 * protocol.h — canonical upstream contract between the nRF swarm node and the
 * Jetson Orin Nano over the USB-CDC-ACM data link.
 *
 * The link carries ONLY newline-delimited JSON: every frame is exactly one
 * JSON object followed by a single '\n'. Raw sensor wire data (e.g. NMEA) is
 * NEVER forwarded — only normalized values serialized in the shapes below.
 *
 * This file is the single source of truth for that contract; firmware must not
 * emit a field name or message type that is not described here.
 *
 *
 * 1. Manifest  (node -> host)  — capability discovery.
 *    Emitted when the host opens the port (CDC-ACM DTR asserted) and
 *    re-emitted on every reconnect. Count-agnostic: the `sensors` array simply
 *    lists every descriptor in the on-device sensor registry.
 *
 *      {"type":"manifest","schema":1,"node":"<hex-id>",
 *       "sensors":[
 *         {"id":"gps0","type":"gps","units":"deg,m","rate_hz":1,"transport":"uart"}
 *       ]}
 *
 * 2. Ack  (host -> node)  — handshake acknowledgement.
 *    The host replies with this after accepting a manifest. The node retries
 *    the manifest with timeout/backoff until it sees an ack (or gives up), then
 *    begins streaming. Minimum accepted form:
 *
 *      {"type":"ack","schema":1}
 *
 * 3. Data  (node -> host)  — one normalized sample, streamed after ack.
 *    The envelope is sensor-agnostic; `value` is whatever the sensor's
 *    descriptor serializes for itself.
 *
 *      {"type":"data","schema":1,"id":"gps0","ts_ms":12345,
 *       "value":{"fix":1,"sats":7,"lat":37.1234567,"lon":-122.1234567,
 *                "alt_m":12.3,"hdop":1.2,"utc":"123519.000"}}
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_PROTOCOL_JSON_H__
#define SWARM_PROTOCOL_JSON_H__

/* Bump whenever an incompatible change is made to any shape above. Carried in
 * every node->host object and echoed in the host's ack. */
#define PROTO_SCHEMA_VERSION 1

/* "type" discriminator values. */
#define PROTO_TYPE_MANIFEST "manifest" /* node -> host: capability manifest    */
#define PROTO_TYPE_DATA     "data"     /* node -> host: one normalized sample   */
#define PROTO_TYPE_ACK      "ack"      /* host -> node: manifest acknowledged   */

/* Every frame is one JSON object terminated by this byte. */
#define PROTO_LINE_TERM '\n'

#endif /* SWARM_PROTOCOL_JSON_H__ */
