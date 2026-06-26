# nRF-testing — Thread CoAP light control on the Seeed XIAO nRF52840

This repo contains the Nordic **OpenThread CoAP** sample pair — `coap_server` and
`coap_client` — ported to run on the **Seeed XIAO nRF52840** (`xiao_ble`) board,
plus the stock `blinky` and `shell_module` samples used as bring-up references.

The two CoAP samples implement a tiny **distributed light-control demo** over a
[Thread](https://www.threadgroup.org/) mesh network: a client node tells one or
many server nodes to turn a "light" on/off/toggle, using
[CoAP](https://datatracker.ietf.org/doc/html/rfc7252) (Constrained Application
Protocol) carried over Thread's IPv6 mesh.

- **Built with:** nRF Connect SDK **v3.3.1** (Zephyr 4.3.99)
- **Target board:** `xiao_ble` (Seeed XIAO nRF52840, non-Sense)
- **Intended fleet:** 2× boards running `coap_server` + 1× board running `coap_client`

---

## Table of contents

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
nRF-testing/
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

This phase delivered **clean `xiao_ble` builds with a working USB serial console**.
Because the XIAO has no user buttons, the original **button-driven actions are not
yet wired to the hardware**:

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
