/*
 * bno055.c — direct-I2C Bosch BNO055 driver (see bno055.h).
 *
 * The BNO055 has its own fusion MCU, so we let it do the 9-DoF fusion (NDOF)
 * and read back: LINEAR_ACCEL (gravity removed, m/s^2), GYRO (yaw rate), and the
 * EULER heading (absolute, magnetometer-corrected). Units are left at the chip
 * defaults (accel m/s^2, gyro dps, euler deg) and converted here so the math is
 * explicit. All register addresses are page 0.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/i2c.h>
#include <zephyr/logging/log.h>

#include "bno055.h"

LOG_MODULE_REGISTER(swarm_bno055, CONFIG_SWARM_NODE_LOG_LEVEL);

#if DT_HAS_CHOSEN(swarm_imu_i2c)
#define HAVE_BUS 1
static const struct device *const bus = DEVICE_DT_GET(DT_CHOSEN(swarm_imu_i2c));
#else
#define HAVE_BUS 0
#endif

/* --- BNO055 register map (page 0) --- */
#define BNO_ADDR_PRIMARY    0x28
#define BNO_ADDR_ALT        0x29
#define REG_CHIP_ID         0x00
#define CHIP_ID_VAL         0xA0
#define REG_PAGE_ID         0x07
#define REG_GYR_DATA_X_LSB  0x14   /* 6 bytes: X,Y,Z int16 LE */
#define REG_EUL_HEAD_LSB    0x1A   /* 2 bytes: heading int16 LE */
#define REG_LIA_DATA_X_LSB  0x28   /* 6 bytes: X,Y,Z int16 LE */
#define REG_UNIT_SEL        0x3B
#define REG_OPR_MODE        0x3D
#define REG_PWR_MODE        0x3E
#define REG_SYS_TRIGGER     0x3F

#define OPR_CONFIGMODE      0x00
#define OPR_NDOF            0x0C
#define PWR_NORMAL          0x00

/* scaling (chip defaults) */
#define LIA_LSB_PER_MS2     100.0f   /* linear accel: 1 m/s^2 = 100 LSB */
#define GYR_LSB_PER_DPS     16.0f    /* gyro: 1 dps = 16 LSB            */
#define EUL_LSB_PER_DEG     16.0f    /* euler: 1 deg = 16 LSB           */
#define DEG2RAD             0.017453292519943295f

static uint8_t dev_addr;
static bool    ready;

#if HAVE_BUS
static int16_t le16(const uint8_t *p)
{
	return (int16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static bool probe_addr(uint8_t addr)
{
	uint8_t id = 0;

	/* The BNO055 needs ~650 ms after power-on before CHIP_ID reads back; we
	 * boot well after that, but retry a few times to be safe. */
	for (int i = 0; i < 12; i++) {
		if (i2c_reg_read_byte(bus, addr, REG_CHIP_ID, &id) == 0 &&
		    id == CHIP_ID_VAL) {
			dev_addr = addr;
			return true;
		}
		k_msleep(50);
	}
	return false;
}
#endif /* HAVE_BUS */

bool bno055_init(void)
{
#if !HAVE_BUS
	return false;
#else
	if (!device_is_ready(bus)) {
		return false;
	}
	if (!probe_addr(BNO_ADDR_PRIMARY) && !probe_addr(BNO_ADDR_ALT)) {
		return false;
	}

	/* CONFIGMODE -> page 0, normal power, default units, NDOF fusion. */
	(void)i2c_reg_write_byte(bus, dev_addr, REG_OPR_MODE, OPR_CONFIGMODE);
	k_msleep(25);
	(void)i2c_reg_write_byte(bus, dev_addr, REG_PAGE_ID, 0x00);
	(void)i2c_reg_write_byte(bus, dev_addr, REG_PWR_MODE, PWR_NORMAL);
	k_msleep(10);
	(void)i2c_reg_write_byte(bus, dev_addr, REG_UNIT_SEL, 0x00); /* m/s^2, dps, deg */
	(void)i2c_reg_write_byte(bus, dev_addr, REG_SYS_TRIGGER, 0x00);
	k_msleep(10);
	(void)i2c_reg_write_byte(bus, dev_addr, REG_OPR_MODE, OPR_NDOF);
	k_msleep(25); /* CONFIG -> NDOF takes ~7 ms */

	ready = true;
	LOG_INF("BNO055 found @ 0x%02x, NDOF fusion mode", dev_addr);
	return true;
#endif
}

bool bno055_present(void)
{
	return ready;
}

bool bno055_read(struct bno055_sample *s)
{
#if !HAVE_BUS
	(void)s;
	return false;
#else
	uint8_t b[6];

	if (!ready) {
		return false;
	}
	s->ax = s->ay = s->gyro_z = 0.0f;
	s->heading_rad = 0.0f;
	s->heading_valid = false;

	/* linear acceleration (gravity removed), body X/Y */
	if (i2c_burst_read(bus, dev_addr, REG_LIA_DATA_X_LSB, b, 6) != 0) {
		return false;
	}
	s->ax = le16(&b[0]) / LIA_LSB_PER_MS2;
	s->ay = le16(&b[2]) / LIA_LSB_PER_MS2;

	/* gyro Z = yaw rate */
	if (i2c_burst_read(bus, dev_addr, REG_GYR_DATA_X_LSB, b, 6) != 0) {
		return false;
	}
	s->gyro_z = (le16(&b[4]) / GYR_LSB_PER_DPS) * DEG2RAD;

	/* absolute heading (Euler) — present once the magnetometer calibrates */
	if (i2c_burst_read(bus, dev_addr, REG_EUL_HEAD_LSB, b, 2) == 0) {
		s->heading_rad = (le16(&b[0]) / EUL_LSB_PER_DEG) * DEG2RAD;
		s->heading_valid = true;
	}
	return true;
#endif
}
