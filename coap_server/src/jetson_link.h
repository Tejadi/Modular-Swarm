/*
 * jetson_link.h — upstream USB-CDC-ACM data link to the Jetson Orin Nano.
 *
 * The nRF is the USB device; the Jetson is the host. This link emits ONLY
 * newline-delimited JSON per protocol.h. It detects the host opening the port
 * via CDC-ACM DTR, announces a capability manifest (built from the sensor
 * registry), waits for the host ack with timeout/backoff, then streams
 * normalized sensor data. It re-announces on every reconnect.
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#ifndef JETSON_LINK_H__
#define JETSON_LINK_H__

/* Enable USB, bring up the CDC-ACM data port, and start the link thread.
 * Returns 0 on success, <0 if no data port is configured for this board. */
int jetson_link_init(void);

#endif /* JETSON_LINK_H__ */
