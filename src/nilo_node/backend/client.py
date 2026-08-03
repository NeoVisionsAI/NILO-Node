"""NILO-backend client orchestrating auth, transport, and adapters."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nilo_node.backend.adapters.config import ConfigAdapter
from nilo_node.backend.adapters.heartbeat import HeartbeatAdapter
from nilo_node.backend.adapters.manifest import ManifestAdapter
from nilo_node.backend.adapters.physiology import PhysiologyAdapter
from nilo_node.backend.adapters.upload import UploadAdapter
from nilo_node.backend.auth.manager import AuthManager
from nilo_node.backend.auth.store import TokenStore
from nilo_node.backend.connectivity import ConnectivityState
from nilo_node.backend.exceptions import BackendEndpointNotConfiguredError
from nilo_node.backend.transport import BackendTransport
from nilo_node.config.models import AppConfig
from nilo_node.monitoring.models import Campaign, ChunkRecord

logger = logging.getLogger(__name__)


class BackendClient:
    """High-level interface to NILO-backend. Endpoints are configurable; stubs when unset."""

    def __init__(self, config: AppConfig, storage_base: Path, node_id: str) -> None:
        self._config = config
        self._node_id = node_id
        self._connectivity = ConnectivityState()

        token_path = Path(config.backend.auth.token_store_path)
        if not token_path.is_absolute():
            token_path = storage_base / token_path

        self._auth = AuthManager(config, TokenStore(token_path), node_id)
        self._transport = BackendTransport(config, self._auth, node_id)
        self._config_adapter = ConfigAdapter()
        self._heartbeat_adapter = HeartbeatAdapter()
        self._physiology_adapter = PhysiologyAdapter()
        self._manifest_adapter = ManifestAdapter()
        self._upload_adapter = UploadAdapter()
        self._started = False

    @property
    def connectivity(self) -> ConnectivityState:
        return self._connectivity

    async def start(self) -> None:
        if not self._config.backend.enabled:
            logger.info("Backend integration disabled (backend.enabled=false)")
            return

        await self._transport.start()
        self._connectivity.authenticated = self._auth.authenticated
        self._started = True

        endpoints = self._config.backend.endpoints
        configured = [
            name
            for name in (
                "login",
                "refresh",
                "campaign",
                "heartbeat",
                "manifest",
                "upload",
                "physiology",
            )
            if endpoints.is_configured(name)
        ]
        if configured:
            logger.info("Backend endpoints configured: %s", ", ".join(configured))
        else:
            logger.info(
                "No backend endpoints configured yet — set backend.endpoints.* when ready"
            )

        if self._auth.is_jwt_mode and not endpoints.login:
            logger.warning(
                "JWT auth mode enabled but backend.endpoints.login is empty — "
                "authentication will run on first configured login path"
            )

    async def close(self) -> None:
        await self._transport.close()
        self._started = False

    async def fetch_campaign(self) -> Campaign | None:
        if self._config.monitoring.dev_campaign is not None:
            return Campaign.model_validate(self._config.monitoring.dev_campaign)

        if not self._config.backend.enabled or not self._config.adapter_enabled("config"):
            return None

        endpoint = self._config.backend.endpoints.campaign
        if not endpoint:
            logger.debug("Campaign endpoint not configured — skipping remote fetch")
            return None

        if not self._started:
            await self.start()

        try:
            campaign = await self._config_adapter.fetch(
                self._transport,
                self._node_id,
                endpoint,
            )
            self._connectivity.record_success()
            self._connectivity.authenticated = self._auth.authenticated
            logger.info(
                "Campaign fetched: name=%s subject_user_id=%s",
                campaign.campaign_name,
                campaign.subject_user_id,
            )
            return campaign
        except BackendEndpointNotConfiguredError:
            return None
        except Exception as exc:
            self._connectivity.record_failure(str(exc))
            self._connectivity.authenticated = False
            logger.warning("Failed to fetch campaign from backend: %s", exc)
            return None

    async def send_heartbeat(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        connectivity = self._connectivity.to_dict()
        connectivity["within_offline_grace"] = self._connectivity.is_within_grace(
            self._config.monitoring.offline_grace_sec
        )
        payload["backend"] = connectivity

        if not self._config.backend.enabled or not self._config.adapter_enabled("heartbeat"):
            logger.debug("Heartbeat adapter disabled")
            return

        endpoint = self._config.backend.endpoints.heartbeat
        if not endpoint:
            logger.info("Heartbeat (local, endpoint not configured): node_id=%s", self._node_id)
            return

        if not self._started:
            await self.start()

        try:
            await self._heartbeat_adapter.send(
                self._transport,
                self._node_id,
                endpoint,
                payload,
            )
            self._connectivity.record_success()
            self._connectivity.authenticated = self._auth.authenticated
            logger.debug("Heartbeat sent successfully")
        except BackendEndpointNotConfiguredError:
            logger.info("Heartbeat (local): endpoint not configured")
        except Exception as exc:
            self._connectivity.record_failure(str(exc))
            logger.warning("Failed to send heartbeat: %s", exc)

    async def forward_physiology(self, payload: dict[str, Any]) -> bool:
        if not self._config.backend.enabled or not self._config.adapter_enabled("physiology"):
            logger.debug("Physiology adapter disabled")
            return False

        endpoint = self._config.backend.endpoints.physiology
        if not endpoint:
            logger.debug("Physiology forward skipped (endpoint not configured)")
            return False

        if not self._started:
            await self.start()

        try:
            return await self._physiology_adapter.forward(
                self._transport,
                self._node_id,
                endpoint,
                payload,
            )
        except BackendEndpointNotConfiguredError:
            return False
        except Exception as exc:
            self._connectivity.record_failure(str(exc))
            logger.warning("Failed to forward physiology: %s", exc)
            return False

    async def sync_chunk(self, chunk: ChunkRecord, chunk_path: Path) -> None:
        if not self._config.backend.enabled:
            return

        if not self._started:
            await self.start()

        manifest_path = chunk_path / "manifest.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            import json

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        if self._config.adapter_enabled("manifest") and self._config.backend.endpoints.manifest:
            await self.send_chunk_manifest(chunk, chunk_path, manifest=manifest)

        if self._config.adapter_enabled("upload") and self._config.backend.endpoints.upload:
            await self.upload_chunk_files(chunk, chunk_path)

        self._connectivity.record_success()
        self._connectivity.authenticated = self._auth.authenticated

    async def send_chunk_manifest(
        self,
        chunk: ChunkRecord,
        chunk_path: Path,
        *,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        endpoint = self._config.backend.endpoints.manifest
        if not endpoint:
            raise BackendEndpointNotConfiguredError(
                "backend.endpoints.manifest is not configured"
            )

        if manifest is None:
            import json

            manifest_path = chunk_path / "manifest.json"
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists()
                else {}
            )

        await self._manifest_adapter.send(
            self._transport,
            self._node_id,
            endpoint,
            chunk_id=chunk.chunk_id,
            manifest=manifest,
        )

    async def upload_chunk_files(self, chunk: ChunkRecord, chunk_path: Path) -> None:
        endpoint = self._config.backend.endpoints.upload
        if not endpoint:
            raise BackendEndpointNotConfiguredError(
                "backend.endpoints.upload is not configured"
            )

        await self._upload_adapter.send(
            self._transport,
            self._node_id,
            endpoint,
            chunk_id=chunk.chunk_id,
            chunk_path=chunk_path,
        )
