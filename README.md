# nRF Reconfigurable Swarm — Thread/CoAP mesh with an Olympus command center

This repo began as the Nordic **OpenThread CoAP** sample pair (`coap_server` /
`coap_client`) on the **Seeed XIAO nRF52840**, and now hosts a **reconfigurable,
ad-hoc swarm** that reports into the **Olympus** command center.

Each agent is a Jetson + nRF module carrying any subset of a modular sensor stack
(GPS, IMU, or neither). A module is the unit of identity — it can ride a vehicle
or operate standalone. When a module comes online the command station records its
position, its available sensors, and whether it **contributes** data to the swarm
or only **consumes** swarm information, and shows a live view of every module. An
overlay **autorouter** connects the modules robustly on top of OpenThread's own
mesh routing.

Everything new lives in this repo (`proto/`, `swarm_node/`, `jetson_agent/`,
`olympus_link/`, `sim/`); Olympus is reached over its existing network interfaces,
so a module appears in the command center with **no Olympus code change**.

- **Built with:** nRF Connect SDK **v3.3.1** (Zephyr 4.3.99)
- **Target board:** `xiao_ble` (Seeed XIAO nRF52840) or `nrf52840dk`
- **Two layers:** the swarm overlay (first half of this README) is built on the
  original **CoAP light demo** (second half), which still documents the Thread/CoAP
  mechanics it reuses.
- **Full design:** see [`OLYMPUS_INTEGRATION.md`](OLYMPUS_INTEGRATION.md)

---

## Table of contents

**Swarm + Olympus**
- [Reconfigurable swarm + Olympus command center](#reconfigurable-swarm--olympus-command-center)
- [Architecture](#architecture)
- [The swarm protocol: current vs upgraded](#the-swarm-protocol-current-vs-upgraded)
- [Run it step by step](#run-it-step-by-step)
- [Repository layout](#repository-layout)

**Underlying CoAP light demo**
- [The demo in one picture](#the-demo-in-one-picture)
- [Hardware notes for the XIAO](#hardware-notes-for-the-xiao)
- [The shared CoAP interface](#the-shared-coap-interface)
- [How the server works](#how-the-server-works)
- [How the client works](#how-the-client-works)
- [Thread network parameters](#thread-network-parameters)
- [LED & button mapping on the XIAO](#led--button-mapping-on-the-xiao)
- [Building](#building)
- [Flashing (UF2 bootloader)](#flashing-uf2-bootloader)
- [Serial console & the OpenThread shell](#serial-console--the-openthread-shell)
- [Bringing up the network](#bringing-up-the-network)
- [Repository layout](#repository-layout)
- [Current limitations / next phase](#current-limitations--next-phase)

---

## Reconfigurable swarm + Olympus command center

The swarm turns the stock light demo into a fleet of reconfigurable modules that
report into Olympus. Each module announces itself on the mesh; the command station
records its **position**, its **available sensors**, and whether it **contributes
to** or only **consumes** swarm information, then keeps a live view of everything.
An overlay autorouter connects the modules once they are online.

### Architecture

```
 Agent (xN)                          RF mesh            Command station (Olympus host)
 GPS/IMU → jetson_agent  --USB-CDC--  nRF (swarm_node) --OT/CoAP--> gateway nRF --USB-CDC--> olympus_link
            |  (redundant IP: Zenoh + vehicle-api over WiFi/5G) ---------------------------^   |
            v                                                                                   v
        Olympus Zenoh  <-------------------------------- telemetry / topology / registry  ------+
        Olympus dashboard renders swarm/{id}/telemetry (existing key)
```

| Component | Where | What it does |
|-----------|-------|--------------|
| [`proto/`](proto) | C + Python | one wire format both sides share (CoAP overlay + COBS/CRC serial framing) |
| [`swarm_node/`](swarm_node) | Zephyr/C | module firmware (announce, telemetry, ranging) and the `--gateway` build |
| [`jetson_agent/`](jetson_agent) | Python | per-agent companion: redundant IP path + Jetson sensor fusion |
| [`olympus_link/`](olympus_link) | Python | command station: registry, localization, autorouter, push to Olympus |
| [`sim/`](sim) | Python | PTY swarm simulator + automated end-to-end test (no hardware) |

OpenThread keeps doing L2/L3 mesh routing and self-healing; the autorouter is an
**application overlay** that assigns roles, aggregation parents (with a redundant
secondary), and provider→consumer subscriptions, and pushes them back down the mesh.
Modules without GPS are localized by the station: their RTT/RSSI ranges to GPS
anchors are multilaterated (least squares), fused with IMU dead-reckoning. The
nRF52840 cannot measure true time-of-flight, so ranges are coarse (meters–tens of
meters) and surfaced as a position quality.

### System hierarchy

Three levels, one mesh. Each vehicle decides for **itself** (decentralized) unless
the leader sends an override; passive nodes only ever transmit. The nRF is the
sensor + radio front-end; the Jetson is the brain.

Each module runs **two EKFs in series**. **EKF #1** on the nRF fuses GPS + IMU into
a fused pose. **EKF #2** on the Jetson takes that fused pose and — *only if it has
more to work with* (a camera's VIO, and/or peer fixes within 50 m) — fuses those in
for a better fix; with nothing extra it simply uses the nRF's pose as-is. When EKF #2
does improve the fix, it injects the refined pose back to the nRF (`POSE_INJECT`) to
broadcast on the mesh.

```
                       LEVEL 3 — COMMAND / BASE STATION   (one leader)
     ┌─────────────────────────────────────────────────────────────────────┐
     │  operator browser ──► Olympus dashboard (React/Cesium)  :3000         │
     │        ▲ map / telemetry              │ click-to-command              │
     │        │                              ▼                               │
     │   Zenoh router ◄──── swarm-link ────► vehicle-api                     │
     │                       │  registry · localize · autorouter ·          │
     │                       │  command-translate (Olympus cmd → swarm)      │
     │                       ▼  USB-CDC  (swarm_proto, COBS+CRC)             │
     │                  LEADER nRF   (GPS+IMU + EKF, SWARM_FLAG_LEADER)      │
     └───────────────────────────┬─────────────────────────────────────────┘
                                  │
        OpenThread 802.15.4 mesh — swarm_proto over CoAP, multicast ff03::1
              ┌───────────────────┴───────────────────────┐
              ▼                                            ▼
 LEVEL 2 — ACTIVE VEHICLE  (member, ×N)            LEVEL 1 — PASSIVE NODE (×M)
 ┌─────────────────────────────────────────────┐  ┌──────────────────────────────┐
 │ GPS ┐                                        │  │ GPS/IMU ─► nRF (beacon)       │
 │ IMU ┴─► nRF · EKF #1  (GPS + IMU fusion)      │  │ caps: PASSIVE_RX | BEACON_TX  │
 │              │ fused pose                     │  │ transmits telemetry only;     │
 │  POSE_INJECT ▲│▼ serial (USB-CDC)             │  │ command gate refuses actions  │
 │ Jetson · EKF #2  (fuse / refine):            │  └──────────────────────────────┘
 │   in  = nRF fused pose                        │
 │       + camera VIO        (only if a camera)  │
 │       + peer fixes ≤50 m  (only if in range)  │
 │   out: no extras  → uses the nRF pose as-is   │
 │        has extras → refined pose ─► INJECT    │
 │ • mission FSM (explore/search/goto/RTL)       │
 │ • coordination: Voronoi coverage + ORCA       │
 │ • perception: YOLO (separate process)         │
 │ • override ◄ leader  (if OVERRIDABLE)         │
 │ caps: AUTONOMOUS | OVERRIDABLE                │
 └─────────────────────────────────────────────┘
```

**Who decides what**

| Level | Node | Decides | Commandable? |
|---|---|---|---|
| 3 | Leader / base station | issues fleet goals + overrides; hosts the GUI | — (it is the commander) |
| 2 | Active vehicle | its own mission + collision avoidance, on its Jetson | yes if `OVERRIDABLE` — a leader override preempts the local policy; **EMERGENCY** (imminent collision) outranks even the override |
| 1 | Passive node | nothing — transmits GPS/IMU/sensors only | no (`PASSIVE_RX`/`BEACON_TX`; the on-node gate drops action commands) |

### Leader node vs member node — step by step

A unit is "leader" or "member" by **which firmware you flash** and **which software
the host runs**. Same hardware, two recipes — there is no runtime switch (the leader
flag and mesh bridging are compiled in).

**Leader / command module** (one per swarm)
1. Flash its nRF as leader: `cd swarm_node && ./build.sh --leader`, double-tap RESET, then `./build.sh --leader --flash`.
2. Plug that nRF's USB into the command computer (your laptop, or a Jetson).
3. Start the command stack: `./run-command-station.sh` (Zenoh + vehicle-api + dashboard + swarm-link).
4. Open the GUI at `http://localhost:3000` (or `http://<host-ip>:3000` from another machine). The leader shows up as a fleet anchor (it carries GPS+IMU) and is the only node allowed to issue overrides.

**Member / active vehicle** (many)
1. Flash its nRF as a plain node: `cd swarm_node && ./build.sh`, then `./build.sh --flash`.
2. Plug that nRF's USB into the vehicle's Jetson.
3. Run the agent on the Jetson:
   ```bash
   python3 -m jetson_agent --port /dev/ttyACM1 --prefix ceres \
     --vehicle-api http://<leader-ip>:3001 --zenoh-rest http://<leader-ip>:8000
   ```
   It runs its own EKF + mission + avoidance and obeys leader overrides (vehicle mounts default to `AUTONOMOUS|OVERRIDABLE`).

**Passive node** (beacon / external provider)
- Flash a plain node built passive (`CONFIG_SWARM_CAP_PASSIVE_RX=y`, no `OVERRIDABLE`/`AUTONOMOUS`). It transmits telemetry and the command gate refuses to command it. No Jetson needed.

### The swarm protocol: current vs upgraded

The stock firmware was a button-driven 1-byte `PUT /light` plus a one-shot
`GET /provisioning` — fire-and-forget, no sensors, no liveness. The upgrade keeps
OpenThread's mesh and adds a CoAP overlay ([`proto/swarm_protocol.h`](proto/swarm_protocol.h)):

| Resource | Dir | Purpose |
|----------|-----|---------|
| `swm/hello` | up | periodic descriptor: id, role (provider/consumer/relay), mount, sensor bitmap, battery — the "module online" event |
| `swm/tlm` | up | position (gps/ranged/imu/fused) + heading + sensor readings + status |
| `swm/nbr` | up | neighbor link table (RSSI + RTT range + quality) — feeds the autorouter |
| `swm/rng` | p2p | two-way RTT ranging for GPS-denied localization |
| `swm/rte` | down | route assignment: primary + secondary parent + subscriptions |
| `swm/cmd` | down | command channel (set role/mount/rate, identify, reboot) |

Up messages are non-confirmable realm-local multicast (flooded mesh-wide to the
gateway); downlink is confirmable unicast (ACKed). Liveness is a `hello` heartbeat
+ last-seen TTL.

### Run it step by step

Requires **Python 3.9+** (stdlib only — no pip installs needed for the host side).

#### A. Without hardware — simulator → command center

Exercises the whole command-station pipeline on one machine.

1. (optional) Run the automated end-to-end check:

   ```bash
   python3 sim/test_e2e.py        # registration, ranged localization, routing, failover, reconfig
   ```

2. Start the swarm simulator. It opens a pseudo-serial port and prints the path:

   ```bash
   python3 sim/swarm_sim.py
   #  point olympus_link at:  --port /dev/pts/N
   ```

3. In another terminal, run the command-station service against that path. To just
   watch it work with no Olympus running, use the dry-run sink:

   ```bash
   python3 -m olympus_link --port /dev/pts/N --sink dryrun -v
   ```

   You will see the gateway get discovered, modules announce (`HELLO`), register,
   get localized by ranging, and receive `ROUTE` assignments (parent / secondary /
   subscriptions).

4. To drive a **live Olympus dashboard** instead, bring up Olympus and use the
   `rest` sink:

   ```bash
   # in the olympus repo (separate checkout):
   docker compose up -d zenoh-router vehicle-api dashboard
   # back in nRF-swarm:
   python3 -m olympus_link --port /dev/pts/N --prefix ceres --sink rest
   # open http://localhost:3000 — modules appear on the map as they come online,
   # move, drop out (failover), and reconfigure.
   ```

   `--prefix` must match the dashboard instance (the bundled dashboard polls
   `ceres/**`; the Rust bridge default is `olympus`). Set `SWARM_VEHICLE_API_KEY`
   only if vehicle-api auth is enabled (it is off by default).

#### B. On hardware

1. Build + flash the firmware (needs the nRF Connect SDK toolchain — see
   [Building](#building)):

   ```bash
   cd swarm_node
   ./build.sh                 # plain node (provider+relay) — flash >= 2 boards
   ./build.sh --gateway       # command-station gateway — flash 1, wire it to the host
   ```

   Role / mount / name are Kconfig options (`CONFIG_SWARM_ROLE_*`,
   `CONFIG_SWARM_MOUNT_VEHICLE`, `CONFIG_SWARM_NODE_NAME`, `CONFIG_SWARM_ATTACHED_TO`);
   wire the modular IMU / GPS in the board overlay (`boards/*.overlay`). Missing
   sensors degrade gracefully — their HELLO bit stays cleared and the station ranges
   the module instead.

   The **IMU is auto-detected at boot** (`sensors.c`): an **MPU-6050** (`0x68`, Zephyr
   driver) *or* a **BNO055** (`0x28`, direct-I2C `bno055.c` since NCS ships no driver).
   Both can stay wired; the firmware uses whichever answers. A BNO055 additionally
   feeds its magnetometer-corrected **absolute heading** into EKF #1 (observable even
   at a standstill); MPU-6050 nodes keep heading from gyro + GPS course. With **no GPS
   lock** the node anchors at a **placeholder** (Philadelphia) and dead-reckons from
   the IMU so it still reports a position; the first real GPS fix re-anchors at the
   true location, and the IMU keeps dead-reckoning if GPS is later lost.

2. Confirm the mesh forms: on each board's `ot` shell console, `ot state` reaches
   `child` / `router` / `leader`.

3. On the command-station host, wire the gateway's swarm **data** CDC port (the
   second `/dev/ttyACM*` — the first is the `ot` shell) and run:

   ```bash
   python3 -m olympus_link --port /dev/ttyACM0 --prefix ceres --sink rest \
       --vehicle-api http://localhost:3001
   ```

4. (optional, redundant IP path) On each agent's Jetson, run the companion:

   ```bash
   python3 -m jetson_agent --port /dev/ttyACM1 --prefix ceres \
       --vehicle-api http://<command-station>:3001
   # add SWARM_JETSON_GPS=gpsd to fold in a Jetson-side GPS
   ```

5. Power on modules — each registers as it comes online and the autorouter connects
   them. Detach a module from its vehicle (or change its role) and the command
   center updates live.

---

## The underlying CoAP light demo

The swarm overlay above is built on the original Nordic CoAP demo, which still
documents the Thread/CoAP mechanics it reuses (`coap_server` / `coap_client`).

## The demo in one picture

```
        ┌──────────────────┐                       ┌──────────────────┐
        │   coap_client     │                       │   coap_server     │
        │  (FTD, "switch")  │                       │  (FTD, "light")   │
        │                   │   CoAP / UDP 5683     │                   │
        │  PUT  /light  ────┼──── over Thread ─────►│  /light  resource │
        │  GET  /provision. │◄──── mesh (IPv6) ─────┤  /provisioning    │
        └──────────────────┘                       └──────────────────┘
                  │                                          │
                  └──────────── same Thread mesh ────────────┘
              (PAN ID 0xABCD, channel 11, shared network key)
```

- The **server** advertises two CoAP resources: `light` and `provisioning`.
- The **client** sends CoAP requests to those resources, either to **one** server
  (unicast) or to **all** servers at once (multicast).
- Because the client starts out not knowing any server's address, it first runs a
  **provisioning handshake** to learn a specific server's mesh-local address before
  it can do *unicast* requests. Multicast requests need no provisioning.

---

## Hardware notes for the XIAO

The samples were written for the **nRF52840 DK**, whose console goes out a UART
wired to the on-board debugger's virtual COM port. The XIAO is different in ways
that matter here:

| Feature | nRF52840 DK | Seeed XIAO nRF52840 |
| --- | --- | --- |
| Console transport | UART → on-board debugger VCOM | **native USB CDC ACM** (no debugger, no UART bridge) |
| Flashing | J-Link / `west flash` | **UF2 bootloader** (double-tap RESET, drag-and-drop) |
| User buttons | 4 (`sw0..sw3`) | **none** (only RESET) |
| LEDs | 4 discrete (`led0..led3`) | 1 **RGB** LED = `led0` (red) / `led1` (green) / `led2` (blue) |

The board's own devicetree (`zephyr/boards/seeed/xiao_ble/`) already routes
`zephyr,console` and `zephyr,shell-uart` to a built-in USB CDC ACM node and enables
the USB device stack at boot. So **no custom USB overlay is needed** — the only
board-specific tweak each sample carries is in its `prj.conf`:

```conf
CONFIG_SHELL=y
CONFIG_SHELL_BACKEND_SERIAL=y
CONFIG_BOOT_DELAY=5000   # hold boot 5 s so the host enumerates the CDC port
                         # before the first log/prompt is emitted
```

`CONFIG_BOOT_DELAY=5000` matters because the XIAO's USB serial port only appears
*after* the firmware boots; without the delay, the first boot banner and the
initial shell prompt are emitted before any terminal is connected and are lost.

---

## The shared CoAP interface

Both samples agree on one small contract, defined in
[`coap_server/interface/coap_server_client_interface.h`](coap_server/interface/coap_server_client_interface.h)
(the client includes the same header from `coap_client/coap_server/interface/`):

```c
#define COAP_PORT 5683                      /* standard CoAP UDP port */

enum light_command {
    THREAD_COAP_UTILS_LIGHT_CMD_OFF    = '0',
    THREAD_COAP_UTILS_LIGHT_CMD_ON     = '1',
    THREAD_COAP_UTILS_LIGHT_CMD_TOGGLE = '2',
};

#define PROVISIONING_URI_PATH "provisioning"
#define LIGHT_URI_PATH        "light"
```

- **`/light`** — a `PUT` whose 1-byte payload is one of the ASCII light commands
  above (`'0'`, `'1'`, `'2'`).
- **`/provisioning`** — a `GET` the client multicasts to discover a server; the
  server replies with its **mesh-local EID** (a stable IPv6 address) so the client
  can address it directly afterward.

---

## How the server works

Source: [`coap_server/src/coap_server.c`](coap_server/src/coap_server.c) and
[`coap_server/src/ot_coap_utils.c`](coap_server/src/ot_coap_utils.c).

1. **Startup** (`main`): initialises a dedicated work queue, the CoAP layer
   (`ot_coap_init`), the DK LED/button library, registers a Thread role-change
   callback, then calls `openthread_run()` to bring the stack up.
2. **CoAP resources** (`ot_coap_init`): registers the `light` and `provisioning`
   resources and a default handler, then `otCoapStart()` begins listening on UDP
   5683.
3. **Light requests** (`light_request_handler` → `on_light_request`): validates the
   request is a non-confirmable `PUT`, reads the 1-byte command, and drives the
   "light" LED on/off/toggle.
4. **Provisioning** is a deliberate, time-boxed pairing step:
   - It is **off by default**. A provisioning `GET` arriving while disabled is
     logged and ignored.
   - Pressing the provisioning button (button 4 on a DK — see
     [limitations](#current-limitations--next-phase) for the XIAO) opens a **5-second
     window** during which the provisioning LED blinks.
   - While open, a provisioning `GET` is answered with the server's mesh-local EID
     (`provisioning_response_send`), and the window then closes.
5. **Connection LED** (`on_thread_state_changed`): lit while the node is a
   child/router/leader, off when disabled/detached.

The server is a **Full Thread Device (FTD)** — it can become a router or the
network leader.

## How the client works

Source: [`coap_client/src/coap_client.c`](coap_client/src/coap_client.c) and
[`coap_client/src/coap_client_utils.c`](coap_client/src/coap_client_utils.c).

The client offers four actions, each dispatched onto its own work queue so the
network stack is never blocked from a callback context:

| Action | Function | CoAP request |
| --- | --- | --- |
| Toggle **one** light (unicast) | `coap_client_toggle_one_light` | `PUT /light` (`'2'`) to the provisioned server address |
| Toggle **all** lights (multicast) | `coap_client_toggle_mesh_lights` | `PUT /light` (`'0'`/`'1'`) to `ff03::1` |
| **Provision** (discover a server) | `coap_client_send_provisioning_request` | `GET /provisioning` multicast → learns server EID |
| Toggle **MTD SED/MED** mode | `coap_client_toggle_minimal_sleepy_end_device` | local only (changes radio sleep behaviour) |

Key details:

- **Multicast address:** `ff03::1` (mesh-local all-nodes), built in
  `multicast_local_addr`. Multicast `PUT /light` reaches every server without any
  prior setup — this is the simplest thing to demonstrate first.
- **Unicast requires provisioning:** `toggle_one_light` refuses to send until a
  server address has been learned (`unique_local_addr`), logging *"Peer address not
  set. Activate 'provisioning' option on the server side."* The address is captured
  in `on_provisioning_reply` from the server's response payload.
- **Connection tracking** (`on_thread_state_changed`): sets `is_connected` and
  drives the connection LED on role changes; requests submitted while disconnected
  are dropped with *"Connection is broken."*
- **Sleepy End Device support:** when built as an MTD with SED enabled
  (`CONFIG_OPENTHREAD_MTD_SED`), the client lowers its poll period during a
  request/response exchange for responsiveness, then restores it. In the **default**
  build the client is an **FTD** (always-on), so this path is inactive.

---

## Thread network parameters

Both images are compiled with the **same** network credentials, so the nodes form
one mesh automatically with no commissioning:

| Parameter | Value | Source |
| --- | --- | --- |
| Network key | `00112233445566778899aabbccddeeff` | `CONFIG_OPENTHREAD_NETWORKKEY` in each `prj.conf` |
| PAN ID | `0xABCD` (43981) | `CONFIG_OPENTHREAD_PANID` (SDK default) |
| Channel | `11` | `CONFIG_OPENTHREAD_CHANNEL` (SDK default) |
| Network name | `ot_zephyr` | `CONFIG_OPENTHREAD_NETWORK_NAME` (SDK default) |
| Device type | FTD (both) | `CONFIG_OPENTHREAD_FTD=y` |
| CoAP port | UDP `5683` | shared interface header |

> The pre-shared network key is fine for a bench demo but is **not secret** — don't
> ship it. Real deployments use Thread commissioning.

---

## LED & button mapping on the XIAO

The DK library indexes LEDs/buttons in devicetree order, so `DK_LED1` = `led0`,
`DK_LED2` = `led1`, `DK_LED3` = `led2`, and `DK_LED4` = a 4th LED that **does not
exist** on the XIAO. The RGB LED is **active-low**.

**Server:**

| Code symbol | DK index | XIAO LED | Meaning |
| --- | --- | --- | --- |
| `OT_CONNECTION_LED` | `DK_LED1` | `led0` (red) | lit while attached to Thread |
| `PROVISIONING_LED` | `DK_LED3` | `led2` (blue) | blinks during the 5 s provisioning window |
| `LIGHT_LED` | `DK_LED4` | *(none)* | the controlled "light" — **no physical LED on XIAO** |

**Client:**

| Code symbol | DK index | XIAO LED | Meaning |
| --- | --- | --- | --- |
| `OT_CONNECTION_LED` | `DK_LED1` | `led0` (red) | lit while attached to Thread |
| `BLE_CONNECTION_LED` | `DK_LED2` | `led1` (green) | BLE NUS link (only in the `BT_NUS` build) |
| `MTD_SED_LED` | `DK_LED3` | `led2` (blue) | MED/SED mode (only meaningful in MTD/SED builds) |

Because the XIAO has **no buttons**, the button-driven actions are not reachable on
hardware yet — see [next phase](#current-limitations--next-phase).

---

## Building

Each sample ships a `build.sh` that sets up the nRF Connect SDK toolchain
environment (installed by the VS Code extension at
`/opt/nordic/ncs/v3.3.1`) and runs `west build` for `xiao_ble`.

```bash
# Server (flash this image to BOTH server boards)
cd coap_server
./build.sh --pristine        # clean build
./build.sh                   # incremental rebuild

# Client
cd ../coap_client
./build.sh --pristine
```

Flags: `--pristine` forces a clean build; `--flash` copies the resulting UF2 to a
mounted XIAO drive (see below). Edit the `NCS_VERSION` / `TC` paths at the top of
`build.sh` if your SDK or toolchain version differs.

Resulting firmware:

- Server: `coap_server/build/coap_server/zephyr/zephyr.uf2`
- Client: `coap_client/build/coap_client/zephyr/zephyr.uf2`

> **Note on iCloud:** this tree lives under an iCloud-synced folder. `build/` is
> git-ignored, but iCloud can create `… 2.ext` duplicate files inside `build/`
> mid-build. They're harmless to flashing (always flash the file in the plain
> `zephyr/` folder), but consider keeping build dirs out of iCloud to avoid races.

## Flashing (UF2 bootloader)

The XIAO has **no debugger** — do **not** use the IDE "Flash" button (its runner
passes a `-i <serial>` argument the UF2 path rejects). Flash by drag-and-drop:

1. **Double-tap RESET** on the XIAO → a `XIAO-BOOT` drive mounts.
2. Copy the sample's `zephyr.uf2` onto that drive (don't delete anything else —
   `CURRENT.UF2`/`INFO_UF2.TXT` are virtual).
3. The drive auto-ejects and the board reboots into the new firmware. A
   "Disk Not Ejected Properly" warning on macOS is expected and harmless.

Flash the **same** `coap_server` UF2 to both server boards; flash the `coap_client`
UF2 to the third board. Or use `./build.sh --flash` with the board's bootloader
drive mounted.

## Serial console & the OpenThread shell

After flashing, open the XIAO's USB CDC serial port (it appears as
`/dev/tty.usbmodem*` on macOS). Any terminal works; e.g.:

```bash
tio /dev/tty.usbmodem*          # or: screen, minicom, the VS Code serial monitor
```

You'll see the boot banner (after the 5 s delay), the sample's startup log
(`Start CoAP-server sample` / `Start CoAP-client sample`), and a live shell prompt:

```
uart:~$
```

The OpenThread shell is available under the `ot` command (`CONFIG_OPENTHREAD_SHELL`).

> The CDC backend uses `SHELL_BACKEND_SERIAL_CHECK_DTR` (on by default for this
> board), so the shell only transmits once your terminal **asserts DTR**. Most
> terminals do; if you see no prompt after the boot delay, enable DTR in your
> terminal.

## Bringing up the network

The nodes share credentials, so they self-form. Useful `ot` shell commands on each
board:

```text
ot state            # disabled / detached / child / router / leader
ot ifconfig         # up/down
ot thread start     # start the Thread protocol (if not already running)
ot channel          # should be 11
ot networkkey       # should match across all boards
ot rloc16           # short address once attached
ot router table     # (on an FTD) routers in the mesh
ot child table      # children attached to this router
ot ipaddr           # this node's IPv6 addresses (incl. mesh-local EID)
```

Expected outcome with all three powered: one board becomes `leader`, the others
become `router`/`child`. Confirm with `ot state` on each and `ot router table` /
`ot child table` on the leader.

---

## Repository layout

```
nRF-swarm/
│  ── swarm + Olympus integration ──
├── proto/                       # shared wire protocol: swarm_protocol.h (C) + swarm_proto.py
├── swarm_node/                  # reconfigurable module firmware (node + --gateway builds)
│   ├── src/                     #   swarm_main / swarm_coap / sensors / ranging / serial_link
│   ├── boards/                  #   data CDC-ACM + modular IMU/GPS chosen-node overlays
│   └── prj.conf, overlay-gateway.conf, Kconfig, build.sh
├── olympus_link/                # command station: registry, localization, autorouter, Olympus push
├── jetson_agent/                # per-agent companion: redundant IP path + Jetson sensor fusion
├── sim/                         # PTY swarm simulator + automated end-to-end test
├── OLYMPUS_INTEGRATION.md       # full design + verification status
│
│  ── underlying CoAP light demo ──
├── coap_server/                 # CoAP "light" server (FTD) — flash to 2 boards
│   ├── src/                     #   coap_server.c, ot_coap_utils.c/.h
│   ├── interface/               #   shared CoAP contract header
│   ├── boards/                  #   per-board overlays/confs (DK/nRF53/etc.)
│   ├── prj.conf                 #   app config (incl. XIAO console/boot tweaks)
│   └── build.sh                 #   toolchain env + west build for xiao_ble
├── coap_client/                 # CoAP "switch" client (FTD) — flash to 1 board
│   ├── src/                     #   coap_client.c, coap_client_utils.c/.h, ble_utils.c
│   ├── coap_server/interface/   #   copy of the shared CoAP contract header
│   ├── extra_conf/              #   optional MTD / multiprotocol-BLE fragments
│   ├── prj.conf
│   └── build.sh
├── blinky/                      # stock Zephyr blinky — XIAO bring-up reference
├── shell_module/                # stock Zephyr shell sample — USB-console reference
└── .vscode/                     # nRF Connect extension app list
```

## Current limitations / next phase

The **swarm overlay + Olympus integration** is now delivered (see the first half of
this README and [`OLYMPUS_INTEGRATION.md`](OLYMPUS_INTEGRATION.md)). The `swarm_node`
firmware replaces the missing XIAO buttons with the `swm/cmd` downlink channel
(identify / set-role / set-mount) driven from the command center, and the whole
host-side pipeline is verified against the simulator. The firmware itself still
needs a hardware build with the nRF Connect SDK toolchain to validate on-device.

For the **original light demo**, an earlier phase delivered clean `xiao_ble` builds
with a working USB serial console. Because the XIAO has no user buttons, those
**button-driven actions are not wired to the hardware**:

- Server: opening the provisioning window (DK button 4) has no trigger.
- Client: the unicast / multicast / provisioning / SED-toggle actions (DK buttons
  1–4) have no trigger.

You can still form and inspect the Thread mesh today via the `ot` shell. A future
phase will expose these actions on the XIAO — e.g. custom shell commands that call
`coap_client_toggle_one_light()`, `coap_client_toggle_mesh_lights()`, and
`coap_client_send_provisioning_request()`, and a server-side trigger for the
provisioning window — replacing the missing buttons.

---

*Based on Nordic Semiconductor's `openthread/coap_server` and `openthread/coap_client`
samples (LicenseRef-Nordic-5-Clause), adapted for the Seeed XIAO nRF52840.*
