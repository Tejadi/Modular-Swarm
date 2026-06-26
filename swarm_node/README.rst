.. _swarm_node:

Reconfigurable Swarm Module (OpenThread + CoAP overlay)
#######################################################

This firmware turns the Nordic OpenThread CoAP sample into a node for a
reconfigurable, ad-hoc swarm that reports into the Olympus command center.

A module can ride on a vehicle or operate standalone; it carries any subset of
the modular sensor stack (GPS, IMU, barometer, ...). When it powers on it
announces itself on the mesh; the command station records its position, its
available sensors, and whether it contributes data to the swarm or only
consumes swarm information. An overlay autorouter on the station then connects
the modules robustly on top of OpenThread's own mesh routing.

What it does
************

* Forms / joins the Thread mesh using the shared network key (same as the
  original coap_server / coap_client samples).
* Runs the swarm CoAP overlay (see ``../proto/swarm_protocol.h``):

  ``swm/hello`` announce, ``swm/tlm`` telemetry, ``swm/nbr`` neighbor link
  report, ``swm/rng`` ranging, ``swm/rte`` route assignment, ``swm/cmd``
  command. Announce/telemetry/neighbor are non-confirmable realm-local
  multicast (flooded mesh-wide to the gateway); route/cmd are confirmable
  unicast downlink.
* Probes the modular sensor stack from devicetree and reports the real sensor
  bitmap. GPS builds publish a GPS fix; GPS-denied builds dead-reckon from the
  IMU and are localized by the station via neighbor ranging.
* Speaks a COBS+CRC framed serial protocol to a companion computer over a
  dedicated USB-CDC port (``proto/swarm_proto.py`` is the host-side mirror).

Builds
******

Plain node (provider + relay, the default)::

    ./build.sh

Command-station gateway (bridges the mesh to the Olympus host over serial,
injects route/cmd downlink)::

    ./build.sh --gateway

Or with west directly::

    west build -b xiao_ble .                                   # node
    west build -b xiao_ble . -- -DEXTRA_CONF_FILE=overlay-gateway.conf  # gateway

Role, mount, name and vehicle attachment are Kconfig options
(``CONFIG_SWARM_ROLE_*``, ``CONFIG_SWARM_MOUNT_VEHICLE``,
``CONFIG_SWARM_NODE_NAME``, ``CONFIG_SWARM_ATTACHED_TO``) and can be changed at
runtime by the ``cmd`` channel.

Wiring
******

Board overlays in ``boards/`` add the dedicated swarm data CDC-ACM port
(``chosen { swarm,serial }``) and show where to attach the modular IMU / GPS
(``chosen { swarm,imu; swarm,gps-uart }``). Missing sensors degrade gracefully
— their HELLO bit stays cleared and the station ranges the module instead.

Bring-up
********

#. Flash >= 2 nodes and 1 ``--gateway`` node.
#. On each, the ``ot`` shell is on the console CDC-ACM port: ``ot state`` should
   reach ``child`` / ``router`` and LED1 lights once attached.
#. Wire the gateway's swarm data port to the Olympus host and run
   ``python -m olympus_link --port /dev/ttyACM<data>``.
#. Each module appears in the command center as it powers on; the autorouter
   connects them.
