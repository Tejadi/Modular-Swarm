/*
 * gps.h — Adafruit Ultimate GPS (MTK3339) NMEA driver.
 *
 * Reads NMEA off the board's hardware UART (chosen swarm,gps-uart), parses RMC
 * + GGA with a minmea-style parser into a normalized fix, and registers a "gps"
 * descriptor with the sensor registry. Raw NMEA is never exposed upstream.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef GPS_H__
#define GPS_H__

#include <stdbool.h>
#include <stdint.h>

/* Normalized fix. Degrees are decimal (+N/+E positive). */
struct gps_fix {
	bool     valid;        /* true once a usable position has been parsed   */
	uint8_t  fix_quality;  /* GGA fix quality: 0 none, 1 GPS, 2 DGPS, ...   */
	uint8_t  satellites;   /* satellites in use (GGA)                       */
	double   latitude;     /* decimal degrees, +N / -S                      */
	double   longitude;    /* decimal degrees, +E / -W                      */
	double   altitude_m;   /* MSL altitude in metres (optional, 0 if none)  */
	double   hdop;         /* horizontal dilution of precision (optional)   */
	uint8_t  hours;        /* UTC time of fix                               */
	uint8_t  minutes;
	uint8_t  seconds;
	uint16_t millis;
};

/* Bring up the GPS UART, push boot-time PMTK config (RMC+GGA only, optional
 * 57600/5Hz via CONFIG_APP_GPS_HIGH_RATE), start interrupt-driven NMEA RX, and
 * register the "gps" sensor descriptor. Returns 0 on success, <0 if no GPS UART
 * is configured for this board (in which case the node still runs without GPS).
 */
int gps_init(void);

/* Copy the most recent fix into *out. Returns true if a valid fix exists. */
bool gps_get_fix(struct gps_fix *out);

#endif /* GPS_H__ */
