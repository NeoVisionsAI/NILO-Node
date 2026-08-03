# NILO-Node Architecture

## 1. Overview

NILO-Node is an edge data-collection platform designed to run 24/7 on a mini PC. It orchestrates hardware peripherals (OAK ToF camera, WiFi access point, Bluetooth microphones), exposes a local REST API for companion devices (NILO-Cardmed-Dev), stores captured data locally in **time-aligned chunks**, and synchronizes configuration and telemetry with NILO-backend.

Design goals:

- **Fully dockerized** deployment with automatic restart on failure and boot.
- **Parameterized** static configuration via a single YAML file.
- **Persistent runtime state** (devices, assignments, chunk index, history) in a lightweight local database.
- **Modular, plugin-oriented design** — new data sources, API routes, and backend adapters can be added without restructuring storage or core orchestration.
- **Chunk-centric storage** — all sources write into the same time window folder, enabling merge, search, and deletion by timestamp range.
- **English-only** for code, configuration keys, API paths, and log messages.

### 1.1 Target Hardware

| Component | Description |
|-----------|-------------|
| Host | Mini PC running Ubuntu Server (or minimal Debian-based OS) |
| Camera | [OAK-D-SR-PoE (OAK ToF)](https://shop.luxonis.com/products/oak-d-sr-poe) over USB |
| WiFi clients | NILO-Cardmed-Dev and other local devices |
| Audio | Bluetooth microphones |

> **Note:** Although the camera SKU includes PoE, NILO-Node assumes USB connectivity on the host for DepthAI pipeline access inside Docker.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Host (Mini PC)                                 │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    Docker (restart: always)                           │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │                     nilo-node container                         │  │  │
│  │  │                                                                 │  │  │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │  │  │
│  │  │  │ Orchestrator │  │ Config Mgr   │  │ State Store (SQLite) │  │  │  │
│  │  │  └──────┬───────┘  └──────────────┘  └──────────────────────┘  │  │  │
│  │  │         │                                                       │  │  │
│  │  │  ┌──────┴──────────────────────────────────────────────────┐   │  │  │
│  │  │  │              Campaign Controller                         │   │  │  │
│  │  │  │   (pull config from backend, campaign + run lifecycle)   │   │  │  │
│  │  │  └──────┬──────────────────────────────────────────────────┘   │  │  │
│  │  │         │                                                       │  │  │
│  │  │  ┌──────┴──────────────────────────────────────────────────┐   │  │  │
│  │  │  │                   Chunk Coordinator                      │   │  │  │
│  │  │  │   (wall-clock boundaries, open/finalize, manifest)     │   │  │  │
│  │  │  └──────┬──────────────────────────────────────────────────┘   │  │  │
│  │  │         │                                                       │  │  │
│  │  │  ┌──────┴──────────────────────────────────────────────────┐   │  │  │
│  │  │  │              DataSource plugins (modular)                │   │  │  │
│  │  │  │  rgb │ tof │ pose │ audio │ physiology │ (future...)    │   │  │  │
│  │  │  └─────────────────────────────────────────────────────────┘   │  │  │
│  │  │                                                                 │  │  │
│  │  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐   │  │  │
│  │  │  │ Local API   │  │ Backend     │  │ Health / Storage    │   │  │  │
│  │  │  │ (routers)   │  │ adapters    │  │ managers            │   │  │  │
│  │  │  └─────────────┘  └─────────────┘  └─────────────────────┘   │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│         │ USB              │ WiFi (AP)           │ Bluetooth                 │
│         ▼                  ▼                     ▼                           │
│    OAK ToF Camera    NILO-Cardmed-Dev       BT Microphones                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTPS (contracts TBD — modular adapters)
                                    ▼
                           ┌─────────────────┐
                           │  NILO-backend   │
                           └─────────────────┘
```

### 2.1 Why a Single Privileged Container

OAK ToF (USB), WiFi AP (`hostapd`), and Bluetooth (BlueZ) all require low-level host access. A **single privileged container** supervised by `supervisord` keeps deployment simple while the **internal codebase stays modular** via plugins and adapters.

---

## 3. Repository Layout (Target)

```
NILO-Node/
├── docker/
│   ├── Dockerfile
│   ├── supervisord.conf
│   └── entrypoint.sh
├── config/
│   ├── nilo-node.example.yaml
│   └── nilo-node.yaml              # gitignored
├── deploy/
│   └── systemd/
│       └── nilo-node.service
├── docs/
│   └── ARCHITECTURE.md
├── src/
│   └── nilo_node/
│       ├── main.py                 # Orchestrator entrypoint
│       ├── config/
│       ├── state/                  # SQLite models + repositories
│       ├── monitoring/             # Campaign controller + schedule engine
│       ├── chunks/                 # Chunk coordinator + manifest writer
│       ├── sources/                # DataSource plugin registry
│       │   ├── base.py             # DataSource protocol
│       │   ├── rgb/
│       │   ├── tof/
│       │   ├── pose/
│       │   ├── audio/
│       │   └── physiology/         # Cardmed-Dev ingest
│       ├── api/                    # FastAPI app + modular routers
│       │   └── routers/
│       ├── backend/                # Modular backend adapters (contracts TBD)
│       │   ├── client.py
│       │   └── adapters/
│       ├── network/
│       ├── health/
│       └── storage/                # Index queries, retention, deletion
├── tests/
├── docker-compose.yml
├── docker-compose.prod.yml
└── pyproject.toml
```

Adding a new data source = new package under `sources/{name}/` + YAML registration. No changes to chunk layout conventions.

---

## 4. Monitoring Model

Recording is organized in **three levels**. The clinician-facing unit is the **campaign** (e.g. `pruebas_dolor`); NILO-Node derives shorter internal units from the schedule.

```
Campaign (named study, from NILO-backend)
  └── Recording run(s)  — continuous ON period(s), no gaps
        └── Chunk(s)    — fixed-duration slices (e.g. 5 min)
```

### 4.1 Terminology

| Level | Internal name | What it means | Clinician example |
|-------|---------------|---------------|-------------------|
| **Campaign** | `campaign` | A named monitoring assignment for a subject, with schedule and sources. Persists across day/night gaps. | `"pruebas_dolor"` — all data for this study this week |
| **Recording run** | `recording_run` | One **uninterrupted** stretch while capture is ON. A new run starts after any pause. | Mon 10:00–22:00 (run 1), Tue 10:00–22:00 (run 2), … |
| **Chunk** | `chunk` | Fixed-length slice inside a run (configurable, e.g. 300 s). | 10:00–10:05, 10:05–10:10, … |

**Important:** When a clinician says *"session"*, they usually mean the **campaign**. When the schedule pauses overnight, the **campaign continues** but a **new recording run** opens the next morning.

### 4.1.1 Patient / Subject Identification

NILO-Node collects **medical patient data**. Every campaign, recording run, and chunk **must include** a `subject_user_id` field in its metadata — the backend patient/user identifier.

| Rule | Detail |
|------|--------|
| **Field name** | `subject_user_id` (string, UUID or backend-defined ID) |
| **Required in schema** | Always present in JSON manifests, SQLite rows, and backend payloads |
| **Nullable** | May be `null` when no patient is assigned yet (e.g. campaign created before enrollment) |
| **Propagation** | Copied from campaign → recording run → chunk `manifest.json` at creation time |
| **Backend source** | Set by the clinician in NILO-backend when creating or editing the campaign |

Example with patient:

```json
{ "campaign_name": "pruebas_dolor", "subject_user_id": "patient-uuid-123", … }
```

Example without patient (field still present):

```json
{ "campaign_name": "pruebas_dolor", "subject_user_id": null, … }
```

Queries such as *"all recordings for patient X"* filter on `subject_user_id IS NOT NULL AND subject_user_id = :id`. De-identified or unassigned campaigns remain valid with `null`.

---

The doctor configures campaigns in the **NILO-backend app**. NILO-Node **pulls** the active campaign for this node via `ConfigAdapter` (poll interval in local YAML). Local `config/nilo-node.yaml` holds **defaults and fallbacks only** (chunk duration default, poll interval, enabled adapters) — not the clinical schedule.

**Flow:**

1. Doctor creates/activates campaign `pruebas_dolor` in backend app → assigned to node UUID.
2. NILO-Node polls `ConfigAdapter` → receives campaign payload → validates → stores snapshot in SQLite.
3. `CampaignController` evaluates schedule every tick → opens/closes recording runs → `ChunkCoordinator` rotates chunks.
4. Doctor pauses, modifies, or ends campaign in app → next poll applies change (hot-reload where safe).
5. If backend unreachable: continue last known campaign until `offline_grace_sec` expires, then stop recording and alert via heartbeat.

### 4.3 Campaign Payload (backend → node, contract TBD)

Illustrative structure — exact URLs/auth defined when NILO-backend API is ready:

```json
{
  "campaign_id": "uuid-from-backend",
  "campaign_name": "pruebas_dolor",
  "subject_user_id": "patient-uuid-123",
  "status": "active",
  "valid_from": "2026-08-04T08:00:00+02:00",
  "valid_until": "2026-08-09T22:00:00+02:00",
  "chunk_duration_sec": 300,
  "timezone": "Europe/Madrid",
  "schedule": {
    "mode": "weekly",
    "rules": [
      {
        "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
        "windows": [{ "start": "10:00", "end": "22:00" }]
      }
    ]
  },
  "sources": {
    "rgb": { "enabled": true },
    "tof": { "enabled": true },
    "pose": { "enabled": true },
    "audio": { "enabled": true },
    "physiology": { "enabled": true }
  }
}
```

**Schedule modes** (all supported):

| Mode | Behaviour | Use case |
|------|-----------|----------|
| `fixed_window` | Single continuous window: `start` → `end` datetimes | 5 hours non-stop; Mon 10:00 → Sat 22:00 without nightly pause |
| `weekly` | Per-day time windows within `valid_from`–`valid_until` | Record 10:00–22:00 each day, off at night |
| `always` | Capture whenever campaign is `active` and inside validity range | 24/7 within campaign bounds |

`fixed_window` example (5 hours continuous):

```json
{
  "schedule": {
    "mode": "fixed_window",
    "start": "2026-08-04T09:00:00+02:00",
    "end": "2026-08-04T14:00:00+02:00"
  }
}
```

`fixed_window` example (Mon 10:00 → Sat 22:00, no nightly stop):

```json
{
  "valid_from": "2026-08-04T10:00:00+02:00",
  "valid_until": "2026-08-09T22:00:00+02:00",
  "schedule": {
    "mode": "fixed_window",
    "start": "2026-08-04T10:00:00+02:00",
    "end": "2026-08-09T22:00:00+02:00"
  }
}
```

### 4.4 Lifecycle Rules

**Campaign states** (from backend): `scheduled` | `active` | `paused` | `completed` | `cancelled`

| Event | Action on NILO-Node |
|-------|---------------------|
| Campaign becomes `active` + schedule ON | Open recording run (if none active) |
| Schedule turns OFF (e.g. 22:00 daily) | Finalize current chunk → close recording run; **campaign stays active** |
| Schedule turns ON again (e.g. next day 10:00) | Open **new** recording run under same campaign |
| Campaign `paused` | Finalize chunk → close run; no new runs until resumed |
| Campaign `completed` / `cancelled` | Finalize chunk → close run; archive campaign locally |
| Config change (sources, chunk duration) | Apply on next chunk boundary unless `immediate: true` flag from backend |

### 4.5 Worked Examples

**A — `pruebas_dolor`, 5 hours continuous**

- Campaign: `pruebas_dolor`, `fixed_window` 09:00–14:00
- Result: **1 campaign → 1 recording run → ~60 chunks** (at 5 min)

**B — `pruebas_dolor`, Mon–Sat daytime only (10:00–22:00)**

- Campaign: `weekly` rules, `valid_until` Saturday 22:00
- Result: **1 campaign → 6 recording runs** (Mon…Sat) → many chunks per run  
- Query *"all data for pruebas_dolor"* → filter by `campaign_id` or `campaign_name` across all runs

**C — `pruebas_dolor`, Mon 10:00 → Sat 22:00 without stopping**

- Campaign: single `fixed_window` spanning the full period
- Result: **1 campaign → 1 recording run** → all chunks contiguous  
- Same clinician label as B, different schedule mode

### 4.6 Local Fallback Configuration

Used only when backend is unavailable or for dev/testing:

```yaml
monitoring:
  offline_grace_sec: 3600             # Keep last campaign this long if backend down
  default_chunk_duration_sec: 300     # Fallback if campaign omits it

  # Dev/test stub — overridden when ConfigAdapter returns a campaign
  dev_campaign: null
```

---

## 5. Chunk-Centric Storage (Recommended Design)

### 5.1 Design Decision

**Recommendation: unified chunk directories with per-source subfolders**, indexed in SQLite.

Each alternative was evaluated:

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| Separate trees per source (`/video/`, `/audio/`, …) | Simple per-source writers | Hard to correlate by time; painful merge/delete | ❌ Rejected |
| Single container file (MCAP, ROS bag) | One file per chunk | Hard to append async sources (physiology photos); less human-inspectable | ❌ Rejected |
| **Unified chunk folder + source subdirs** | Time-aligned; extensible; easy delete-by-range; human-readable | Requires chunk coordinator | ✅ **Selected** |
| SQLite BLOBs for media | Queryable | Poor fit for GB-scale video; DB bloat | ❌ Rejected |

### 5.2 Directory Layout

```
/data/recordings/
└── campaigns/
    └── {campaign_id}/                    # UUID from backend
        ├── campaign.json                 # Snapshot of campaign config at activation
        └── runs/
            └── {recording_run_id}/       # New UUID per continuous ON period
                └── chunks/
                    └── {chunk_id}/
                        ├── manifest.json
                        ├── .complete
                        └── sources/
                            ├── rgb/ …
                            ├── tof/ …
                            ├── pose/ …
                            ├── audio/ …
                            └── physiology/ …
```

- **`campaign.json`**: `campaign_name`, `subject_user_id`, schedule snapshot, `valid_from`/`valid_until`.
- All chunks in a campaign share `campaign_id` and `campaign_name` in their manifest — queryable across multiple runs.
- Adding a new source → `sources/{name}/` inside each chunk (unchanged).

### 5.3 Chunk Boundaries

- **Wall-clock aligned** to `chunk_duration_sec` (e.g. 300s → 10:00, 10:05, 10:10… in local schedule timezone, stored as UTC).
- A **ChunkCoordinator** broadcasts `chunk_open` / `chunk_finalize` events to all registered DataSource plugins.
- On finalize: each source closes files, returns a `SourceManifest`; coordinator writes `manifest.json` + `.complete` marker.
- If a source fails mid-chunk: chunk marked `status: partial` in SQLite; other sources' data retained.

### 5.4 Chunk `manifest.json` (authoritative metadata)

```json
{
  "schema_version": "1.0",
  "chunk_id": "01JABC…",
  "campaign_id": "uuid",
  "campaign_name": "pruebas_dolor",
  "recording_run_id": "uuid",
  "node_id": "uuid",
  "subject_user_id": "patient-uuid-123",
  "time_range": {
    "start": "2026-08-03T08:00:00.000Z",
    "end": "2026-08-03T08:05:00.000Z"
  },
  "chunk_duration_sec": 300,
  "sources_present": ["rgb", "tof", "pose", "audio", "physiology"],
  "sources": {
    "rgb": {
      "path": "sources/rgb/video.mp4",
      "frame_count": 9000,
      "fps": 30,
      "codec": "h264"
    },
    "tof": {
      "path": "sources/tof/depth.mkv",
      "timestamps_path": "sources/tof/timestamps.npy",
      "frame_count": 9000,
      "fps": 30,
      "codec": "ffv1",
      "pixel_format": "gray16le",
      "depth_unit": "mm",
      "dtype": "uint16",
      "width": 640,
      "height": 480
    },
    "pose": {
      "path": "sources/pose/landmarks.npy",
      "timestamps_path": "sources/pose/timestamps.npy",
      "frame_count": 4500,
      "fps": 15,
      "model": "mediapipe",
      "landmark_count": 33,
      "dtype": "float32",
      "shape": [4500, 33, 4]
    },
    "audio": {
      "tracks": [
        {
          "mic_id": "bt:AA:BB:CC:DD:EE:FF",
          "path": "sources/audio/bt_AABBCCDDEEFF.flac",
          "sample_rate": 16000,
          "channels": 1
        }
      ]
    },
    "physiology": {
      "index_path": "sources/physiology/index.jsonl",
      "capture_count": 3
    }
  }
}
```

### 5.5 Per-Frame Timestamps

Every time-series source ships a **`timestamps.npy`** (`float64`, UTC epoch seconds, one value per frame/sample-row).

This enables:

- Sub-frame alignment across RGB, ToF, and pose (rates may differ).
- Reconstruction of continuous timelines by concatenating chunks ordered by `time_range.start`.
- Search by campaign: *"all chunks for `pruebas_dolor`"* → SQLite filter on `campaign_id` or `campaign_name`.
- Search by time: *"chunks overlapping 2026-08-03 14:00–15:00"* → overlap on `start_ts`/`end_ts`.

Physiology uses **`index.jsonl`** (one JSON object per capture):

```json
{"capture_ts": "2026-08-03T08:02:14.123Z", "reading_id": "uuid", "image": "images/20260803T080214.123Z_uuid.jpg", "device_id": "cardmed-dev-1"}
```

### 5.6 SQLite Chunk Index

Filesystem layout alone is insufficient for fast range queries and deletion. A **`chunks`** table indexes every finalized chunk:

| Column | Purpose |
|--------|---------|
| `chunk_id` | Primary key |
| `campaign_id` | FK to campaign (indexed) |
| `campaign_name` | Human-readable name, e.g. `pruebas_dolor` (indexed) |
| `recording_run_id` | FK to recording run |
| `subject_user_id` | Patient/user ID from backend; **NOT NULL column, nullable value** (`NULL` = unassigned) |
| `start_ts` | UTC epoch ms (indexed) |
| `end_ts` | UTC epoch ms (indexed) |
| `path` | Absolute path to chunk dir |
| `status` | `complete` \| `partial` \| `deleted` |
| `sources_present` | JSON array |
| `byte_size` | Total chunk size |

**`campaigns`** table:

| Column | Purpose |
|--------|---------|
| `campaign_id` | Primary key (from backend) |
| `campaign_name` | e.g. `pruebas_dolor` |
| `subject_user_id` | Patient/user ID from backend; **NOT NULL column, nullable value** (`NULL` = unassigned) |
| `status` | Mirrors backend campaign status |
| `valid_from` / `valid_until` | Campaign bounds |
| `config_snapshot` | Full JSON from backend |

**`recording_runs`** table:

| Column | Purpose |
|--------|---------|
| `recording_run_id` | Primary key |
| `campaign_id` | FK |
| `start_ts` / `end_ts` | Run bounds (null `end_ts` while active) |
| `path` | `/data/recordings/campaigns/{id}/runs/{run_id}` |

**Deletion by time range** (`DELETE /data?start=&end=` or CLI):

```sql
SELECT chunk_id, path FROM chunks
WHERE status = 'complete'
  AND start_ts < :range_end
  AND end_ts   > :range_start;
```

Then remove directories and mark `status = 'deleted'`. Overlapping chunks are handled correctly with the overlap predicate.

### 5.7 Local Storage & Replication (Phase 2)

**Primary storage** is always the mini PC local disk where NILO-Node runs:

```
{storage.base_path}/{storage.recordings_dir}/campaigns/…
```

Default: `/data/recordings/campaigns/…`

All capture writes go here first. Replication to external destinations is **asynchronous** and **optional**.

#### Storage tiers

```
┌─────────────────────────────────────────────────────────────┐
│  Tier 1 — Local (mini PC)          ALWAYS ON                │
│  /data/recordings/…                                         │
│  Fast writes, SQLite index, retention & range delete        │
└───────────────────────────┬─────────────────────────────────┘
                            │ ReplicationTarget plugins
              ┌─────────────┴─────────────┐
              ▼                           ▼
┌─────────────────────────┐   ┌─────────────────────────────┐
│ Tier 2a — NILO-backend  │   │ Tier 2b — NAS (local mount) │
│ HTTP upload (JWT)       │   │ /mnt/nilo-nas/…             │
│ realtime or daily batch │   │ copy or rsync mirror        │
└─────────────────────────┘   └─────────────────────────────┘
```

| Alternative | Mode | Use case |
|-------------|------|----------|
| **Local only** | `replication.enabled: false` | Dev, short studies, backend/NAS not ready |
| **Backend realtime** | `mode: realtime`, `targets.backend.enabled: true` | Cloud copy shortly after each chunk finalizes |
| **Backend daily** | `mode: scheduled`, `daily_at: "02:00"` | Nightly batch upload to NILO-backend |
| **NAS mirror** | `targets.nas.enabled: true` | On-prem archive on NAS mounted on the mini PC |
| **Backend + NAS** | Both targets enabled | Cloud + local site backup |
| **Manual replicate** | `mode: manual` + `POST /api/v1/chunks/replicate` | Operator-triggered sync |

#### Replication configuration

```yaml
storage:
  base_path: "/data"
  recordings_dir: "recordings"
  max_usage_percent: 85
  retention_days: 30
  delete_only_if_replicated: false   # safety: only delete when all targets complete

replication:
  enabled: true
  mode: "realtime"                   # realtime | scheduled | manual
  daily_at: "02:00"
  delete_local_after_replicated: false
  targets:
    backend:
      enabled: true                  # uses backend.endpoints.upload / manifest
    nas:
      enabled: true
      mount_path: "/mnt/nilo-nas"
      relative_path: "nilo-node"
      method: "copy"                 # copy | rsync
```

#### ReplicationTarget plugin protocol

Adding a new destination (e.g. S3, second NAS):

1. Implement `ReplicationTarget` in `src/nilo_node/storage/replication/{name}_target.py`.
2. Register in `ReplicationManager._build_targets`.
3. Add config block under `replication.targets.{name}`.

Each finalized chunk enqueues one job per enabled target in `replication_jobs`. Status tracked in `chunk_replication`.

#### Retention & deletion

- **API:** `GET /api/v1/chunks`, `POST /api/v1/chunks/delete`, `GET /api/v1/storage/usage`
- **CLI:** `nilo-node chunks-list`, `nilo-node chunks-delete --start … --end …`
- **Retention worker:** deletes chunks older than `retention_days` (respects `delete_only_if_replicated`)

---

## 6. ToF Storage — Recommended Format

### 6.1 Requirements

- Continuous capture at up to 640×480 @ 30 fps (VGA ToF sensor on OAK-D-SR-PoE).
- Preserve **millimetre precision** (uint16 depth values) — lossy H.264 grayscale is insufficient for clinical/metrics use.
- Fit the chunk model (one primary file + timestamps sidecar per chunk).
- Reasonable disk usage for 24/7 or long daily windows.

### 6.2 Size Estimates (5-minute chunk @ 30 fps)

| Format | Approx. size | Lossless | Tooling |
|--------|-------------|----------|---------|
| Raw uint16 stack (`.npy`) | ~5.5 GB | Yes | NumPy |
| NPZ compressed | ~1–2 GB | Yes | NumPy |
| **FFV1 in MKV (`gray16le`)** | **~0.8–1.5 GB** | **Yes** | **ffmpeg, OpenCV** |
| H.265 gray16 | ~200–400 MB | Near-lossless | ffmpeg |
| H.264 (8-bit normalized preview) | ~50–100 MB | No | ffmpeg |

### 6.3 Recommendation

**Primary format: FFV1 lossless codec in MKV container**

```
sources/tof/depth.mkv       # ffv1, pix_fmt gray16le, 640×480
sources/tof/timestamps.npy  # float64 UTC epoch per frame
```

**Why FFV1 MKV:**

1. **Lossless** — uint16 depth in mm is preserved exactly.
2. **~3–6× compression** vs raw — viable for multi-hour daily recording on edge SSD.
3. **Standard tooling** — ffmpeg for playback, transcode, and inspection.
4. **Single file per chunk** — aligns with RGB `video.mp4`; simple lifecycle.
5. **Frame-accurate** when paired with `timestamps.npy`.

**Configurable fallback** for storage-constrained deployments:

```yaml
sources:
  tof:
    enabled: true
    fps: 30
    storage_mode: "lossless"      # lossless | compressed
    # lossless → ffv1/mkv (default)
    # compressed → libx265/gray16le/mkv (~5× smaller, still 16-bit)
    include_preview: false        # optional 8-bit H.264 preview for quick review
```

Optional `depth_preview.mp4` (8-bit normalized) can be generated alongside for debugging without affecting the archival stream.

**Rejected as primary:** raw `.npy` frame stacks — correct but ~5× larger; acceptable only for short lab captures. **Rejected as primary:** 8-bit H.264 — destroys depth precision.

---

## 7. Modular DataSource Plugin System

### 7.1 Protocol

Each source implements the `DataSource` protocol:

```python
class DataSource(Protocol):
    source_id: str                          # e.g. "tof", "rgb", "physiology"

    async def on_campaign_start(self, campaign: Campaign) -> None: ...
    async def on_campaign_stop(self, campaign: Campaign) -> None: ...
    async def on_run_start(self, run: RecordingRun) -> None: ...
    async def on_run_stop(self, run: RecordingRun) -> None: ...

    async def on_chunk_open(self, ctx: ChunkContext) -> None: ...
    async def on_chunk_finalize(self, ctx: ChunkContext) -> SourceManifest: ...
    async def on_chunk_abort(self, ctx: ChunkContext) -> None: ...

    def health(self) -> SourceHealth: ...
```

Registration via config + entry point or explicit registry:

```yaml
sources:
  tof:
    enabled: true
    plugin: "nilo_node.sources.tof.ToFSource"
    fps: 30
    storage_mode: "lossless"
```

Adding a source:

1. Implement `DataSource` in `src/nilo_node/sources/{name}/`.
2. Add config block under `sources.{name}`.
3. Register in plugin registry (or Python entry point).
4. Data appears under `sources/{name}/` in each chunk automatically.

Physiology is **event-driven** (Cardmed-Dev uploads arrive anytime): the plugin buffers into the **current open chunk** based on `capture_ts`. Late arrivals targeting a closed chunk go to `sources/physiology/late/` with a back-reference in `index.jsonl` (`"target_chunk_id": "…"`).

---

## 8. Configuration Strategy

### 8.1 Static Configuration (`config/nilo-node.yaml`)

```yaml
node:
  id: ""
  name: "nilo-node"

backend:
  base_url: "https://api.nilo.example"
  api_key: "${NILO_BACKEND_API_KEY}"
  heartbeat_interval_sec: 30
  config_poll_interval_sec: 300
  # Adapter toggles — endpoints defined when backend contract is ready
  adapters:
    heartbeat: { enabled: true }
    config: { enabled: true }
    manifest: { enabled: false }
    upload: { enabled: false }

monitoring:
  offline_grace_sec: 3600
  default_chunk_duration_sec: 300
  dev_campaign: null                 # Dev/test only; production uses backend

sources:
  rgb:   { enabled: true, fps: 30, codec: "h264" }
  tof:   { enabled: true, fps: 30, storage_mode: "lossless" }
  pose:  { enabled: true, fps: 15, model: "mediapipe" }
  audio: { enabled: true, format: "flac", sample_rate: 16000 }
  physiology: { enabled: true, image_format: "jpeg" }

storage:
  base_path: "/data"
  max_usage_percent: 85
  retention_days: 30                  # Global default; overridable per source

wifi:
  enabled: true
  ssid_prefix: "nilo-node"
  password: "${NILO_WIFI_PASSWORD}"
  interface: "wlan0"

local_api:
  host: "0.0.0.0"
  port: 8080
  auth_token: "${NILO_LOCAL_API_TOKEN}"

logging:
  level: "INFO"
  format: "json"
```

### 8.2 Runtime State (SQLite: `/data/nilo-node.db`)

| Table | Purpose |
|-------|---------|
| `campaigns` | Active/completed campaigns pulled from backend |
| `recording_runs` | Continuous capture periods within a campaign |
| `chunks` | Index of all chunks — **primary query surface** |
| `devices` | Connected peripherals snapshot |
| `device_events` | Connect/disconnect history |
| `cardmed_assignments` | Cardmed-Dev binding to this node |
| `heartbeats` | Cached telemetry payloads |
| `backend_config_snapshots` | Remote config history |
| `upload_queue` | Pending backend sync jobs (when adapters enabled) |

---

## 9. API Design (Modular, Contracts TBD)

### 9.1 Local REST API (NILO-Cardmed-Dev)

FastAPI with **router modules** under `api/routers/`. Versioned prefix `/api/v1/`.

| Router module | Endpoints (initial) | Status |
|---------------|---------------------|--------|
| `health.py` | `GET /health` | Defined |
| `node.py` | `GET /node/info` | Defined |
| `cardmed/register.py` | `POST/DELETE /cardmed/register` | Defined |
| `cardmed/uploads.py` | `POST /cardmed/photos` | Defined |
| `cardmed/status.py` | `GET /cardmed/status` | Defined |
| `bluetooth.py` | `GET /bluetooth/discover`, `/status`, `POST /connect`, `/disconnect`, `PATCH /mics/{mac}/recording` | Defined |
| `devices.py` | `GET /devices` | Defined |
| *(future)* | New routers auto-mounted | Extensible |

Cardmed photo flow:

1. `POST /api/v1/cardmed/photos` (multipart + `capture_ts`, optional metadata).
2. Physiology DataSource writes into current chunk's `sources/physiology/`.
3. Optional forward to backend via `PhysiologyUploadAdapter` when contract is ready.

Authentication: Bearer token. OpenAPI spec generated for Cardmed-Dev team.

### 9.2 NILO-backend Integration (Modular Adapters)

Backend URL paths and response shapes are **not finalized**. NILO-Node ships a full HTTP + JWT layer; you only configure paths in YAML when ready.

#### Layer stack

```
BackendClient
  ├── AuthManager        login, refresh, token store, Authorization header
  ├── BackendTransport   httpx, retry, logging, 401 → refresh
  └── Adapters           ConfigAdapter, HeartbeatAdapter, (Manifest, Upload…)
```

#### Authentication (`backend.auth`)

| Field | Description |
|-------|-------------|
| `mode` | `none` \| `api_key` \| `jwt` |
| `client_id` / `client_secret` | Credentials for login (env-substituted) |
| `token_store_path` | Persisted access/refresh tokens (default `/data/backend/auth_tokens.json`) |
| `refresh_skew_sec` | Refresh this many seconds before JWT `exp` |
| `login_grant` | `client_credentials` or `node_credentials` — login body shape |

**JWT flow:**

1. On startup → load tokens from disk; refresh or login if expired.
2. Every request → `Authorization: Bearer {access_token}`.
3. HTTP 401 → refresh once → retry; if refresh fails → login.
4. Tokens parsed from `{ access_token, refresh_token, expires_in }` (snake_case or camelCase).

**`api_key` mode:** sends `Authorization: Bearer {backend.api_key}` without login.

#### Configurable endpoints (`backend.endpoints`)

| Key | Typical use | Adapter |
|-----|-------------|---------|
| `login` | POST — obtain JWT | AuthManager |
| `refresh` | POST — refresh JWT | AuthManager |
| `campaign` | GET — active campaign for `{node_id}` | ConfigAdapter |
| `heartbeat` | POST — node telemetry | HeartbeatAdapter |
| `manifest` | POST — new chunk notification | ManifestAdapter |
| `upload` | PUT/POST — blob upload | UploadAdapter (future) |

Empty string = **not configured**; adapter skips remote call and logs locally.

Placeholders use `{node_id}`: `/api/v1/nodes/{node_id}/campaign`.

#### Adapters

| Adapter | Direction | Purpose | Contract status |
|---------|-----------|---------|-----------------|
| `ConfigAdapter` | GET | Pull active **campaign** | TBD |
| `HeartbeatAdapter` | POST | Node telemetry | TBD |
| `ManifestAdapter` | POST | New finalized chunks | TBD |
| `UploadAdapter` | PUT/POST | Chunk blob upload | TBD |

#### Offline behaviour

- `ConnectivityState` tracks last success/failure.
- Campaign poll falls back to cached SQLite campaign within `monitoring.offline_grace_sec`.
- Heartbeat payload includes `backend.connectivity` block.

#### Remote configuration (backend → node)

Operational settings are **stored in NILO-backend** and pulled by NILO-Node on poll. No separate DB needed on the edge — SQLite caches the last applied campaign/config snapshot.

| Setting | Remote source | Local fallback |
|---------|---------------|----------------|
| Campaign schedule, subject | `backend.endpoints.campaign` | `monitoring.dev_campaign` |
| Capture toggles (rgb/tof/pose/audio/…) | `campaign.sources.*` | `camera.defaults.*` |
| Storage/replication (future) | Extended config endpoint | `config/nilo-node.yaml` |

When backend pushes an updated campaign, `CameraManager.set_campaign()` refreshes capture flags without restart.

---

## 10. Core Services (Summary)

| Service | Role |
|---------|------|
| **Orchestrator** | Boot, plugin registry, worker supervision |
| **Campaign Controller** | Pulls backend config; manages campaign + recording-run lifecycle |
| **Chunk Coordinator** | Wall-clock chunk rotation; manifest assembly |
| **DataSource plugins** | rgb, tof, pose, audio, physiology |
| **Storage Manager** | Quotas, retention, range deletion via chunk index |
| **Health Reporter** | Periodic telemetry via HeartbeatAdapter |
| **Local API** | Modular FastAPI routers for Cardmed-Dev |
| **WiFi / Bluetooth** | AP and mic management (unchanged from prior design) |

---

## 11. Docker & Deployment

(Unchanged — see prior sections: `restart: always`, privileged container, host systemd unit, `/opt/nilo-node` install path.)

---

## 12. Security Considerations

- Local API bearer token; WiFi WPA2 from env.
- Backend credentials via environment only.
- Cardmed uploads: size/MIME validation before write to chunk.
- Chunk deletion requires authenticated admin endpoint or CLI (future).
- TLS for all backend adapters when enabled.

---

## 13. Technology Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.11+ |
| Local API | FastAPI + Uvicorn |
| HTTP client | httpx |
| Config | PyYAML + pydantic |
| State / chunk index | SQLite |
| Media encoding | ffmpeg (RGB H.264, ToF FFV1, audio FLAC) |
| Camera | depthai SDK |
| Pose | MediaPipe / YOLO (configurable) |
| Chunk IDs | ULID (sortable, unique) |
| Timestamps | UTC float64 epoch in `.npy`; ISO-8601 in JSON |

---

## 14. Implementation Phases

### Phase 0 — Foundation & Skeleton

- Docker, config, SQLite (`campaigns`, `recording_runs`, `chunks` tables)
- Plugin registry + stub DataSource
- ChunkCoordinator with wall-clock rotation (stub writers)
- CampaignController + schedule engine (`fixed_window`, `weekly`, `always`)
- ConfigAdapter stub returning sample campaign payloads
- Health reporter + local API health/node endpoints
- Systemd unit

**Exit criteria:** Backend stub campaign activates; schedules open/close recording runs; stub chunks land under `campaigns/{id}/runs/{run_id}/chunks/`; SQLite index populated.

---

### Phase 1 — Backend Campaign Sync

- ConfigAdapter: pull and validate campaign payload from NILO-backend (or mock)
- Campaign hot-reload on poll (pause, resume, schedule change)
- HeartbeatAdapter reports `campaign_name`, `campaign_id`, active run
- Persist config snapshots in `campaigns` table
- Offline grace behaviour when backend unreachable

**Exit criteria:** Changing campaign schedule in mock backend opens/closes runs without rebuild; heartbeat reflects active campaign.

---

### Phase 2 — Chunk Storage, Retention & Replication ✅

- Local recordings path: `/data/recordings/campaigns/…`
- `StorageManager`: usage stats, retention, range deletion, quota check
- `ReplicationManager` + targets: **backend** (stub/upload-ready), **NAS** (copy/rsync)
- Modes: `realtime`, `scheduled` (daily), `manual`
- API: `/api/v1/storage/usage`, `/api/v1/chunks`, `/api/v1/chunks/delete`, `/api/v1/chunks/replicate`
- CLI: `chunks-list`, `chunks-delete`, `storage-usage`

**Exit criteria:** Chunks indexed and queryable; delete by time range removes only overlapping chunks; NAS replication mirrors chunk folder structure; backend target ready for upload endpoint.

---

### Phase 3 — OAK ToF Camera Sources ✅

- `CameraManager`: discover, connect, disconnect, status
- Mock pipeline when no hardware (`mock_when_unavailable: true`)
- DepthAI discovery when SDK installed; full capture graph incremental
- Sources: `rgb` (video), `tof` (depth FFV1 stub), `pose` (landmarks)
- **Remote capture toggles** via backend campaign `sources.{rgb,tof,pose}.enabled`
- API: `GET /camera/discover`, `POST /camera/connect`, `POST /camera/disconnect`, `GET /camera/status`
- Optional install: `pip install -e ".[camera]"` (depthai, mediapipe, opencv)

**Backend campaign example (remote management):**

```json
{
  "campaign_name": "pruebas_dolor",
  "subject_user_id": "patient-123",
  "sources": {
    "rgb":   { "enabled": true,  "record_video": true },
    "tof":   { "enabled": false, "record_depth": false },
    "pose":  { "enabled": true,  "record_landmarks": true },
    "audio": { "enabled": true },
    "physiology": { "enabled": true }
  }
}
```

NILO-Node polls this via `ConfigAdapter` and applies flags on each campaign update. Storage/replication settings can follow the same pattern when backend exposes them.

**Exit criteria:** With mock pipeline, chunks contain only enabled streams; API discovers/connects; flags change when backend campaign updates.

---

### Phase 3b — DepthAI Full Pipeline (when hardware available)

> **Implemented in Phase 7** — see below.

- DepthAI graph for OAK-D-SR-PoE (RGB + ToF VGA)
- FFmpeg FFV1 writer for ToF, H.264 for RGB
- **Pluggable pose engine** on host CPU — compare during bring-up:
  - `camera.pose_backend: mediapipe` (default) — lightweight landmarks, good latency
  - `camera.pose_backend: yolo` — alternative detector/pose stack (model TBD)
  - `camera.pose_backend: custom` — swap via plugin / `camera.pose_model`
  - Backend campaign may override stream flags; pose **backend choice** stays in node YAML until benchmark results land
- Hot-plug reconnect

**Note:** Run side-by-side accuracy/latency tests (MediaPipe vs YOLO vs other) on target hardware before locking the default.

---

### Phase 7 — DepthAI Full Pipeline ✅

- **`DepthAiDeviceSession`** — RGB preview + ToF/mono depth graph (`camera/depthai_graph.py`)
- **FFmpeg pipe writers** — H.264 MP4 (RGB), FFV1 or x265 gray16 MKV (ToF)
- **Pluggable pose engine** — `mediapipe` | `yolo` | `custom` (`camera/pose/`)
- **Hot-plug reconnect** — watchdog in `CameraManager` (`reconnect_enabled`, `reconnect_interval_sec`)
- Synthetic frame fallback when graph fails but device is present (still uses FFmpeg encoders)

**Exit criteria:** With OAK hardware, chunks contain FFmpeg-encoded RGB/ToF and pose landmarks; reconnect after USB replug; mock mode unchanged for dev/CI.

---

### Phase 4 — Cardmed-Dev & Physiology Source ✅

- WiFi AP (`network/wifi_manager.py`) — hostapd + dnsmasq, mock when unavailable
- Modular Cardmed routers (`/api/v1/cardmed/register`, `/photos`, `/status`)
- `PhysiologySource` plugin — images into active chunk + `index.jsonl`
- `PhysiologyAdapter` stub for backend forward (`backend.endpoints.physiology`)
- `GET /api/v1/devices` — camera, Cardmed, WiFi aggregate

**Exit criteria:** Photo upload lands in `sources/physiology/` of the correct chunk with `index.jsonl` entry.

---

### Phase 5 — Bluetooth Audio Source ✅

- `BluetoothManager` — power on adapter, discover, connect/disconnect (BlueZ / mock)
- Per-mic **`record_enabled`** toggle (persisted in `bluetooth_mics`)
- `AudioSource` plugin — per-mic FLAC tracks + timestamps in chunk
- API: `GET /bluetooth/discover`, `/status`, `POST /connect`, `/disconnect`, `PATCH /mics/{mac}/recording`

**Exit criteria:** Audio tracks appear in chunk manifest alongside video/ToF.

---

### Phase 6 — Production Hardening

- **ManifestAdapter** + **UploadAdapter** — POST manifest JSON and tar.gz chunk archive
- **Offline upload queue** — SQLite `upload_queue`, retries when backend unreachable
- **Partial-chunk recovery** — finalize stale `open` chunks or resume active capture on restart
- **Sync API** — `GET /api/v1/sync/status`
- 24h soak test on target hardware (see [docs/SOAK_TEST.md](docs/SOAK_TEST.md))

**Exit criteria:** Chunks sync to backend when online; survive offline periods; recover cleanly after crash.

---

## 15. Phase Summary

| Phase | Name | Key outcome |
|-------|------|-------------|
| 0 | Foundation | Schedules, chunk coordinator, SQLite index, Docker |
| 1 | Backend stubs | Modular adapters, remote config |
| 2 | Storage & replication | Local disk, retention, NAS/backend targets, API/CLI |
| 3 | Camera | RGB + **ToF (FFV1 MKV)** + pose |
| 4 | Cardmed | WiFi AP, physiology plugin |
| 5 | Audio | Bluetooth mic tracks in chunks |
| 6 | Production | Backend sync, hardening |
| 7 | DepthAI pipeline | FFmpeg RGB/ToF, pose engine, hot-plug |

---

## 16. Resolved & Open Decisions

### Resolved

| Decision | Choice |
|----------|--------|
| Clinician "session" | Maps to **campaign** (`campaign_name`, e.g. `pruebas_dolor`) |
| Continuous vs day-only | Same campaign; schedule mode (`fixed_window` vs `weekly`) controls pauses |
| Config source of truth | **NILO-backend** (doctor app); local YAML = defaults/fallback only |
| Internal continuous unit | **Recording run** — new run after each schedule pause |
| Storage layout | Unified chunk folders with `sources/{name}/` subdirs |
| ToF format | **FFV1 lossless MKV** (`gray16le`) + `timestamps.npy`; optional H.265 compressed mode |
| Chunk alignment | Wall-clock aligned to configurable duration |
| Time indexing | SQLite `chunks` table + per-frame `timestamps.npy` |
| Extensibility | DataSource plugin protocol; backend/local API adapter routers |
| Physiology (async) | `index.jsonl` + images folder inside active chunk |

### Still open (non-blocking)

1. Inference placement: Myriad X vs host CPU for YOLO.
2. WiFi AP: container vs host helper for driver/regdomain edge cases.
3. Backend adapter URL/auth contracts — coordinate when NILO-backend API stabilizes.
4. Cardmed result delivery: polling vs push webhook.

---

## 17. References

- [Luxonis OAK ToF (OAK-D-SR-PoE)](https://shop.luxonis.com/products/oak-d-sr-poe)
- [DepthAI Documentation](https://docs.luxonis.com/)
- [FFV1 codec specification (lossless)](https://ffmpeg.org/ffmpeg-codecs.html#ffv1)
- [Luxonis DepthAI Python API](https://docs.luxonis.com/projects/api/en/latest/)
