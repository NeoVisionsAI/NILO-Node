# 24h Soak Test — NILO-Node

Operational checklist for validating Phase 6 production hardening on target mini PC hardware.

## Prerequisites

- NILO-Node deployed via Docker Compose on target hardware
- Camera, Cardmed, and Bluetooth configured (or mock modes disabled with real devices)
- Backend endpoints configured in `nilo-node.yaml`:
  - `backend.endpoints.manifest`
  - `backend.endpoints.upload`
  - `backend.adapters.manifest.enabled: true`
  - `backend.adapters.upload.enabled: true`
- Replication target `replication.targets.backend.enabled: true`

## Test procedure

1. **Baseline** — Confirm `/api/v1/health` and `/api/v1/sync/status` respond.
2. **Start capture** — Ensure an active campaign is running (backend or `dev_campaign`).
3. **Monitor 24h** — Leave the node recording continuously for at least 24 hours.
4. **Periodic checks** (every 2–4h):
   - `GET /api/v1/sync/status` — upload queue pending count should stay low when online
   - Heartbeat logs — no repeated auth failures
   - `GET /api/v1/storage/usage` — disk usage within quota
   - Verify new chunks appear under `/data/recordings/.../chunks/`
5. **Simulated offline** (optional mid-test):
   - Block outbound HTTPS to backend for 30–60 minutes
   - Confirm `upload_queue.pending` increases in sync status
   - Restore connectivity; pending jobs should drain within `upload_queue.process_interval_sec`
6. **Simulated crash** (optional end-test):
   - `docker compose kill -s SIGKILL nilo-node` during active capture
   - Restart container
   - Confirm startup logs show chunk recovery (`finalized` or `resumed`)
   - Capture resumes or partial chunk is marked `complete` with `"partial": true` in manifest

## Pass criteria

| Check | Expected |
|-------|----------|
| Uptime | No unhandled crashes over 24h |
| Chunks | All expected time windows have `complete` chunks |
| Backend sync | `upload_queue.complete` grows; `pending` ≈ 0 when online |
| Recovery | After SIGKILL restart, no orphaned `open` chunks older than chunk duration |
| Storage | Disk usage stable; retention not deleting unreplicated chunks (if configured) |

## Useful commands

```bash
# Sync status
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/sync/status

# Storage usage
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/storage/usage

# Follow logs
docker compose logs -f nilo-node
```

## Reporting

Record: node_id, firmware/image version, start/end timestamps, chunk count, max upload queue depth, any recovery events, and disk peak usage.
