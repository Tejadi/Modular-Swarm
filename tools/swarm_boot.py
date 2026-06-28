#!/usr/bin/env python3
"""Boot launcher for a swarm Jetson.

On power-up: find the local nRF, decide whether this module is the LEADER
(command station) or a MEMBER, and exec the right stack accordingly. Role comes
from the nRF itself (single source of truth): the leader/gateway nRF sets the
GATEWAY/LEADER flag in its HELLO. An explicit SWARM_ROLE overrides the probe.

  leader  -> python3 -m olympus_link   (mesh -> zenoh/vehicle-api bridge)
  member  -> python3 -m jetson_agent   (brain; auto-detects camera -> scout/executor)

Config comes from the environment (systemd EnvironmentFile=/etc/default/swarm-node):
  SWARM_ROLE        auto | leader | member        (default auto)
  SWARM_NRF_PORT    auto | /dev/ttyACMx            (default auto = first ACM)
  SWARM_KEY_PREFIX  zenoh key prefix               (default ceres)
  SWARM_SINK        rest | zenoh | dryrun          (default rest)
  SWARM_ZENOH_REST  http://host:8000               (default http://localhost:8000)
  SWARM_VEHICLE_API http://host:3001               (default http://localhost:3001)
  SWARM_STATION_LAT/LON  leader anchor (passed through to olympus_link)
"""
import os, sys, time, glob, asyncio, logging

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "proto"))
sys.path.insert(0, os.path.join(REPO, "olympus_link"))
import swarm_proto as sp           # noqa: E402
from serial_link import AsyncSerial  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s swarm_boot: %(message)s")
log = logging.getLogger("swarm_boot")


def find_port(want, wait_s=60):
    """Return the nRF serial device, waiting up to wait_s for it to enumerate."""
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if want and want != "auto":
            if os.path.exists(want):
                return want
        else:
            acm = sorted(glob.glob("/dev/ttyACM*"))
            if acm:
                return acm[0]
        time.sleep(1)
    return None


async def _probe(port, timeout):
    res = {"leader": False, "saw": False}

    def on_payload(payload):
        try:
            msg = sp.decode(payload)
        except Exception:
            return
        if msg is None:
            return
        res["saw"] = True
        if msg.flags & (sp.Flags.GATEWAY | sp.Flags.LEADER):
            res["leader"] = True

    ser = AsyncSerial(port, on_payload)
    ser.start(asyncio.get_running_loop())
    t0 = time.time()
    while time.time() - t0 < timeout and not res["leader"]:
        await asyncio.sleep(0.2)
    ser.close()
    if res["leader"]:
        return "leader"
    return "member" if res["saw"] else None


def detect_role(port, timeout=8.0):
    """leader if the nRF advertises GATEWAY/LEADER, member if it talks but not,
    None if the nRF is silent (caller decides the fallback)."""
    try:
        return asyncio.run(_probe(port, timeout))
    except Exception as e:
        log.warning("role probe failed (%s)", e)
        return None


def main():
    probe_only = "--probe" in sys.argv      # diagnostic: print role + exit, no exec
    role = os.environ.get("SWARM_ROLE", "auto").lower()
    port = find_port(os.environ.get("SWARM_NRF_PORT", "auto"), wait_s=10 if probe_only else 60)
    if not port:
        log.error("no nRF serial port (/dev/ttyACM*) appeared; aborting")
        sys.exit(1)
    log.info("nRF port: %s", port)

    if role == "auto":
        detected = detect_role(port)
        role = detected or "member"
        log.info("role probe -> %s%s", role, "" if detected else " (nRF silent; defaulting to member)")
    else:
        log.info("role (configured): %s", role)

    if probe_only:
        print(f"ROLE={role} PORT={port}")
        return

    prefix = os.environ.get("SWARM_KEY_PREFIX", "ceres")
    sink = os.environ.get("SWARM_SINK", "rest")
    zenoh = os.environ.get("SWARM_ZENOH_REST", "http://localhost:8000")
    vapi = os.environ.get("SWARM_VEHICLE_API", "http://localhost:3001")

    if role == "leader":
        module = "olympus_link"
    else:
        module = "jetson_agent"
        os.environ.setdefault("SWARM_NRF_PORT", port)   # jetson_agent reads this too

    cmd = [sys.executable, "-m", module, "--port", port,
           "--prefix", prefix, "--sink", sink,
           "--zenoh-rest", zenoh, "--vehicle-api", vapi]
    log.info("exec %s stack: %s", role, " ".join(cmd))
    os.chdir(REPO)
    os.execvpe(cmd[0], cmd, os.environ.copy())


if __name__ == "__main__":
    main()
