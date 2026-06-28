#!/usr/bin/env python3
"""
Lightweight swarm command-station web view.

Runs ON the Jetson, reads the leader nRF's USB-CDC serial (swarm_proto binary),
and serves a live Leaflet map of every node it hears. No Docker / Olympus needed
— open http://<jetson-ip>:8000 from a laptop on the same network.

The full Olympus React/Cesium dashboard is the richer option; this is the fast
"see it works" view.
"""
import os, sys, time, json, fcntl, struct, termios, tty, select, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Find swarm_proto.py: alongside the repo's proto/, the Jetson staging dir, or here.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, '..', 'proto'), '/home/aidan', _HERE,
           os.environ.get('SWARM_PROTO_DIR', '')):
    if _p and os.path.exists(os.path.join(_p, 'swarm_proto.py')):
        sys.path.insert(0, _p)
        break
import swarm_proto as sp

PORT_DEV = os.environ.get('SWARM_PORT', '/dev/ttyACM0')
HTTP_PORT = int(os.environ.get('HTTP_PORT', '8000'))

POS_SOURCE = {0: 'NONE', 1: 'GPS', 2: 'RANGED', 3: 'IMU', 4: 'FUSED'}
EKF_BITS = [(0x01, 'GPS'), (0x02, 'IMU'), (0x04, 'VIO'), (0x08, 'PEER'), (0x10, 'CONVERGED')]

state = {}          # eui_str -> dict
state_lock = threading.Lock()


def flags_str(f):
    return '+'.join(name for bit, name in EKF_BITS if f & bit) or 'none'


def reader_loop():
    """Open the CDC with DTR asserted, decode frames, update `state`. Reconnects."""
    while True:
        fd = None
        try:
            fd = os.open(PORT_DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            a = termios.tcgetattr(fd)
            a[2] |= (termios.CLOCAL | termios.CREAD)
            termios.tcsetattr(fd, termios.TCSANOW, a)
            tty.setraw(fd)
            fcntl.ioctl(fd, 0x5416, struct.pack('I', 0x002 | 0x004))  # DTR|RTS
            reader = sp.SerialReader()
            while True:
                r, _, _ = select.select([fd], [], [], 1.0)
                if not r:
                    continue
                data = os.read(fd, 8192)
                if not data:
                    continue
                for payload in reader.feed(data):
                    m = sp.decode(payload)
                    if m is None:
                        continue
                    handle_msg(m)
        except Exception as e:
            time.sleep(1.0)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            time.sleep(0.5)


def handle_msg(m):
    eui = None
    for attr in ('eui', 'src', 'src_eui', 'node'):
        if hasattr(m, attr):
            try:
                eui = sp.eui_str(getattr(m, attr))
            except Exception:
                eui = str(getattr(m, attr))
            break
    if eui is None:
        return
    with state_lock:
        n = state.setdefault(eui, {'eui': eui})
        n['last_seen'] = time.time()
        if isinstance(m, sp.Hello):
            try:
                n['sensors'] = sp.sensor_list(m.sensors)
            except Exception:
                n['sensors'] = []
            n['role'] = getattr(m, 'role', 0)
            n['caps'] = getattr(m, 'capabilities', 0)
        elif isinstance(m, sp.Telemetry):
            n['pos_source'] = POS_SOURCE.get(m.pos_source, str(m.pos_source))
            n['lat'] = round(m.lat, 7)
            n['lon'] = round(m.lon, 7)
            n['has_fix'] = (m.pos_source != 0)
            hdg = getattr(m, 'heading', None)
            n['heading'] = round(hdg, 1) if hdg is not None else None
            n['ekf_flags'] = flags_str(getattr(m, 'ekf_flags', 0))
            n['battery'] = getattr(m, 'battery_pct', None)
            vn = getattr(m, 'vel_n', 0.0) or 0.0
            ve = getattr(m, 'vel_e', 0.0) or 0.0
            n['speed_mps'] = round((vn * vn + ve * ve) ** 0.5, 2)


def snapshot():
    now = time.time()
    with state_lock:
        out = []
        for n in state.values():
            d = dict(n)
            d['age'] = round(now - n.get('last_seen', now), 1)
            out.append(d)
    return out


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>nRF Swarm — command view</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<link rel=stylesheet href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 body{margin:0;font:14px system-ui,sans-serif}
 #map{height:100vh}
 #panel{position:absolute;top:10px;right:10px;z-index:1000;background:#111d;color:#eee;
   padding:10px 12px;border-radius:8px;max-width:320px;box-shadow:0 2px 8px #0008}
 #panel h3{margin:0 0 6px}
 .node{border-top:1px solid #444;padding:6px 0}
 .k{color:#8cf} .dr{color:#fc6} .none{color:#f88}
</style></head><body>
<div id=map></div>
<div id=panel><h3>nRF Swarm</h3><div id=nodes>waiting for telemetry…</div></div>
<script>
const map=L.map('map').setView([39.9526,-75.1652],13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
let markers={},centered=false;
async function tick(){
 let r=await fetch('/api/state'); let nodes=await r.json();
 let html='';
 for(const n of nodes){
  const cls = n.pos_source==='FUSED'?(n.ekf_flags&&n.ekf_flags.includes('GPS')?'k':'dr'):'none';
  html+=`<div class=node><b>${n.eui||'?'}</b><br>`+
   `pos: <span class=${cls}>${n.pos_source||'—'}</span> `+
   `(${n.ekf_flags||'—'})<br>`+
   `sensors: ${(n.sensors||[]).join(',')||'—'}<br>`+
   `lat ${n.lat??'—'}, lon ${n.lon??'—'}<br>`+
   `hdg ${n.heading??'—'}°  spd ${n.speed_mps??'—'} m/s  batt ${n.battery??'—'}%<br>`+
   `<small>age ${n.age}s</small></div>`;
  if(n.has_fix && n.lat!=null){
   if(!markers[n.eui]) markers[n.eui]=L.marker([n.lat,n.lon]).addTo(map);
   markers[n.eui].setLatLng([n.lat,n.lon]).bindPopup(`${n.eui}<br>${n.pos_source} ${n.ekf_flags}`);
   if(!centered){map.setView([n.lat,n.lon],15);centered=true;}
  }
 }
 document.getElementById('nodes').innerHTML = html||'waiting for telemetry…';
}
setInterval(tick,1000); tick();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith('/api/state'):
            body = json.dumps(snapshot()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)


if __name__ == '__main__':
    threading.Thread(target=reader_loop, daemon=True).start()
    srv = ThreadingHTTPServer(('0.0.0.0', HTTP_PORT), H)
    print('serving on 0.0.0.0:%d, reading %s' % (HTTP_PORT, PORT_DEV), flush=True)
    srv.serve_forever()
