/*
 * sensor_registry.h — universal capability-discovery abstraction.
 *
 * Each sensor registers a descriptor at init time. The Jetson handshake builds
 * its capability manifest by walking this registry, so adding a new sensor is
 * just another sensor_registry_register() call at boot — the handshake/manifest
 * logic never changes and is not aware of how many (or which) sensors exist.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SENSOR_REGISTRY_H__
#define SENSOR_REGISTRY_H__

#include <stddef.h>
#include <stdint.h>

#ifndef SENSOR_REGISTRY_MAX
#define SENSOR_REGISTRY_MAX 8
#endif

struct sensor_descriptor {
	const char *id;        /* stable unique id, e.g. "gps0"               */
	const char *type;      /* sensor class, e.g. "gps"                    */
	const char *units;     /* human/units hint, e.g. "deg,m"              */
	uint16_t    rate_hz;   /* nominal sample rate                         */
	const char *transport; /* how it is attached, e.g. "uart"             */

	/* Serialize the latest normalized sample as a JSON object value
	 * (INCLUDING the surrounding { }). Return the number of bytes written
	 * (excluding the NUL), or a negative value if no valid sample exists
	 * yet (in which case no "data" frame is emitted for this tick).
	 */
	int (*read_json)(char *buf, size_t cap);
};

/* Register a descriptor. The pointer must remain valid for the program's life
 * (typically a static const). Returns 0 on success, -ENOMEM if the registry is
 * full, -EINVAL on a bad descriptor. */
int sensor_registry_register(const struct sensor_descriptor *desc);

/* Number of registered descriptors. */
int sensor_registry_count(void);

/* Descriptor at index [0, count), or NULL if out of range. */
const struct sensor_descriptor *sensor_registry_get(int idx);

/* Build the capability manifest line (no trailing '\n') into buf: a single
 * JSON object with the schema version, the node id, and the full descriptor
 * array. Returns bytes written (excluding NUL), or -1 if it did not fit. */
int sensor_registry_build_manifest(char *buf, size_t cap, const char *node_id);

#endif /* SENSOR_REGISTRY_H__ */
