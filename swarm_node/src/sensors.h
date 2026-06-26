/*
 * Modular sensor layer. Detects which sensors the current build actually has
 * (from devicetree), exposes that as the HELLO sensor bitmap, and produces a
 * telemetry snapshot. Missing sensors degrade gracefully — their bits stay 0.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_SENSORS_H__
#define SWARM_SENSORS_H__

#include <stdint.h>

#define SWARM_MAX_READINGS 8

struct swarm_tlm_snapshot {
	uint8_t  status;        /* enum swarm_status      */
	uint8_t  pos_source;    /* enum swarm_pos_source  */
	int32_t  lat_e7;
	int32_t  lon_e7;
	int32_t  alt_cm;
	uint16_t heading_cdeg;
	uint8_t  battery_pct;
	uint8_t  pos_quality;
	uint8_t  n_readings;
	struct {
		uint8_t channel;    /* enum swarm_channel */
		float   value;
	} readings[SWARM_MAX_READINGS];
};

/* Probe attached sensors; returns the populated sensor bitmap (swarm_sensor_bit). */
uint16_t swarm_sensors_init(void);

/* Fill a telemetry snapshot from the latest sensor data + dead-reckoning. */
void swarm_sensors_read(struct swarm_tlm_snapshot *out);

/* Most recent battery percentage (also used by the HELLO descriptor). */
uint8_t sensors_battery_pct(void);

/* Blink the locate LED so an operator can find this module in the field. */
void sensors_identify(void);

#endif /* SWARM_SENSORS_H__ */
