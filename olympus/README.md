# OLYMPUS OS

**Autonomous Fleet Command & Control**

A multi-vertical operating system for autonomous drone swarms built on the Scout-Executor paradigm with a bilevel communications architecture. OLYMPUS OS is the core engine powering all ELARIS fleet operations — switch domains through instance configuration, not code. **Default vertical: ATHENA (Tactical ISR & Defense).**

---

## ELARIS Platform Hierarchy

```
ELARIS (Company)
└── OLYMPUS OS (Platform Engine)
    ├── Core Modules
    │   ├── Vehicle API    — REST + WebSocket gateway (Rust/Axum)
    │   ├── Bridge         — Zenoh + LoRa + ELRS + MAVLink networking (Rust)
    │   ├── Brain          — Swarm AI + mission planning (Python)
    │   └── Dashboard      — Overwatch tactical UI (React/Cesium)
    │
    └── Instances
        ├── CERES    — Precision Agriculture
        ├── ATHENA   — Tactical ISR & Defense
        ├── VULCAN   — Industrial Inspection
        └── HERMES   — Search & Rescue
```

---

## Instances

Each instance configures OLYMPUS OS for a specific operational domain. An instance defines: **Models** (detection types), **Executors** (task types + routing), **Config** (operating area, fleet defaults), **UI** (branding, advisor persona), and **Integrations** (external system API stubs).

### CERES — Precision Agriculture

| | |
|---|---|
| **Mission ID** | `FIELD-OPS-001` |
| **Operating Area** | 500m farm, Salinas Valley CA |
| **Zones** | Fields (Lettuce, Spinach, Kale, Arugula) |
| **Detections** | Weed, Pest, Disease, Nutrient Deficiency, Irrigation Leak, Crop Stress |
| **Executor Actions** | Spray, Fertilize, Seed, Inspect, Sample |
| **Payload** | Tank Level — 10L liquid |
| **AI Advisor** | Farm Advisor |
| **Integrations** | John Deere Operations Center, Climate Corp FieldView, AgLeader SMS |

**Use cases:** Row crop spot-spraying, orchard canopy stress detection, irrigation leak mapping, soil moisture surveys.

### ATHENA — Tactical ISR & Defense

| | |
|---|---|
| **Mission ID** | `RECON-001` |
| **Operating Area** | 1000m grid, Los Angeles CA |
| **Zones** | Sectors A1/A2/B1/B2 (Low/Med/High threat) |
| **Detections** | Hostile Activity, Vehicle, Personnel, IED Suspected, Structural Change, Thermal Anomaly |
| **Executor Actions** | Investigate, Photograph, Mark, Relay |
| **Payload** | Payload Bay — marker/sensor equipment |
| **AI Advisor** | Tactical Advisor |
| **Integrations** | Link 16, Team Awareness Kit (TAK), Cursor on Target (CoT), NATO STANAG 4586 |

**Use cases:** Border surveillance, base perimeter monitoring, post-event recon, military training exercises.

### VULCAN — Industrial Inspection

| | |
|---|---|
| **Mission ID** | `INSP-001` |
| **Operating Area** | 300m facility, Houston TX |
| **Zones** | Tank Farm, Pipe Rack, Cooling Tower, Flare Stack |
| **Detections** | Structural Crack, Corrosion, Thermal Anomaly, Leak, Vegetation Encroachment, Surface Deformation |
| **Executor Actions** | Photograph, Thermal Scan, Measure, Sample |
| **Payload** | Sensor Bay — sensor/sample equipment |
| **AI Advisor** | Inspection Advisor |
| **Integrations** | SAP Plant Maintenance, IBM Maximo, OSIsoft PI, GIS (ArcGIS/QGIS) |

**Use cases:** Pipeline corrosion surveys, flare stack inspections, transmission line checks, solar panel thermal scans, bridge structural monitoring, mine tailings dam tracking.

### HERMES — Search & Rescue

| | |
|---|---|
| **Mission ID** | `SAR-001` |
| **Operating Area** | 2000m grid, San Francisco CA |
| **Zones** | Grid A1/A2/B1/B2 (Forest, Coastal, Urban, Open) |
| **Detections** | Person Detected, Thermal Signature, Debris Field, Vehicle Wreckage, Signal Detected |
| **Executor Actions** | Investigate, Drop Supplies, Mark Location, Relay Comms |
| **Payload** | Supply Payload — 5.0 kg emergency supplies |
| **AI Advisor** | SAR Coordinator |
| **Integrations** | D4H Incident Management, FEMA IPAWS, CAP ESAR, ATAK |

**Use cases:** Wilderness missing person search, maritime survivor detection, post-disaster debris field scanning, urban collapse grid search with thermal + visual.

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           OLYMPUS OS Architecture                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────┐     ┌───────────┐     ┌───────────┐                             │
│  │   Scout   │     │ Executor  │     │ Executor  │                             │
│  │   Drone   │     │  Drone A  │     │  Drone B  │                             │
│  │ (Jetson   │     │ (Jetson   │     │ (Jetson   │                             │
│  │  Orin NX) │     │  Orin     │     │  Orin     │                             │
│  │           │     │  Nano)    │     │  Nano)    │                             │
│  └─────┬─────┘     └─────┬─────┘     └─────┬─────┘                             │
│        │ MAVLink          │ MAVLink         │                                   │
│        │ (TELEM2)         │ (TELEM2)        │                                   │
│  ┌─────┴─────┐     ┌─────┴─────┐     ┌─────┴─────┐                             │
│  │ Pixhawk   │     │ Pixhawk   │     │ Pixhawk   │                             │
│  │    6C     │     │    6C     │     │    6C     │                             │
│  └─────┬─────┘     └─────┬─────┘     └─────┴─────┘                             │
│        │ TELEM1           │                                                     │
│        │                  │                                                     │
│  ┌─────┴──────────────────┴─────────────────────────────────┐                   │
│  │                 Communication Stack                       │                   │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐             │                   │
│  │  │  LoRa    │   │  ELRS    │   │  Zenoh   │             │                   │
│  │  │ 915MHz   │   │ 2.4GHz   │   │ TCP/WS   │             │                   │
│  │  │ Mesh     │   │ MAVLink  │   │ (WiFi/5G)│             │                   │
│  │  │ PRIMARY  │   │ SECONDARY│   │ TERTIARY │             │                   │
│  │  └──────────┘   └──────────┘   └──────────┘             │                   │
│  └──────────────────────┬───────────────────────────────────┘                   │
│                         │                                                       │
│  ┌──────────────────────┴───────────────────────────────────┐                   │
│  │                    Base Station                           │                   │
│  │                                                           │                   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐               │                   │
│  │  │  Rust    │  │  Python  │  │  Zenoh   │               │                   │
│  │  │  Bridge  │  │  Brain   │  │  Router  │               │                   │
│  │  └────┬─────┘  └────┬─────┘  └──────────┘               │                   │
│  │       │              │                                    │                   │
│  │  ┌────┴──────────────┴─────┐  ┌──────────────────────┐  │                   │
│  │  │  Vehicle API Gateway    │  │   Overwatch UI        │  │                   │
│  │  │  (Axum REST+WS :3001)  │  │  (React/Cesium :3000) │  │                   │
│  │  └─────────────────────────┘  └──────────────────────┘  │                   │
│  └──────────────────────────────────────────────────────────┘                   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Scout-Executor Paradigm

OLYMPUS separates drones into two distinct roles:

**Scouts** scan terrain using camera + ML inference, generate detections, and create tasks for executors. They fly at 35m altitude in systematic coverage patterns optimized by information gain.

**Executors** bid on tasks via CBBA auction, transit to targets, and perform domain-specific actions (spray, photograph, drop supplies, etc.). They fly at 50m transit / 5m working altitude.

```
Scout Coverage Loop:
  Partition field → Generate coverage path → Rank by information gain →
  Navigate waypoint → Run inference → Detection? → Publish detection →
  Create task → CBBA auction → Executor assigned → Loop

Executor Task Loop:
  Receive task announcement → Calculate bid (distance, battery, payload, capability) →
  Submit bid → Win auction? → Check escalation level →
  APPROVE_REQUIRED? → Wait for human / timeout → Transit → Execute → Return
```

### Bilevel Autonomy Model

The swarm operates with two layers of authority. The tactical layer (swarm) handles real-time decisions autonomously. The strategic layer (human) sets objectives and intervenes on high-risk actions.

```
┌─────────────────────────────────────────────────────────┐
│  STRATEGIC LAYER (Human / Base Station)                 │
│  - Mission objectives, rules of engagement, geo-fences  │
│  - Approve/deny escalated actions                       │
│  - Override any tactical decision                       │
└──────────────┬──────────────────────────────────────────┘
               │ Directives ↓    ↑ Escalations
┌──────────────▼──────────────────────────────────────────┐
│  TACTICAL LAYER (Swarm / Autonomous)                    │
│  - CBBA task allocation (runs without human)            │
│  - ORCA collision avoidance (runs without human)        │
│  - Detection → Task pipeline (runs without human)       │
│  - Escalate when: high-cost, ambiguous, novel           │
└─────────────────────────────────────────────────────────┘
```

**Escalation levels** (computed per-task by the EscalationEngine):

| Level | Behavior | Example |
|-------|----------|---------|
| `AUTO` | Execute immediately, no notification | Routine weed spray, confidence > 0.9 |
| `NOTIFY` | Execute and inform operator | Moderate-priority inspect task |
| `APPROVE_REQUIRED` | Wait for human approval (with timeout) | High-cost payload expenditure, novel detection type |
| `EMERGENCY` | Immediate human attention required | IED suspected, swarm commitment > 50% |

Thresholds are configurable per vertical — CERES is more autonomous (farming is lower risk), ATHENA requires more human oversight (defense operations).

When comms to the base station are lost for > 60s, the swarm enters **autonomous consensus mode**: a Raft-like leader election among connected drones selects a leader who assumes escalation-approval authority within pre-defined rules of engagement. Decisions are synced back to the base when comms restore.

---

## Communication Stack

### Bilevel Communications Architecture

Communication is split into two semantic layers with distinct reliability guarantees:

```
                         BASE STATION
  ┌──────────────────────────────────────────────────────┐
  │  Vehicle API    Dashboard    AI Agent   Aggregator   │
  │  (Rust/Axum)   (Cesium)    (Drift Mon) (SwarmNet)   │
  │       └────────────┴────────────┴───────────┘        │
  │                     Zenoh Router                      │
  └─────────────────────────┬────────────────────────────┘
                            │
   ══════════════════════════════════════════════════════
    STRATEGIC LAYER (TCP semantics — reliable, ACK'd)
    ELRS 2.4GHz + WiFi/Zenoh
    Model weights ↓  Commands ↓↑  Recall/Redeploy ↓
   ══════════════════════════════════════════════════════
                            │
       ┌────────────────────┼────────────────────┐
       │                    │                    │
   ┌───┴────┐          ┌───┴────┐          ┌────┴───┐
   │Scout A │          │Scout B │          │Exec C  │
   │LocalML │          │LocalML │          │CBBA    │
   │Camera  │          │Camera  │          │Payload │
   └───┬────┘          └───┬────┘          └────┬───┘
       │                    │                    │
   ══════════════════════════════════════════════════════
    TACTICAL LAYER (UDP semantics — fire-and-forget)
    LoRa 915MHz Mesh (Meshtastic / Heltec V3)
    Intermediate features ↔  Map deltas ↔  Telemetry ↔
    Port 67=telem  Port 0x44=detections  Port 0x45=features
   ══════════════════════════════════════════════════════
```

**Key rules:**
- **Tactical layer**: NO model weights, NO commands. Only intermediate features (~152B), compact telemetry (~30B), detection summaries (~25B), belief map deltas. Fire-and-forget (UDP semantics).
- **Strategic layer**: Model weights, recall/redeploy commands, escalation responses, full telemetry archive. Reliable delivery (TCP semantics).
- **AI Agent** monitors concept drift → recalls fleet (strategic) → pushes new model (strategic) → redeploys.

### Three-Tier Radio Architecture

Drones communicate via **LoRa radio mesh** as the primary link — not WiFi. Three independent communication channels provide redundancy:

| Tier | Technology | Range | Bandwidth | Layer | Role |
|------|-----------|-------|-----------|-------|------|
| **Primary** | LoRa 915MHz (Heltec V3 / Meshtastic) | ~10km LOS | ~1 kbps | Tactical | P2P features, telemetry, detections |
| **Secondary** | ELRS 2.4GHz (EP1 RX / MAVLink mode) | ~2km | ~50 kbps | Strategic | MAVLink telemetry + RC + commands |
| **Tertiary** | WiFi 6 / 5G (Zenoh TCP/WS) | ~200m | High | Strategic | Model weights, dashboard, bulk sync |

### ELRS MAVLink Mode

The ExpressLRS link runs in **MAVLink mode** (ELRS 3.5+), providing bidirectional MAVLink telemetry and embedded RC channels over a single 2.4GHz radio link. No separate RC receiver needed.

```
Jetson ──UART──→ Pixhawk TELEM2 ──MAVLink Router──→ Pixhawk TELEM1 ──→ EP1 RX
                  (460800 baud)   (auto-forwards)    (460800 baud)     │
                                                                   ELRS 2.4GHz
                                                                       │
                                                              RadioMaster Pocket TX
                                                                       │
                                                              Base Station (USB serial)
                                                                       │
                                                              Rust Bridge ──→ Zenoh
```

### Detection Mesh Transmission

Detections travel from Jetson to base via MAVLink TUNNEL messages through the Pixhawk. This works even without WiFi:

```
Jetson (camera inference)
  │
  ├─ Detection object (~100 bytes)
  │   encoded as MAVLink TUNNEL msg (payload_type=0x4F)
  │
  ▼
Pixhawk 6C (TELEM2, 460800 baud)
  │
  │  PX4 auto-routes MAVLink between all TELEM ports (MAV_X_FORWARD=1)
  │
  ▼
EP1 RX (TELEM1) ──ELRS 2.4GHz──→ RadioMaster ──USB──→ Base Station Bridge
                                                           │
                                                    decode TUNNEL → Detection
                                                    publish to Zenoh
                                                    forward over LoRa (port 0x44)
```

**TUNNEL binary format** (16–128 bytes):
```
[0x4F magic][det_type:u8][confidence:u8][severity:u8]
[lat:i32 LE][lon:i32 LE][alt:u16 LE]
[id_len:u8][detected_by:bytes][timestamp:u32 LE]
```

High-confidence detections (>= 0.7) are also forwarded over LoRa on port `0x44` for additional redundancy.

### Comms-Degraded Operating Modes

The `CommsMonitor` tracks link quality across all channels and automatically transitions between operating modes with hysteresis to prevent flapping:

| Mode | Condition | Behavior |
|------|-----------|----------|
| `FULL_COMMS` | WiFi + LoRa/ELRS active | Normal operation — full telemetry, model updates, bulk sync |
| `DEGRADED` | WiFi down, LoRa/ELRS only | Reduced telemetry rate, batch detections, text-only commands |
| `MINIMAL` | Only LoRa | Critical telemetry only (position + battery), no model updates |
| `DENIED` | All comms lost | Execute last known waypoints, RTL at battery < 30%, buffer detections locally |

Detections made during comms blackout are stored in a **SQLite detection archive** (max 50,000 entries) on the Jetson. When comms restore, the `DetectionSyncer` bulk-uploads buffered detections to the base station with deduplication.

---

## Navigation & GPS-Denied Operations

### GPS Health Monitoring

The `NavigationManager` continuously monitors GPS quality (satellite count, HDOP, fix type) and switches position sources when GPS degrades:

| Condition | Position Source | Behavior |
|-----------|----------------|----------|
| ≥ 6 sats, HDOP < 4.0, 3D fix | `GPS` | Normal GPS navigation |
| ≥ 4 sats, HDOP < 8.0, 2D fix + VIO tracking | `GPS_VIO_FUSED` | GPS primary, VIO for smoothing |
| GPS lost + VIO tracking | `VIO` | Visual-Inertial Odometry from Jetson camera + IMU |
| GPS lost + VIO lost | `DEAD_RECKONING` | Last known position + IMU integration |

VIO deltas are integrated from the last known GPS position using meters-to-degrees conversion. Position source changes are published on telemetry with a `position_source` flag so downstream consumers know the origin.

---

## Algorithms

### CBBA — Communication-Aware Consensus-Based Bundle Algorithm

Decentralized multi-task allocation with bandwidth awareness.

- Each executor greedily builds a task bundle, then resolves conflicts with peers via Zenoh
- Multi-factor scoring: distance (35%), urgency (20%), battery (15%), payload (15%), capability (15%)
- Diminishing marginal returns for multi-task bundles
- **Heterogeneous capabilities**: executors declare their capabilities (e.g., `[spray, fertilize]` vs `[photograph, relay]`). Tasks not matching an executor's capabilities score 0
- **Bandwidth penalty**: tasks requiring high-bandwidth coordination (spray, seed, fertilize, drop_supplies) are penalized when operating in degraded comms
- **Agent censoring**: when channel load > 70%, bid scores are reduced to limit message count during auction
- Converges in 3-5 rounds; falls back to simple auction for single executor

### ORCA — Optimal Reciprocal Collision Avoidance

Real-time velocity-level deconfliction, O(n) per drone per timestep.

- Computes velocity obstacles from all neighbors within 50m radius
- Selects closest safe velocity to preferred velocity
- Altitude stratification enforced: scouts 35m, executors transit 50m, work 5m
- Emergency altitude offset (+/-15m) if horizontal separation < 10m

### Information-Gain Waypoint Planning

Scouts use a **Bayesian occupancy grid** (BeliefMap) to prioritize unvisited and high-yield areas:

- ~5m grid cells track per-cell belief about detection probability
- Unvisited cells have high uncertainty (Shannon entropy = 1.0) → high information gain
- Cells with confirmed detections get elevated belief → increased revisit priority
- Cells visited with no detection get decreased belief → deprioritized
- Waypoints are ranked by: `0.7 * information_gain + 0.3 * proximity_score`
- Belief map updates continuously during scanning (every navigation step + every detection)

### Swarm Consensus (Comms-Denied)

When the base station is unreachable for > 60s, drones form an autonomous decision-making cluster:

1. **Leader Election** — Raft-like protocol: drones start as followers, random election timeout triggers candidacy, majority vote elects leader
2. **Leader Authority** — Elected leader can approve/deny escalation requests within pre-defined Rules of Engagement (max tasks, allowed task types, max resource commitment)
3. **Decision Logging** — All autonomous decisions are recorded with timestamps and rationale
4. **Sync on Reconnect** — When base comms restore, all decisions are synced back for human review
5. **Autonomous Timeout** — If no base contact for > `autonomous_timeout_s` (default 600s), leader orders RTL

### Adaptive Telemetry Rates

| Drone Status | Telemetry Rate |
|--------------|----------------|
| Idle / Charging | 1 Hz |
| Scanning | 3 Hz |
| Transiting / Executing / Returning | 5 Hz (base rate) |
| Emergency | 10 Hz |

### SwarmNet — Centrally-Trained, Locally-Inferred Collaborative Learning

Drones run inference on-device and send detection results (~100 bytes each) to the base station over LoRa. The base station collects detections from **all** drones, retrains a global model, and pushes updated weights back to the fleet. No model weights are ever exchanged drone-to-drone. Intermediate features ARE shared peer-to-peer on the tactical layer.

```
Drone (field)                          Base Station
─────────────                          ────────────
Run inference locally           →
Send detections via LoRa (~100B) →      Collect detections from all drones
Share features P2P via LoRa     ↔       Train global model
  (tactical, ~152B)                     AI Agent monitors drift (Page-Hinkley)
                                        Drift detected? → Recall → Retrain → Redeploy
Load updated weights            ←      Push global model via Zenoh (~2KB)
  OR                            ←      Push soft labels via Zenoh (~200B)
```

**Components:**

- **LocalTrainer** (per-drone): Lightweight neural network running on-device (online SGD, 256-sample buffer). Runs inference during scans, trains on its own detections to stay warm between global pushes. Supports knowledge distillation from soft labels (temperature-scaled KL divergence). Receives peer intermediate features via tactical layer for P2P learning.
- **CentralAggregator** (base station): Subscribes to `olympus/detection/**`, trains a global model from all fleet detections (2048-sample buffer). Monitors for concept drift via the Page-Hinkley test — if accuracy drops > 10%, triggers immediate retrain
- **SwarmNetController** (per-drone): Receives global model weights or soft labels from central, loads into local trainer. Publishes model metadata (accuracy, version) for monitoring
- **AIAgent** (base station): Strategic-layer agent that monitors drift, recalls scouts via `RECALL_FOR_UPDATE`, pushes converged model, and redeploys scouts via `REDEPLOY`. Logs all actions to SQLite audit trail. Exposes status via Vehicle API `/api/v1/ai-agent/` endpoints.

**Adaptive features:**

| Feature | Description |
|---------|-------------|
| **Concept drift detection** | Page-Hinkley test monitors rolling accuracy; fires immediate retrain when accuracy drops > threshold |
| **AI Agent recall/retrain/redeploy** | Automated cycle: recall scouts (TCP) → retrain global model → push weights → redeploy scouts |
| **Peer feature sharing** | Scouts share intermediate features (~152B) over LoRa mesh (tactical layer) for immediate P2P distillation — neighbors improve without waiting for base |
| **Soft-label distillation** | When bandwidth is limited, base sends class probabilities (~200B) instead of full weights (~2KB). Drones learn via KL-divergence distillation |
| **Bandwidth-adaptive mode** | Automatically switches between full weight push (WiFi) and soft-label push (LoRa-only) based on comms mode |

### AI Advisor — Open-Source LLM

The AI Advisor provides a natural-language chat interface for operators to query fleet status, analyze detections, plan strategy, and assess model retraining needs. It runs **entirely locally** using an open-source LLM via [Ollama](https://ollama.com) — no cloud API keys required.

```
Dashboard (AiChat.jsx)
    │
    ▼
Vehicle API (Rust proxy, /api/v1/advisor/chat)
    │
    ▼
Advisor Service (Python/aiohttp, port 8080)
    ├── SwarmContextGatherer → fetches live fleet/detection/model data from Vehicle API
    ├── RetrainingAssessor → evaluates drift status, accuracy trends, retrain recommendations
    └── OllamaClient → sends context-enriched prompt to local LLM
          │
          ▼
      Ollama (localhost:11434, default: llama3.1:8b)
```

**Capabilities:**

| Capability | What the advisor does |
|------------|----------------------|
| **Data Collection** | Summarizes what scouts have found, detection counts by type, confidence statistics |
| **Strategic Planning** | Recommends deployment changes based on fleet positions, battery levels, pending tasks |
| **Retraining Assessment** | Monitors drift detection, accuracy trends, recommends when to trigger model retrain |
| **Fleet Status** | Reports on vehicle positions, battery, task assignments, trust tiers |
| **Vertical Awareness** | Adapts persona and terminology per instance (ATHENA=tactical, CERES=agricultural, etc.) |

Before each LLM call, the advisor injects a **live operations context** block into the system prompt with fleet telemetry, detection summaries, and SwarmNet model metrics. The LLM reasons over this context to give grounded, data-driven responses.

**Setup:**

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull the default model
ollama pull llama3.1:8b

# Start the advisor
cd olympus/brain && pip install -e .
olympus-advisor
```

Or via Docker Compose (Ollama starts automatically):
```bash
docker compose up --build
docker exec olympus-ollama ollama pull llama3.1:8b
```

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.1:8b` | LLM model name (any Ollama model) |
| `VEHICLE_API_URL_INTERNAL` | `http://localhost:3001` | Vehicle API for context fetching |

---

## Project Structure

```
olympus/
├── dashboard/                  # Overwatch UI (React 18 + Cesium + Tailwind)
│   └── src/
│       ├── instances/          # Instance definitions
│       │   ├── index.js        # Instance loader + useInstance() hook
│       │   ├── ceres/          # Agriculture instance
│       │   ├── athena/         # Defense instance
│       │   ├── vulcan/         # Industrial instance
│       │   └── hermes/         # SAR instance
│       ├── components/         # Shared UI components
│       ├── store/              # Zustand state management
│       ├── hooks/              # Zenoh polling, custom hooks
│       └── utils/              # Detection/environment generators
│
├── brain/                      # Swarm intelligence (Python 3.11)
│   └── olympus_brain/
│       ├── protocol.py         # Wire protocol, data models, Zenoh keys
│       ├── node.py             # Base drone node (Zenoh pub/sub)
│       ├── scout.py            # Scout drone controller + BeliefMap
│       ├── executor.py         # Executor drone controller + state machine
│       ├── cbba.py             # CA-CBBA task allocation (bandwidth-aware)
│       ├── orca.py             # ORCA collision avoidance
│       ├── swarmnet.py         # SwarmNet collaborative learning + drift detection
│       ├── escalation.py       # Escalation scoring engine (per-vertical thresholds)
│       ├── swarm_consensus.py  # Raft-like leader election for comms-denied
│       ├── navigation.py       # GPS/VIO failover navigation manager
│       ├── comms_monitor.py    # Link quality tracking + operating modes
│       ├── detection_buffer.py # SQLite detection archive (50K entries, queryable)
│       ├── ai_agent.py         # Strategic AI agent: drift → recall → retrain → redeploy
│       ├── athena.py           # ATHENA defense: ThreatClassifier, ROEEnforcer, NATO symbols
│       ├── cli_register.py     # Platform registration CLI (olympus-register)
│       ├── mavlink_tunnel.py   # MAVLink TUNNEL encode/decode for detections
│       ├── camera_detector.py  # Jetson camera inference pipeline
│       ├── partitioning.py     # Field partitioning + coverage path planning
│       ├── mission_planner.py  # Mission coordination + feature pipeline
│       ├── mission_profile.py  # Backend instance profiles (default: athena)
│       ├── advisor.py          # AI advisor: Ollama LLM + swarm context injection
│       └── api.py              # Advisor HTTP API (aiohttp, port 8080)
│
├── bridge/                     # Low-level networking (Rust + Tokio)
│   └── src/
│       ├── bridge.rs           # Main orchestrator + detection routing
│       ├── zenoh_handler.rs    # Zenoh pub/sub
│       ├── lora.rs             # LoRa/Meshtastic bridge (primary comms)
│       ├── elrs.rs             # ExpressLRS/CRSF bridge (2.4GHz)
│       ├── mavlink_handler.rs  # MAVLink/PX4 + TUNNEL decoding
│       ├── protocol.rs         # Wire protocol definitions
│       ├── telemetry.rs        # Telemetry aggregation
│       ├── config.rs           # Bridge configuration
│       ├── lib.rs              # Library entry point
│       └── main.rs             # Binary entry point
│
├── vehicle-api/                # REST + WebSocket gateway (Rust/Axum)
│   ├── openapi.yaml            # OpenAPI 3.1 spec
│   └── src/
│       ├── routes/             # Fleet, mission, metrics, AI agent, webhooks
│       ├── metrics.rs          # SQLite telemetry/detection store
│       ├── ws/                 # WebSocket telemetry stream
│       └── middleware/         # Auth (SHA-256 keys), rate limiting
│
├── docker-compose.yml
├── zenoh.json5
└── .env.example
```

### Instance File Structure

Each instance directory follows the same layout:

```
instances/<name>/
├── models.js         # Detection types + generation parameters
├── executors.js      # Task types + detection-to-task routing
├── config.js         # Operating area, fleet defaults, environment
├── ui.js             # Name, advisor persona, UI labels, logo SVG
├── integrations.js   # External system API stubs
└── index.js          # Assembles all exports
```

---

## Quick Start

### Docker Compose (Full Stack)

```bash
cd olympus

cp .env.example .env
# Edit .env: set OLYMPUS_API_KEY, optionally change OLYMPUS_INSTANCE

docker compose up --build

# Pull the LLM model for the advisor (first time only)
docker exec olympus-ollama ollama pull llama3.1:8b

# Dashboard:    http://localhost:3000
# Vehicle API:  http://localhost:3001
# Zenoh REST:   http://localhost:8000
# Advisor:      http://localhost:8080
```

With brain simulation (synthetic telemetry):

```bash
docker compose --profile simulation up --build
```

### Dashboard Only (Development)

```bash
cd olympus/dashboard
npm install
npm start
# http://localhost:3000
```

The dashboard runs standalone with simulated telemetry.

### Switch Instance

Via environment variable:

```bash
REACT_APP_INSTANCE=athena npm start
```

Via URL parameter (no rebuild):

```
http://localhost:3000?instance=athena
http://localhost:3000?instance=vulcan
http://localhost:3000?instance=hermes
```

Default is `athena` when no instance is specified.

### Register Platforms (CLI)

```bash
# Install brain package
cd olympus/brain && pip install -e .

# Register a scout drone
olympus-register --id scout-01 --role scout --vertical athena \
    --api-url http://localhost:3001 --api-key <key>

# Register an executor with capabilities
olympus-register --id executor-01 --role executor --vertical athena \
    --capabilities investigate,photograph,mark,relay \
    --home-lat 34.0522 --home-lon -118.2437 \
    --api-url http://localhost:3001 --api-key <key>

# Register a partner vehicle (requires operator approval)
olympus-register --id partner-uav-01 --role scout --vertical athena \
    --trust-tier partner --provides-telemetry --provides-detections \
    --command-authority advisory \
    --accepted-commands "emergency_stop,recall_for_update" \
    --cbba-participant --ttl 7200 \
    --api-url http://localhost:3001 --api-key <partner-key>

# Register an observer (read-only, auto-approved)
olympus-register --id observer-01 --role observer \
    --trust-tier observer \
    --api-url http://localhost:3001 --api-key <observer-key>
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLYMPUS_API_KEY` | *(empty)* | API key for Vehicle API authentication |
| `OLYMPUS_INSTANCE` | `athena` | Active instance: `athena`, `ceres`, `vulcan`, `hermes` |
| `REACT_APP_INSTANCE` | `athena` | Frontend instance (build-time for Docker) |
| `REACT_APP_ZENOH_URL` | `http://localhost:8000` | Zenoh HTTP endpoint |
| `REACT_APP_VEHICLE_API_URL` | `http://localhost:3001` | Vehicle API endpoint |
| `REACT_APP_CESIUM_ION_TOKEN` | *(optional)* | Cesium Ion token for terrain |
| `ZENOH_ENDPOINTS` | `tcp/localhost:7447` | Zenoh router connection |
| `VEHICLE_API_PORT` | `3001` | Vehicle API listen port |
| `ADVISOR_PORT` | `8080` | Advisor API listen port |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama LLM API endpoint |
| `OLLAMA_MODEL` | `llama3.1:8b` | LLM model name for advisor |
| `VEHICLE_API_URL_INTERNAL` | `http://localhost:3001` | Vehicle API URL for advisor context |

| `OLYMPUS_PARTNER_KEYS_FILE` | *(optional)* | Path to partner keys JSON (multi-partner mode) |
| `MAVLINK_PORT` | `/dev/ttyUSB0` | MAVLink serial port |
| `MAVLINK_BAUD` | `57600` | MAVLink baud rate |
| `LORA_PORT` | *(optional)* | LoRa Meshtastic serial port |
| `ELRS_ENABLED` | `false` | Enable ExpressLRS 2.4GHz radio |
| `ELRS_SERIAL_PORT` | `/dev/ttyUSB1` | ELRS receiver UART port |
| `ELRS_BAUD_RATE` | `420000` | CRSF protocol baud rate |

### Security

- API keys stored as **SHA-256 hashes** at rest — raw keys never written to disk, config, or logs
- Constant-time comparison on all auth checks (prevents timing attacks)
- Per-partner scoped permissions (`read:telemetry`, `write:telemetry`, `command:own`, `command:all`, `mission:control`, `read:detections`, `admin:approve`, etc.)
- Per-partner rate limiting (not just per-IP)
- Webhook payloads signed with HMAC-SHA256 (`X-Olympus-Signature` header)
- Zenoh internal only — not exposed publicly; all external access through Vehicle API REST gateway
- When `OLYMPUS_API_KEY` is empty, auth is disabled (development mode)

#### OWASP Hardening

The Vehicle API and Python Brain include defense-in-depth measures following OWASP best practices:

| Category | Protection | Details |
|----------|-----------|---------|
| **Rate Limiting** | Tiered per route | Commands: 10/60s, Mutations: 20/60s, Reads: 100/60s, Advisor: 15/60s, Health: unlimited |
| **Rate Limiting** | Per-partner keys | Authenticated users rate-limited by partner ID, anonymous by IP |
| **Rate Limiting** | Retry-After header | 429 responses include `Retry-After` seconds per RFC 6585 |
| **Input Validation** | Command allowlist | Only 15 known commands accepted; unknown types rejected with 400 |
| **Input Validation** | Coordinate bounds | Lat ±90, Lon ±180, Alt -100..50000; NaN/Infinity rejected |
| **Input Validation** | Status allowlist | Only known vehicle statuses accepted in telemetry ingest |
| **Input Validation** | Payload size cap | Command params limited to 4KB serialized JSON |
| **Input Validation** | Vehicle ID format | 1-64 chars, alphanumeric + hyphens + underscores only |
| **SSRF Protection** | Webhook URL validation | Blocks private, loopback, link-local, CGN IPs; HTTPS required |
| **SSRF Protection** | Webhook limits | Max 10 per partner, 1000 total, URL max 2048 chars |
| **Auth** | Header-only tokens | `Authorization: Bearer` only — no query param tokens (CWE-614) |
| **Auth** | Mission scope check | `start_mission`/`abort_mission` require `mission:control` scope |
| **Auth** | Detection scope check | Detection list requires `read:detections` scope |
| **Auth** | Health outside auth | `/api/v1/health` is unauthenticated for load balancer probes |
| **Error Masking** | No entity ID leaks | Vehicle IDs, Zenoh errors, serde details logged server-side only (CWE-209) |
| **CORS** | Restrictive default | Default `http://localhost:3000`, not `*`; wildcard logs `error!` |
| **OOM Prevention** | Detection store cap | In-memory detections capped at 10,000 with oldest-first eviction |
| **Fail-Closed Trust** | Unknown = OBSERVER | Unregistered drones default to OBSERVER (no command authority) |
| **Prompt Injection** | Advisor sanitization | All telemetry strings sanitized before LLM prompt injection |
| **X-Forwarded-For** | Spoofing prevention | Only trusted when forwarded IP is not private/loopback |
| **CLI Security** | Env var API keys | `OLYMPUS_API_KEY` env var preferred over `--api-key` CLI arg (visible in `ps`) |

---

## Hardware

| Component | Scout | Executor |
|-----------|-------|----------|
| Compute | Jetson Orin NX (16GB) | Jetson Orin Nano (8GB) |
| Flight Controller | Pixhawk 6C (PX4) | Pixhawk 6C (PX4) |
| Primary Link | Heltec V3 LoRa Radio (Meshtastic 915MHz) | Heltec V3 LoRa Radio (Meshtastic 915MHz) |
| Low-Latency Link | Happymodel EP1 RX (ExpressLRS 2.4GHz) | Happymodel EP1 RX (ExpressLRS 2.4GHz) |
| Tertiary Link | WiFi 6 / 5G (when in range) | WiFi 6 / 5G (when in range) |
| Camera | e-CAM25_CUONX (AR0234, 4-lane MIPI CSI) | VIO camera |
| Primary Sensor | Multispectral/EO Camera | Domain-specific |
| Payload | None | Domain-specific |

### Pixhawk 6C Serial Port Map

| Port | UART | PX4 Device | Connection | Baud |
|------|------|------------|------------|------|
| TELEM1 | UART7 | /dev/ttyS5 | EP1 RX (ELRS MAVLink) | 460800 |
| TELEM2 | UART5 | /dev/ttyS3 | Jetson /dev/ttyTHS1 | 460800 |
| RC IN | — | — | (unused in MAVLink mode) | — |

### PX4 Parameters

```
# TELEM1 — ELRS MAVLink link (EP1 RX)
MAV_0_CONFIG = 101        # TELEM1
SER_TEL1_BAUD = 460800
MAV_0_RATE = 9600         # Max B/s
MAV_0_MODE = 0            # GCS mode
MAV_0_FORWARD = 1         # Forward to other instances

# TELEM2 — Jetson companion computer
MAV_1_CONFIG = 102        # TELEM2
SER_TEL2_BAUD = 460800
MAV_1_MODE = 2            # Onboard (companion)
MAV_1_RATE = 0            # Half theoretical max
MAV_1_FORWARD = 1         # Forward to other instances (critical for TUNNEL routing)
```

---

## Drone Onboarding Guide

Step-by-step guide to bring a new drone into the OLYMPUS fleet. Drones communicate via **LoRa radio** as the primary link.

### Prerequisites

| Category | Requirements |
|----------|-------------|
| **Hardware** | Drone frame + motors/ESCs, Jetson Orin NX (scout) or Nano (executor), Pixhawk 6C flight controller, Heltec V3 LoRa radio, Happymodel EP1 RX, GPS module, battery |
| **Firmware** | PX4 v1.14+ on Pixhawk, Meshtastic 2.x on Heltec V3, ELRS 3.5+ on EP1 RX |
| **Software** | Rust toolchain (rustup), Python 3.11+, QGroundControl |
| **Base Station** | Running `docker compose up` (Zenoh router, Vehicle API, Dashboard) |

### Step 1: Flash Flight Controller

```bash
# Install QGroundControl, connect Pixhawk 6C via USB
# Flash PX4 firmware v1.14+
# In QGroundControl:
#   1. Set airframe type (e.g., Generic Quadcopter)
#   2. Calibrate: accelerometer, gyroscope, magnetometer, radio
#   3. Configure TELEM1 for ELRS (MAV_0_CONFIG, SER_TEL1_BAUD)
#   4. Configure TELEM2 for companion computer (MAV_1_CONFIG, SER_TEL2_BAUD)
#   5. Enable forwarding (MAV_0_FORWARD=1, MAV_1_FORWARD=1)
```

### Step 2: Configure ELRS Receiver

```bash
# Flash EP1 RX with ELRS 4.0+ firmware (ExpressLRS Configurator)
# Set binding phrase to match your TX module (e.g., "olympus-1")

# For flight testing (Phase 0A): Set output to SBUS, wire to RC IN port
# For full stack (Phase 0B+): Set to MAVLink mode, wire to TELEM1
#   On RadioMaster Pocket: ELRS Lua → Other Devices → EP1 → Serial Protocol → MAVLink
#   On RadioMaster Pocket: ELRS Lua → Link Mode → MAVLink
```

### Step 3: Configure LoRa Radio

The LoRa radio is the **primary communication link** (RF radio, not WiFi).

```bash
# Install Meshtastic CLI
pip install meshtastic

# Connect Heltec V3 via USB, flash Meshtastic firmware
meshtastic --set lora.region US    # Set to your regulatory region
meshtastic --set lora.hop_limit 3  # Mesh relay depth
meshtastic --ch-set name OLYMPUS --ch-index 0
meshtastic --ch-set psk random --ch-index 0

# Verify radio-to-radio connectivity (need 2+ radios)
meshtastic --sendtext "ping"
```

### Step 4: Set Up Compute Module

```bash
# Flash Jetson Orin with JetPack SDK 6.1+
# After boot, install dependencies:
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
sudo apt install python3.11 python3.11-venv build-essential cmake

# Clone OLYMPUS
git clone <your-olympus-repo-url> ~/olympus
cd ~/olympus/bridge && cargo build --release
cd ~/olympus/brain && pip install -e .

# Connect serial cables:
#   Pixhawk TELEM2 → Jetson /dev/ttyTHS1 (MAVLink, 460800 baud)
#   Heltec V3 USB  → Jetson /dev/ttyUSB0 (LoRa/Meshtastic)
```

### Step 5: Configure and Start Bridge

```bash
# Set environment variables (or create /etc/olympus/bridge.toml)
export OLYMPUS_DRONE_ID=scout-01
export OLYMPUS_ROLE=scout
export ZENOH_MODE=peer
export ZENOH_CONNECT=tcp/base-station:7447
export LORA_ENABLED=true
export LORA_SERIAL_PORT=/dev/ttyUSB0
export ELRS_ENABLED=true
export ELRS_SERIAL_PORT=/dev/ttyUSB1
export MAVLINK_ENABLED=true
export MAVLINK_CONNECTION=serial:/dev/ttyTHS1:460800
export MAVLINK_SYSTEM_ID=1

cd ~/olympus/bridge
cargo run --release
# Expected: "Bridge started", "LoRa connected", "MAVLink heartbeat received"
```

### Step 6: Verify Connectivity

```bash
# On the base station, check Zenoh for this drone's telemetry:
curl http://localhost:8000/olympus/swarm/scout-01/telemetry

# From the Dashboard (http://localhost:3000):
#   - Drone should appear in fleet list
#   - Telemetry (position, battery, status) should update

# Test MAVLink command chain:
#   Dashboard → Vehicle API → Zenoh → Bridge → MAVLink → Pixhawk
curl -X POST http://localhost:3001/api/v1/fleet/scout-01/command \
  -H "Authorization: Bearer <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"command": "ARM"}'
```

### Step 7: Start Brain Module

```bash
# On the drone's Jetson compute module:

# For scout drones (detection + coverage scanning):
cd ~/olympus/brain
python -m olympus_brain.scout --id scout-01

# For executor drones (task bidding + execution):
python -m olympus_brain.executor --id executor-01
```

### Step 8: Mission Readiness Check

| Check | Expected |
|-------|----------|
| Telemetry rate | 1 Hz (idle state) |
| LoRa heartbeat | Every 5 seconds on `olympus/swarm/{id}/heartbeat` |
| Dashboard visibility | Drone appears with correct position on 3D map |
| Command response | ARM/DISARM commands execute within 1s |
| LoRa mesh fallback | Disconnect WiFi — drone stays visible via LoRa heartbeat |
| ELRS link | Link quality > 50%, RSSI on `olympus/swarm/{id}/elrs/link` |
| Battery reporting | Voltage, percentage, remaining time all populated |
| SwarmNet (scouts) | Model metadata on `olympus/swarm/{id}/model/metadata` |
| Escalation | High-risk task triggers APPROVE_REQUIRED in dashboard |

---

## Test Bench Tiers

Progressive validation tiers — a drone must pass all checks at one tier before advancing.

### Tier 1: RC Control (Simple)

**Active:** ELRS SBUS, Pixhawk, motors, RC controller, GPS
**Off:** Jetson, LoRa, Zenoh, autonomy, SwarmNet

| Check | Pass Criteria |
|-------|--------------|
| Binding | EP1 LED solid, RadioMaster shows RSSI |
| RC calibration | All 4 sticks + switches respond in QGC Radio tab |
| Motor spin | All motors spin correct direction (QGC motor test) |
| Sensor calibration | Accel, gyro, compass, level horizon — all green |
| Stabilized hover | Drone holds altitude with manual stick input |
| Position hold | GPS lock (≥6 sats, HDOP < 2.0), drone holds position hands-off |
| RTL | Returns to launch point and lands on RC failsafe |
| Battery failsafe | Triggers land/RTL at configured voltage threshold |

### Tier 2: Autonomous + Telemetry (Advanced)

**Active:** ELRS MAVLink mode, Pixhawk, GPS, bridge, Zenoh, dashboard, QGC over radio
**Off:** Jetson detections, SwarmNet, LoRa mesh, escalation

| Check | Pass Criteria |
|-------|--------------|
| MAVLink mode | ELRS set to MAVLink, EP1 on TELEM1, 460800 baud |
| QGC over radio | QGC connects via RadioMaster, shows heartbeat |
| RC via MAVLink | Sticks still control drone through ELRS MAVLink mode |
| Bridge telemetry | Bridge logs HEARTBEAT + GLOBAL_POSITION_INT |
| Zenoh pub | Telemetry on `olympus/swarm/scout-1/telemetry` |
| Dashboard map | Drone marker visible on Cesium map at correct GPS location |
| Live tracking | Dashboard updates position in real-time during flight |
| Autonomous waypoint | QGC mission mode: upload 3 waypoints, drone flies autonomously |
| Geofence | Drone respects QGC geofence boundaries |

### Tier 3: Full Stack (Most Advanced)

**Active:** All Tier 2 + Jetson, camera, SwarmNet, LoRa mesh, CBBA, ORCA, escalation, comms monitor, detection buffer

| Check | Pass Criteria |
|-------|--------------|
| Jetson MAVLink | Jetson sends/receives MAVLink on TELEM2, Pixhawk forwards to ELRS |
| Camera detection | Jetson camera runs inference, produces Detection objects |
| TUNNEL transmission | Detections reach base station via MAVLink TUNNEL |
| LoRa mesh | Heltec V3 sends compact telemetry, base receives |
| Detection over LoRa | Detections forwarded via LoRa port 0x44 |
| SwarmNet inference | Local model runs on Jetson, classifies detections |
| SwarmNet update | Base retrains global model, pushes weights via Zenoh |
| CBBA allocation | Multi-drone task auction completes, tasks assigned by score |
| ORCA avoidance | Drones avoid each other at < 50m distance |
| Escalation flow | High-risk task triggers APPROVE_REQUIRED, dashboard shows request |
| Escalation timeout | Unanswered escalation auto-proceeds after 30s |
| Comms degradation | WiFi killed → DEGRADED mode, telemetry rate drops |
| Comms denied | All comms killed → continues waypoints, stores detections locally |
| Comms recovery | Comms restored → buffered detections sync to base |
| Swarm consensus | Base comms lost → leader election, leader approves tasks |
| VIO fallback | GPS jammed → switches to VIO, maintains position |
| Multi-drone flight | ≥2 drones fly coordinated mission with all systems active |

---

## Partner API (External Vehicles)

Third-party vehicles (John Deere tractors, Boeing aircraft, etc.) join the fleet via REST API.

### 1. Generate a partner API key

```bash
openssl rand -hex 32
cd olympus/vehicle-api
cargo run -- --hash-key "your-raw-key-here"
```

### 2. Create a partner keys file

```json
[
  {
    "id": "partner-deere-01",
    "org_name": "John Deere",
    "key_hash": "<sha256-hash-from-step-1>",
    "scopes": ["read:telemetry", "write:telemetry", "read:detections"],
    "created_at": "2026-01-15T00:00:00Z",
    "expires_at": "2027-01-15T00:00:00Z"
  }
]
```

Start with: `OLYMPUS_PARTNER_KEYS_FILE=partner_keys.json cargo run`

### 3. Register a vehicle

```bash
curl -X POST http://localhost:3001/api/v1/vehicles/register \
  -H "Authorization: Bearer <raw-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "vehicle_id": "deere-tractor-42",
    "role": "executor",
    "capabilities": ["spray", "fertilize"],
    "position": {"latitude": 42.36, "longitude": -71.06, "altitude": 0}
  }'
```

### 4. Push telemetry

```bash
curl -X POST http://localhost:3001/api/v1/vehicles/deere-tractor-42/telemetry \
  -H "Authorization: Bearer <raw-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "position": {"latitude": 42.361, "longitude": -71.059, "altitude": 0},
    "battery": {"percentage": 85},
    "status": "executing"
  }'
```

### 5. Subscribe to events (webhooks)

```bash
curl -X POST http://localhost:3001/api/v1/webhooks \
  -H "Authorization: Bearer <raw-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://your-server.com/olympus-events",
    "events": ["detection", "task_assigned", "task_completed"],
    "secret": "your-webhook-signing-secret"
  }'
```

### Available scopes

| Scope | Access |
|-------|--------|
| `read:telemetry` | View fleet telemetry |
| `write:telemetry` | Push vehicle telemetry |
| `read:detections` | View detections |
| `write:detections` | Submit detections |
| `command:own` | Command own vehicles |
| `command:all` | Command any vehicle |
| `admin:approve` | Approve/revoke partner registrations |

Full API spec: [`vehicle-api/openapi.yaml`](vehicle-api/openapi.yaml)

### Trust-Tiered Vehicle Registration

Vehicles register with one of three trust tiers, controlling command authority and data access:

| Tier | Registration | Command Authority | Data Access |
|------|-------------|-------------------|-------------|
| **TRUSTED** | Auto-approved | BINDING — commands executed directly | Full read/write |
| **PARTNER** | Requires operator approval | ADVISORY — commands are suggestions | Per-manifest negotiated |
| **OBSERVER** | Auto-approved | NONE — no command authority | Read-only telemetry |

**Registration flow:**

1. Vehicle calls `POST /api/v1/vehicles/register` with `trust_tier` and optional `capability_manifest`
2. TRUSTED/OBSERVER → auto-approved (200). PARTNER → stored as PENDING (202), requires operator approval
3. Operator reviews pending registrations via `GET /api/v1/vehicles/pending`
4. Operator approves/rejects via `POST /api/v1/vehicles/{id}/approve`
5. Registration events published to `olympus/registry/{vehicle_id}` for all nodes

**CapabilityManifest** (for PARTNER vehicles):
```json
{
  "provides_telemetry": true,
  "provides_detections": true,
  "provides_features": false,
  "accepted_commands": ["emergency_stop", "recall_for_update"],
  "command_authority": "advisory",
  "participates_in_cbba": false,
  "ttl_seconds": 7200
}
```

**Safety:** `EMERGENCY_STOP` always goes through regardless of trust tier — safety overrides all access control.

**Backward compatible:** Missing `trust_tier` defaults to TRUSTED, so existing agents keep working without changes.

---

## Metrics & History

SQLite-backed telemetry, detection, task, and mission logging (7-day rolling window).

```bash
# Historical telemetry
curl http://localhost:3001/api/v1/metrics/telemetry?vehicle_id=scout-01&from=2026-01-01T00:00:00Z

# Detection summary
curl http://localhost:3001/api/v1/metrics/detections/summary

# Mission history
curl http://localhost:3001/api/v1/metrics/missions
```

---

## API Reference

### Zenoh Key Space

| Key Pattern | Purpose |
|-------------|---------|
| `olympus/swarm/{id}/telemetry` | Drone position, battery, status |
| `olympus/swarm/{id}/heartbeat` | LoRa heartbeat (compact) |
| `olympus/swarm/{id}/comms` | Communication link status |
| `olympus/swarm/{id}/elrs/link` | ELRS link stats (RSSI, LQ, SNR) |
| `olympus/swarm/{id}/features` | Intermediate perception features |
| `olympus/swarm/{id}/model/metadata` | Per-drone model version + accuracy |
| `olympus/detection/{id}` | Detection events |
| `olympus/task/auction` | Task announcements |
| `olympus/task/{id}/bid` | Task bids from executors |
| `olympus/task/{id}/award` | Task award to winning bidder |
| `olympus/command/{id}` | Commands to specific drone |
| `olympus/command/*` | Broadcast commands |
| `olympus/zone/{id}` | Zone assignments |
| `olympus/cbba/{id}/bundle` | CBBA bundle exchange |
| `olympus/escalation/{id}` | Escalation requests from drones |
| `olympus/escalation/response` | Human escalation responses |
| `olympus/swarmnet/model/global` | Global model weights from base station |
| `olympus/swarmnet/status` | Global SwarmNet status |
| `olympus/elrs/{id}/rx` | ELRS received packets |
| `olympus/lora/{node}/rx` | LoRa received packets |
| `olympus/registry/{id}` | Vehicle registration/approval/revocation events |

### Vehicle API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/fleet` | All drone statuses |
| `GET` | `/api/v1/fleet/{id}` | Single drone status |
| `POST` | `/api/v1/fleet/{id}/command` | Send command to drone |
| `GET` | `/api/v1/missions` | Active missions |
| `POST` | `/api/v1/vehicles/register` | Register vehicle (trust-tiered) |
| `POST` | `/api/v1/vehicles/{id}/telemetry` | Push partner telemetry |
| `GET` | `/api/v1/vehicles/pending` | List pending partner registrations |
| `POST` | `/api/v1/vehicles/{id}/approve` | Approve/reject partner registration |
| `POST` | `/api/v1/vehicles/{id}/revoke` | Revoke vehicle registration |
| `POST` | `/api/v1/webhooks` | Register event webhook |
| `GET` | `/api/v1/webhooks` | List registered webhooks |
| `GET` | `/api/v1/metrics/telemetry` | Historical telemetry query |
| `GET` | `/api/v1/metrics/detections/summary` | Detection counts |
| `GET` | `/api/v1/metrics/missions` | Mission history |
| `GET` | `/api/v1/ai-agent/status` | AI agent drift metrics, accuracy, model version |
| `POST` | `/api/v1/ai-agent/retrain` | Force recall/retrain/redeploy cycle |
| `POST` | `/api/v1/ai-agent/recall` | Recall all scouts for model update |
| `POST` | `/api/v1/ai-agent/redeploy` | Resume all scouts after update |
| `POST` | `/api/advisor/chat` | AI advisor chat |
| `GET` | `/api/advisor/health` | Advisor health check |
| `WS` | `/ws` | Real-time telemetry stream |

### Commands

| Command | Description |
|---------|-------------|
| `EMERGENCY_STOP` | Immediate halt |
| `RETURN_TO_LAUNCH` | Return to launch position |
| `PAUSE` | Hold position |
| `RESUME` | Continue mission |
| `GO_TO` | Navigate to coordinates |
| `START_SCAN` | Begin coverage pattern |
| `EXECUTE_TASK` | Execute assigned task |
| `SET_ALTITUDE` | Change operational altitude |
| `RECALL_FOR_UPDATE` | Recall scouts for model update (strategic layer) |
| `REDEPLOY` | Resume scouts after model update |
| `UPDATE_MODEL` | Push new model weights to fleet |

---

## Development

### Build Dashboard

```bash
cd olympus/dashboard
npm install
npm run build
```

### Build Rust Components

```bash
cd olympus/bridge && cargo build --release
cd olympus/vehicle-api && cargo build --release
```

### PX4 SITL Testing

```bash
# Terminal 1
make px4_sitl gazebo

# Terminal 2
MAVLINK_ENABLED=true MAVLINK_CONNECTION="udp:127.0.0.1:14550" \
  OLYMPUS_DRONE_ID=scout-01 cargo run --bin olympus-bridge
```

---

## ATHENA — Defense Vertical

ATHENA is the default vertical, providing tactical ISR (Intelligence, Surveillance, Reconnaissance) with built-in defense-specific safeguards.

### Threat Classification

Detections are automatically classified into three threat levels:

| Level | Detections | Response |
|-------|-----------|----------|
| **GREEN** | structural_change, thermal_anomaly | Log and photograph. Schedule follow-up if in sensitive area. |
| **AMBER** | vehicle_detected, person_detected | Photograph and track. Buddy system required for investigation. |
| **RED** | hostile_activity, ied_suspected | Mark position. Do NOT approach. Alert command immediately. |

### Rules of Engagement (ROE)

The `ROEEnforcer` validates all executor task assignments before execution:

| Rule | Constraint |
|------|-----------|
| **IED standoff** | IED suspected → MARK/PHOTOGRAPH only, maintain 50m minimum distance |
| **Buddy system** | INVESTIGATE tasks require minimum 2 available drones |
| **RED approval** | RED-level threats require human approval before executor action |
| **Exclusion zones** | Geographic exclusion zones checked before any task assignment |
| **Autonomous limit** | Executors can only autonomously respond to AMBER and below |

### Tactical Symbols

NATO APP-6(D) compatible symbol IDs are generated for each detection, rendered on the CesiumJS tactical map with threat-level color coding (RED/AMBER/GREEN).

---

## Roadmap

### Software

| Improvement | Description |
|-------------|-------------|
| **Dashboard escalation UI** | Approval panel for pending escalation requests, auto-approve rules editor, escalation history |
| **UWB inter-drone ranging** | Decawave DWM1001 modules for cm-precision ranging, distributed graph optimization for GPS-denied relative positioning |
| **LoRa command priority queue** | Prioritize EMERGENCY_STOP and RTL over telemetry on bandwidth-limited link |
| **Mesh-topology-aware CBBA** | Factor LoRa hop count into task scoring — closer drones in mesh = lower latency |
| **Hardware health telemetry** | Motor RPM, ESC temperature, vibration levels for predictive maintenance |

### Hardware

| Improvement | Description |
|-------------|-------------|
| **Standardized sensor bus** | USB-C / UART / I2C interface spec for hot-swappable sensors |
| **Payload weight auto-detection** | Load cell on mount plate, bridge adjusts flight parameters |
| **LoRa antenna diversity** | Dual antenna for better mesh reliability in terrain with obstacles |

---

**OLYMPUS OS** by **ELARIS**
