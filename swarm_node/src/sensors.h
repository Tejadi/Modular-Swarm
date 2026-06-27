/*
 * Modular sensor layer. Detects which sensors the current build actually has
 * (from devicetree), exposes that as the HELLO sensor bitmap, runs a GPS+IMU
 * EKF (see ekf.c) on a dedicated thread, and produces a telemetry snapshot with
 * the fused fix. Missing sensors degrade gracefully — their bits stay 0.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_SENSORS_H__
#define SWARM_SENSORS_H__

#include <stdint.h>
#include <stdbool.h>

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
	/* Fused-kinematics trailer (EKF output). Sent only when has_kinematics. */
	bool     has_kinematics;
	int16_t  vel_n_cms;
	int16_t  vel_e_cms;
	uint16_t pos_std_cm;
	uint16_t hdg_std_cd;
	uint8_t  ekf_flags;     /* enum swarm_ekf_flag    */
	uint8_t  n_readings;
	struct {
		uint8_t channel;    /* enum swarm_channel */
		float   value;
	} readings[SWARM_MAX_READINGS];
};

/* Probe attached sensors, start the EKF thread; returns the sensor bitmap. */
uint16_t swarm_sensors_init(void);

/* Mesh-facing snapshot: the Jetson's injected pose while fresh, else the EKF. */
void swarm_sensors_read(struct swarm_tlm_snapshot *out);

/* Jetson-facing snapshot: ALWAYS the on-board EKF fix (no injected pose), so the
 * Jetson fuses an independent estimate rather than reading back its own injection. */
void swarm_sensors_read_own(struct swarm_tlm_snapshot *out);

/* Adopt a Jetson-computed fused pose (POSE_INJECT over the serial link). While
 * it stays fresh (SWARM_POSE_FRESH_MS) telemetry broadcasts this instead of the
 * on-board EKF fix; the EKF keeps running underneath as the fallback. */
void sensors_inject_pose(int32_t lat_e7, int32_t lon_e7, int32_t alt_cm,
			 uint16_t heading_cdeg, int16_t vel_n_cms, int16_t vel_e_cms,
			 uint16_t pos_std_cm, uint16_t hdg_std_cd, uint8_t src_flags);

/* Most recent battery percentage (also used by the HELLO descriptor). */
uint8_t sensors_battery_pct(void);

/* Blink the locate LED so an operator can find this module in the field. */
void sensors_identify(void);

#endif /* SWARM_SENSORS_H__ */
