/*
 * Reconfigurable swarm module — firmware entry point.
 *
 * Brings up OpenThread + the swarm CoAP overlay, probes the modular sensor
 * stack, and runs periodic announce / telemetry / neighbor timers off a work
 * queue. A plain node announces itself and streams telemetry; the gateway build
 * (CONFIG_SWARM_GATEWAY) instead bridges the mesh to the Olympus host over
 * serial and only emits a neighbor report so the command station knows which
 * modules it hears directly (the roots of the routing tree).
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/net/openthread.h>
#include <dk_buttons_and_leds.h>
#include <openthread/thread.h>
#include <openthread/link.h>
#include <string.h>

#include "swarm_node.h"
#include "swarm_coap.h"
#include "sensors.h"
#include "ranging.h"
#include "serial_link.h"

LOG_MODULE_REGISTER(swarm_node, CONFIG_SWARM_NODE_LOG_LEVEL);

#define OT_CONNECTION_LED DK_LED1

struct swarm_node g_node;

#define WORKQ_STACK_SIZE 2048
#define WORKQ_PRIORITY 5
K_THREAD_STACK_DEFINE(swarm_workq_stack, WORKQ_STACK_SIZE);
static struct k_work_q swarm_workq;

static struct k_work hello_work;
static struct k_work tlm_work;
static struct k_work nbr_work;

static struct k_timer hello_timer;
static struct k_timer tlm_timer;
static struct k_timer nbr_timer;

/* --- work handlers: hold the OpenThread API lock around CoAP sends --- */

static void with_ot_lock(void (*fn)(void))
{
	struct openthread_context *ctx = openthread_get_default_context();

	openthread_api_mutex_lock(ctx);
	fn();
	openthread_api_mutex_unlock(ctx);
}

static void hello_work_fn(struct k_work *w) { ARG_UNUSED(w); with_ot_lock(swarm_coap_send_hello); }
static void tlm_work_fn(struct k_work *w)   { ARG_UNUSED(w); with_ot_lock(swarm_coap_send_telemetry); }
static void nbr_work_fn(struct k_work *w)   { ARG_UNUSED(w); with_ot_lock(swarm_coap_send_neighbors); }

static void hello_timer_fn(struct k_timer *t) { ARG_UNUSED(t); k_work_submit_to_queue(&swarm_workq, &hello_work); }
static void tlm_timer_fn(struct k_timer *t)   { ARG_UNUSED(t); k_work_submit_to_queue(&swarm_workq, &tlm_work); }
static void nbr_timer_fn(struct k_timer *t)   { ARG_UNUSED(t); k_work_submit_to_queue(&swarm_workq, &nbr_work); }

/* --- thread connectivity LED --- */

static void on_thread_state_changed(otChangedFlags flags, void *ctx)
{
	ARG_UNUSED(ctx);
	if (!(flags & OT_CHANGED_THREAD_ROLE)) {
		return;
	}
	switch (otThreadGetDeviceRole(g_node.ot)) {
	case OT_DEVICE_ROLE_CHILD:
	case OT_DEVICE_ROLE_ROUTER:
	case OT_DEVICE_ROLE_LEADER:
		dk_set_led_on(OT_CONNECTION_LED);
		g_node.connected = true;
		break;
	default:
		dk_set_led_off(OT_CONNECTION_LED);
		g_node.connected = false;
		break;
	}
}
static struct openthread_state_changed_callback ot_state_cb = {
	.otCallback = on_thread_state_changed,
};

/* --- identity from the factory EUI64 + build-time configuration --- */

static void init_identity(void)
{
	otExtAddress ext;

	otLinkGetFactoryAssignedIeeeEui64(g_node.ot, &ext);
	memcpy(g_node.eui, ext.m8, SWARM_EUI64_LEN);

	g_node.role = 0;
	if (IS_ENABLED(CONFIG_SWARM_ROLE_PROVIDER)) g_node.role |= SWARM_ROLE_PROVIDER;
	if (IS_ENABLED(CONFIG_SWARM_ROLE_CONSUMER)) g_node.role |= SWARM_ROLE_CONSUMER;
	if (IS_ENABLED(CONFIG_SWARM_ROLE_RELAY))    g_node.role |= SWARM_ROLE_RELAY;

	g_node.mount = IS_ENABLED(CONFIG_SWARM_MOUNT_VEHICLE)
			? SWARM_MOUNT_VEHICLE : SWARM_MOUNT_STANDALONE;

	g_node.capabilities = 0;
	if (IS_ENABLED(CONFIG_SWARM_CAP_AUTONOMOUS)) g_node.capabilities |= SWARM_CAP_AUTONOMOUS;
	if (IS_ENABLED(CONFIG_SWARM_CAP_OVERRIDABLE)) g_node.capabilities |= SWARM_CAP_OVERRIDABLE;
	if (IS_ENABLED(CONFIG_SWARM_CAP_PASSIVE_RX)) g_node.capabilities |= SWARM_CAP_PASSIVE_RX;
	if (IS_ENABLED(CONFIG_SWARM_CAP_BEACON_TX)) g_node.capabilities |= SWARM_CAP_BEACON_TX;
	if (IS_ENABLED(CONFIG_SWARM_CAP_RELAY_ONLY)) g_node.capabilities |= SWARM_CAP_RELAY_ONLY;

	strncpy(g_node.name, CONFIG_SWARM_NODE_NAME, sizeof(g_node.name) - 1);
	g_node.name_len = strlen(g_node.name);

	strncpy(g_node.attached_to, CONFIG_SWARM_ATTACHED_TO, sizeof(g_node.attached_to) - 1);
	g_node.attached_len = strlen(g_node.attached_to);

	g_node.tlm_period_ms = SWARM_TLM_PERIOD_MS;

	LOG_INF("module %02x%02x%02x%02x%02x%02x%02x%02x role=0x%02x mount=%d '%s'",
		g_node.eui[0], g_node.eui[1], g_node.eui[2], g_node.eui[3],
		g_node.eui[4], g_node.eui[5], g_node.eui[6], g_node.eui[7],
		g_node.role, g_node.mount, g_node.name);
}

int main(void)
{
	LOG_INF("Start swarm_node (%s)",
		IS_ENABLED(CONFIG_SWARM_GATEWAY) ? "GATEWAY" : "node");

	g_node.ot = openthread_get_default_instance();
	if (!g_node.ot) {
		LOG_ERR("no OpenThread instance");
		return 0;
	}

	if (dk_leds_init()) {
		LOG_WRN("LED init failed");
	}

	init_identity();
	swarm_ranging_init();
	serial_link_init();
	g_node.sensors = swarm_sensors_init();

	if (swarm_coap_init()) {
		LOG_ERR("CoAP init failed");
		return 0;
	}

	k_work_queue_init(&swarm_workq);
	k_work_queue_start(&swarm_workq, swarm_workq_stack,
			   K_THREAD_STACK_SIZEOF(swarm_workq_stack),
			   WORKQ_PRIORITY, NULL);
	k_work_init(&hello_work, hello_work_fn);
	k_work_init(&tlm_work, tlm_work_fn);
	k_work_init(&nbr_work, nbr_work_fn);

	k_timer_init(&hello_timer, hello_timer_fn, NULL);
	k_timer_init(&tlm_timer, tlm_timer_fn, NULL);
	k_timer_init(&nbr_timer, nbr_timer_fn, NULL);

	/* Plain nodes AND the leader announce + stream telemetry (the leader is a
	 * fleet position anchor); a pure gateway is infrastructure and does not. */
	if (!IS_ENABLED(CONFIG_SWARM_GATEWAY) || IS_ENABLED(CONFIG_SWARM_LEADER)) {
		k_timer_start(&hello_timer, K_MSEC(500), K_MSEC(SWARM_HELLO_PERIOD_MS));
		k_timer_start(&tlm_timer, K_MSEC(1000), K_MSEC(SWARM_TLM_PERIOD_MS));
	}
	k_timer_start(&nbr_timer, K_MSEC(1500), K_MSEC(SWARM_NBR_PERIOD_MS));

	openthread_state_changed_callback_register(&ot_state_cb);
	openthread_run();

	return 0;
}
