/*
 * bno055.h — minimal direct-I2C driver for the Bosch BNO055 9-DoF IMU.
 *
 * Zephyr/NCS ships no BNO055 driver (it's a fusion sensor with an onboard
 * Cortex-M0), so this talks to it directly over the chosen IMU I2C bus
 * (chosen { swarm,imu-i2c }). It runs the chip in NDOF fusion mode and reads
 * the gravity-free LINEAR acceleration + yaw rate the EKF wants, plus the
 * magnetometer-corrected absolute heading (a bonus the MPU-6050 cannot give).
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef SWARM_BNO055_H__
#define SWARM_BNO055_H__

#include <stdbool.h>

/* One IMU sample, already normalised to the EKF's units/frame expectations. */
struct bno055_sample {
	float ax;            /* linear (gravity-free) body accel X, m/s^2  */
	float ay;            /* linear (gravity-free) body accel Y, m/s^2  */
	float gyro_z;        /* yaw rate, rad/s                            */
	float heading_rad;   /* absolute heading (compass, from North), rad */
	bool  heading_valid; /* mag-calibrated absolute heading available  */
};

/* Probe the chosen IMU I2C bus for a BNO055 (addr 0x28, then 0x29) and, if
 * found, configure it into NDOF fusion mode. Returns true on success. */
bool bno055_init(void);

/* True once bno055_init() has found and configured a chip. */
bool bno055_present(void);

/* Read one sample. Returns true on a clean I2C read, false otherwise. */
bool bno055_read(struct bno055_sample *s);

#endif /* SWARM_BNO055_H__ */
