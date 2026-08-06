# NILO-Node

Edge data collection platform for the NILO medical monitoring system. Runs on a mini PC, orchestrates sensors (OAK ToF camera, Bluetooth microphones, NILO-Cardmed-Dev), stores patient data in time-aligned chunks, and syncs with NILO-backend.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Phase 0 (current)

- Dockerized orchestrator with config loading, SQLite state, campaign/recording-run/chunk lifecycle
- Stub data sources writing into the chunk layout
- Local REST API (`/api/v1/health`, `/api/v1/node/info`)
- Heartbeat reporter with local logging

## Phase 7 (current)

- **DepthAI full pipeline** — RGB + ToF graph for OAK cameras (`DepthAiDeviceSession`)
- **FFmpeg encoders** — H.264 MP4 (RGB), FFV1/x265 MKV (ToF `lossless` | `compressed`)
- **Pluggable pose engine** — `camera.pose_backend`: `mediapipe` | `yolo` | `custom`
- **Hot-plug reconnect** — `camera.reconnect_enabled`, `reconnect_interval_sec`
- Requires: `pip install -e ".[camera]"` and `ffmpeg` on host/container

### Pose backend config

```yaml
camera:
  pose_backend: mediapipe   # mediapipe | yolo | custom
  pose_model: mediapipe
  pose_plugin: ""           # e.g. my_pkg.engine.MyPoseEngine (custom only)
  tof_storage_mode: lossless
  reconnect_enabled: true
  reconnect_interval_sec: 15
```

## OAK ToF hardware tests (Docker)

No host Python venv — everything runs in the **`nilo-node:hardware`** image:

```bash
xhost +local:docker    # once per desktop session
./scripts/oak/run-in-docker.sh tof    # ToF GUI
./scripts/oak/run-in-docker.sh pose   # pose MediaPipe / YOLO
```

See [`scripts/oak/README.md`](scripts/oak/README.md).

Production deploy (`./scripts/install.sh`) builds the same **hardware** image for the mini PC with OAK.

## Phase 6 (current)

- **ManifestAdapter** + **UploadAdapter** — sync finalized chunks to NILO-backend
- **Offline upload queue** — SQLite-backed retry when backend unreachable
- **Partial-chunk recovery** — finalize stale or resume active chunks after restart
- API: `GET /api/v1/sync/status`
- Soak test guide: [docs/SOAK_TEST.md](docs/SOAK_TEST.md)

### Sync API

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/sync/status
```

## Phase 5 (current)

- **Bluetooth manager** — discover, connect, disconnect (BlueZ / mock sin hardware)
- **Grabación por micrófono** — `record_enabled` por MAC, persistido en SQLite
- **`AudioSource`** — tracks FLAC + timestamps en chunk
- API: `/api/v1/bluetooth/discover`, `/status`, `/connect`, `/disconnect`, `/mics/{mac}/recording`

### Bluetooth API

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/bluetooth/discover
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mac_address":"AA:BB:CC:DD:EE:01"}' \
  http://localhost:8080/api/v1/bluetooth/connect
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"record_enabled":false}' \
  http://localhost:8080/api/v1/bluetooth/mics/AA:BB:CC:DD:EE:01/recording
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mac_address":"AA:BB:CC:DD:EE:01"}' \
  http://localhost:8080/api/v1/bluetooth/disconnect
```

## Phase 4 (current)

- **WiFi AP** for Cardmed-Dev (`hostapd` + `dnsmasq`, mock mode in dev when no `wlan0`)
- **Cardmed API**: register, upload photos, status
- **`PhysiologySource`**: images + `index.jsonl` in active chunk
- **`GET /api/v1/devices`**: camera, Cardmed, WiFi aggregate

### Cardmed API

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"device_id":"cardmed-1","device_name":"Dev Kit"}' \
  http://localhost:8080/api/v1/cardmed/register

curl -X POST -H "Authorization: Bearer $TOKEN" \
  -F device_id=cardmed-1 \
  -F capture_ts=2026-08-03T10:00:30+00:00 \
  -F file=@photo.jpg \
  http://localhost:8080/api/v1/cardmed/photos

curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/cardmed/status
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/devices
```

SSID when WiFi AP is enabled: `nilo-node-{first-8-hex-of-node-uuid}` (see `/api/v1/node/info` → `wifi.ssid`).

### Phase 3b note (pose engine)

Pose backend is **pluggable** (`camera.pose_backend`: `mediapipe` | `yolo` | `custom`). Compare accuracy/latency on hardware before locking the default.

## Phase 3 (current)

- **OAK camera module**: discover, connect, disconnect (mock pipeline without hardware)
- **Per-stream capture toggles** from backend campaign (`rgb`, `tof`, `pose`)
- Install camera extras: `pip install -e ".[camera]"` (depthai, mediapipe, opencv)
- API: `/api/v1/camera/discover`, `/connect`, `/disconnect`, `/status`

### Camera API

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/camera/discover
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"device_id": null}' http://localhost:8080/api/v1/camera/connect
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/camera/status
```

Remote capture flags come from NILO-backend campaign `sources` block (polled automatically).

- **Local storage** on mini PC: `/data/recordings/campaigns/…`
- **Replication** (optional, modular):
  - `backend` — upload to NILO-backend (ready when endpoints configured)
  - `nas` — mirror to NAS mount (`copy` or `rsync`)
  - Modes: `realtime`, `scheduled` (daily), `manual`
- Retention, quota monitoring, delete by time range
- API + CLI for chunk management

### Storage API (requires bearer token if configured)

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/storage/usage
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/api/v1/chunks?start=2026-08-03T10:00:00+00:00&end=2026-08-03T12:00:00+00:00"
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"start":"2026-08-03T10:00:00+00:00","end":"2026-08-03T11:00:00+00:00","dry_run":true}' \
  http://localhost:8080/api/v1/chunks/delete
```

### CLI

```bash
nilo-node storage-usage --config config/nilo-node.dev.yaml
nilo-node chunks-list --start 2026-08-03T10:00:00+00:00 --end 2026-08-03T12:00:00+00:00
nilo-node chunks-delete --start ... --end ... --dry-run
```

## Quick start (development)

```bash
# Run tests
pip install -e ".[dev]"
pytest

# Start with Docker Compose (uses config/nilo-node.dev.yaml, 60s chunks)
docker compose up --build
curl http://localhost:8080/api/v1/health
curl http://localhost:8080/api/v1/node/info
```

Data is written under the `nilo-data` volume at `/data/recordings/campaigns/…`.

## Production deployment

One-liner from a cloned repo (builds image locally, installs to `/opt/nilo-node`):

```bash
sudo ./scripts/install.sh
# or with systemd auto-start on boot:
sudo INSTALL_SYSTEMD=1 ./scripts/install.sh
```

From a pre-built registry image (no git clone):

```bash
sudo NILO_IMAGE=ghcr.io/neovisions/nilo-node:latest ./scripts/deploy.sh install
```

### Deploy script commands

| Command | Description |
|---------|-------------|
| `sudo ./scripts/install.sh` | First install: Docker, config, `.env`, build/pull, start |
| `sudo ./scripts/deploy.sh update` | Pull/build latest and recreate container |
| `./scripts/deploy.sh status` | Container state + `/api/v1/health` |
| `./scripts/deploy.sh logs` | Last 200 log lines (`logs -f` to follow) |
| `sudo ./scripts/deploy.sh stop` | Stop container |
| `sudo ./scripts/deploy.sh uninstall` | Stop + optional data removal |

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `NILO_INSTALL_DIR` | `/opt/nilo-node` | Install path |
| `NILO_IMAGE` | *(empty = build)* | Docker Hub / GHCR image tag |
| `NILO_REPO` | GitHub URL | Clone source when not using image |
| `NILO_REPO_BRANCH` | `main` | Branch to deploy |
| `INSTALL_SYSTEMD` | `0` | Set `1` to register systemd unit |
| `NONINTERACTIVE` | `0` | Set `1` to auto-generate secrets |

Secrets live in `${NILO_INSTALL_DIR}/.env` (see [`deploy/env.example`](deploy/env.example)). Config: `config/nilo-node.yaml` (created from example on first install; PoE camera settings applied by `deploy.sh update`).

### OAK-D-SR-PoE (production camera)

Topology: **OAK → PoE injector → Ethernet mini PC**. Before first capture:

```bash
sudo POE_IFACE=enp2s0 ./scripts/oak/setup-poe-network.sh
ping -c 2 169.254.1.222
sudo ./scripts/deploy.sh update
```

Production uses the same DepthAI stack as the ToF test viewer (`src/nilo_node/camera/` — PoE IP, DepthAI v2/v3). Full guide: [`docs/POE_SETUP.md`](docs/POE_SETUP.md).

Hardware smoke test (optional):

```bash
sudo OAK_DEVICE_IP=169.254.1.222 ./scripts/oak/run-in-docker.sh tof
```

### Manual steps (alternative)

1. Install Ubuntu/Debian, Docker Engine, and Compose plugin.
2. Clone this repo to `/opt/nilo-node`.
3. Copy `config/nilo-node.example.yaml` → `config/nilo-node.yaml` and `deploy/env.example` → `.env`.
4. `docker compose -f docker-compose.prod.yml up -d --build`
5. Optional systemd: `sudo cp deploy/systemd/nilo-node.service /etc/systemd/system/` and enable.

Verify:

```bash
curl http://127.0.0.1:8080/api/v1/health
curl -H "Authorization: Bearer $NILO_LOCAL_API_TOKEN" http://127.0.0.1:8080/api/v1/node/info
```

## Configuration

| Variable | Description |
|----------|-------------|
| `NILO_CONFIG_PATH` | Path to YAML config (default `/etc/nilo-node/nilo-node.yaml`) |
| `NILO_BACKEND_API_KEY` | Backend API key (api_key auth mode or login fallback) |
| `NILO_BACKEND_CLIENT_ID` | JWT login client ID |
| `NILO_BACKEND_CLIENT_SECRET` | JWT login client secret |
| `NILO_LOCAL_API_TOKEN` | Local REST API bearer token |
| `NILO_WIFI_PASSWORD` | WiFi AP password (Phase 4+) |

Patient identification: every campaign, recording run, and chunk manifest includes `subject_user_id` (nullable when no patient is assigned).

## License

Proprietary — NeoVisions / NILO.
