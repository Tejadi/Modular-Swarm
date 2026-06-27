/*
 * sensor_registry.c — see sensor_registry.h.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "protocol.h"
#include "sensor_registry.h"

LOG_MODULE_REGISTER(sensor_registry, CONFIG_COAP_SERVER_LOG_LEVEL);

static const struct sensor_descriptor *registry[SENSOR_REGISTRY_MAX];
static int registry_count;

int sensor_registry_register(const struct sensor_descriptor *desc)
{
	if (!desc || !desc->id || !desc->type || !desc->read_json) {
		return -EINVAL;
	}
	if (registry_count >= SENSOR_REGISTRY_MAX) {
		LOG_ERR("sensor registry full, dropping '%s'", desc->id);
		return -ENOMEM;
	}
	registry[registry_count++] = desc;
	LOG_INF("registered sensor '%s' (type=%s, %u Hz, %s)", desc->id,
		desc->type, desc->rate_hz, desc->transport ? desc->transport : "?");
	return 0;
}

int sensor_registry_count(void)
{
	return registry_count;
}

const struct sensor_descriptor *sensor_registry_get(int idx)
{
	if (idx < 0 || idx >= registry_count) {
		return NULL;
	}
	return registry[idx];
}

/* Append to buf at *off, bounds-checked. Returns false (and leaves *off past
 * cap) if it would overflow, so callers can bail with a single check. */
static bool append(char *buf, size_t cap, int *off, const char *fmt, ...)
{
	if (*off < 0 || (size_t)*off >= cap) {
		*off = cap; /* poison */
		return false;
	}
	va_list ap;
	va_start(ap, fmt);
	int n = vsnprintf(buf + *off, cap - *off, fmt, ap);
	va_end(ap);
	if (n < 0 || (size_t)(*off + n) >= cap) {
		*off = cap;
		return false;
	}
	*off += n;
	return true;
}

int sensor_registry_build_manifest(char *buf, size_t cap, const char *node_id)
{
	int off = 0;

	if (!append(buf, cap, &off,
		    "{\"type\":\"%s\",\"schema\":%d,\"node\":\"%s\",\"sensors\":[",
		    PROTO_TYPE_MANIFEST, PROTO_SCHEMA_VERSION,
		    node_id ? node_id : "")) {
		return -1;
	}

	for (int i = 0; i < registry_count; i++) {
		const struct sensor_descriptor *d = registry[i];

		if (!append(buf, cap, &off,
			    "%s{\"id\":\"%s\",\"type\":\"%s\",\"units\":\"%s\","
			    "\"rate_hz\":%u,\"transport\":\"%s\"}",
			    i ? "," : "", d->id, d->type,
			    d->units ? d->units : "",
			    d->rate_hz, d->transport ? d->transport : "")) {
			return -1;
		}
	}

	if (!append(buf, cap, &off, "]}")) {
		return -1;
	}
	return off;
}
