"""NILO-Node orchestrator entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import uvicorn

from nilo_node import __version__
from nilo_node.api.app import create_app
from nilo_node.backend.client import BackendClient
from nilo_node.backend.upload_queue import UploadQueueService
from nilo_node.bluetooth.manager import BluetoothManager
from nilo_node.camera.manager import CameraManager
from nilo_node.cardmed.service import CardmedService
from nilo_node.chunks.coordinator import ChunkCoordinator
from nilo_node.chunks.recovery import ChunkRecoveryService
from nilo_node.config.loader import load_config
from nilo_node.config.applier import SettingsApplier
from nilo_node.config.runtime_store import RuntimeSettingsStore
from nilo_node.config.models import AppConfig
from nilo_node.health.reporter import HealthReporter
from nilo_node.mqtt.handlers import wire_mqtt_handlers
from nilo_node.mqtt.service import MqttService
from nilo_node.monitoring.controller import CampaignController
from nilo_node.network.wifi_manager import WifiApManager
from nilo_node.monitoring.models import (
    AlwaysSchedule,
    Campaign,
    CampaignStatus,
    ChunkRecord,
    SourceToggle,
)
from nilo_node.sources.physiology.store import PhysiologyStore
from nilo_node.sources.registry import (
    build_sources,
    init_bluetooth_manager,
    init_camera_manager,
)
from nilo_node.state.database import Database
from nilo_node.state.repository import StateRepository
from nilo_node.storage.manager import StorageManager
from nilo_node.storage.paths import StoragePaths
from nilo_node.storage.replication.manager import ReplicationManager, enabled_replication_target_ids

logger = logging.getLogger(__name__)

CONFIG_PATH_ENV = "NILO_CONFIG_PATH"
DEFAULT_CONFIG_PATH = "/etc/nilo-node/nilo-node.yaml"


def _setup_logging(config: AppConfig) -> None:
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )


def _ensure_node_id(config: AppConfig, storage_base: Path) -> str:
    if config.node.id:
        return config.node.id

    node_id_file = storage_base / "node_id"
    if node_id_file.exists():
        return node_id_file.read_text(encoding="utf-8").strip()

    node_id = str(uuid.uuid4())
    node_id_file.parent.mkdir(parents=True, exist_ok=True)
    node_id_file.write_text(node_id, encoding="utf-8")
    return node_id


def _default_dev_campaign(config: AppConfig) -> Campaign:
    return Campaign(
        campaign_id=str(uuid.uuid4()),
        campaign_name="dev_capture",
        subject_user_id=None,
        status=CampaignStatus.ACTIVE,
        chunk_duration_sec=config.monitoring.default_chunk_duration_sec,
        timezone="UTC",
        schedule=AlwaysSchedule(),
        sources={
            name: SourceToggle(enabled=cfg.enabled)
            for name, cfg in config.sources.items()
        },
    )


async def _config_poll_loop(
    config: AppConfig,
    backend: BackendClient,
    controller: CampaignController,
    camera: CameraManager,
    bluetooth: BluetoothManager,
    repo: StateRepository,
) -> None:
    interval = config.backend.config_poll_interval_sec
    while True:
        campaign = await backend.fetch_campaign()

        if campaign is None:
            cached = repo.get_active_campaign()
            if cached and backend.connectivity.is_within_grace(
                config.monitoring.offline_grace_sec
            ):
                logger.warning(
                    "Using cached campaign (backend unreachable, within offline grace)"
                )
                campaign = cached
            elif cached and config.monitoring.dev_campaign is None:
                logger.error(
                    "Backend unreachable and offline grace expired — keeping last cached campaign"
                )
                campaign = cached
            elif config.monitoring.dev_campaign is None:
                campaign = _default_dev_campaign(config)

        if campaign is not None:
            controller.set_campaign(campaign)
            camera.set_campaign(campaign)
            bluetooth.set_campaign(campaign)
            repo.save_config_snapshot(campaign.model_dump(mode="json"))
        await asyncio.sleep(interval)


async def _schedule_tick_loop(
    config: AppConfig,
    controller: CampaignController,
) -> None:
    interval = config.monitoring.schedule_tick_sec
    while True:
        await controller.tick()
        await asyncio.sleep(interval)


async def _retention_loop(config: AppConfig, storage_manager: StorageManager) -> None:
    interval = config.storage.retention_check_interval_sec
    while True:
        await asyncio.sleep(interval)
        try:
            result = storage_manager.apply_retention()
            if result.deleted_count:
                logger.info(
                    "Retention deleted %d chunks (%d bytes)",
                    result.deleted_count,
                    result.freed_bytes,
                )
        except Exception as exc:
            logger.warning("Retention check failed: %s", exc)


async def run_async(config_path: str | None = None) -> None:
    path = config_path or os.environ.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH)
    config = load_config(path)
    _setup_logging(config)

    storage_base = Path(config.storage.base_path)
    storage_base.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(storage_base, config.storage.recordings_dir)

    node_id = _ensure_node_id(config, storage_base)
    logger.info("Starting NILO-Node v%s node_id=%s", __version__, node_id)

    db = Database(storage_base / "nilo-node.db")
    db.migrate()
    repo = StateRepository(db)

    settings_store = RuntimeSettingsStore(db, storage_base)
    config_path = Path(path)

    camera_manager = CameraManager(config)
    init_camera_manager(camera_manager)

    bluetooth_manager = BluetoothManager(config, repo)
    await bluetooth_manager.start()
    init_bluetooth_manager(bluetooth_manager)

    physiology_store = PhysiologyStore(config.cardmed, repo)
    sources = build_sources(
        config,
        physiology_store=physiology_store,
        bluetooth_manager=bluetooth_manager,
    )
    backend = BackendClient(config, storage_base, node_id)
    await backend.start()

    upload_queue = UploadQueueService(config, repo)

    cardmed_service = CardmedService(
        config, repo, node_id, physiology_store, backend
    )
    wifi_manager = WifiApManager(config, storage_base, node_id)

    settings_applier = SettingsApplier(
        config,
        config_path,
        camera=camera_manager,
        wifi=wifi_manager,
        bluetooth=bluetooth_manager,
    )
    saved_settings = settings_store.load()
    # WiFi is started by deploy (POST /wifi/restart), not during boot — avoids uap0 races.
    if any(
        [
            saved_settings.camera,
            saved_settings.bluetooth,
            saved_settings.mqtt,
        ]
    ):
        from nilo_node.config.runtime_store import RuntimeSettings

        await settings_applier.apply(
            RuntimeSettings(
                camera=saved_settings.camera,
                bluetooth=saved_settings.bluetooth,
                mqtt=saved_settings.mqtt,
            )
        )
        logger.info("Applied persisted runtime settings from database")

    mqtt_service = MqttService(config, node_id)
    wire_mqtt_handlers(
        mqtt_service,
        camera=camera_manager,
        bluetooth=bluetooth_manager,
        wifi=wifi_manager,
        settings_store=settings_store,
        settings_applier=settings_applier,
    )
    await mqtt_service.start()

    storage_manager = StorageManager(
        config, repo, paths, enabled_target_ids=enabled_replication_target_ids(config)
    )
    replication_manager = ReplicationManager(
        config, repo, paths, backend, storage_manager, upload_queue
    )

    async def on_chunk_finalized(chunk: ChunkRecord) -> None:
        await replication_manager.on_chunk_finalized(chunk)

    chunk_coordinator = ChunkCoordinator(
        repo,
        paths,
        sources,
        on_finalized=on_chunk_finalized,
    )
    campaign_controller = CampaignController(
        repo, chunk_coordinator, sources, paths, node_id
    )

    recovery = ChunkRecoveryService(config, repo, chunk_coordinator, campaign_controller)
    recovery_report = await recovery.run_startup_recovery()
    if any(recovery_report.values()):
        logger.info("Startup chunk recovery: %s", recovery_report)
    health = HealthReporter(
        config,
        repo,
        backend,
        campaign_controller,
        storage_manager,
        camera_manager,
        cardmed_service,
        wifi_manager,
        bluetooth_manager,
        upload_queue,
        node_id,
        __version__,
    )

    replication_manager.start()
    upload_queue.start(backend)
    health.start()

    config_task = asyncio.create_task(
        _config_poll_loop(
            config, backend, campaign_controller, camera_manager, bluetooth_manager, repo
        )
    )
    schedule_task = asyncio.create_task(
        _schedule_tick_loop(config, campaign_controller)
    )
    retention_task = asyncio.create_task(_retention_loop(config, storage_manager))

    app = create_app(
        node_id,
        config,
        campaign_controller,
        storage_manager,
        replication_manager,
        camera_manager,
        repo,
        cardmed_service,
        wifi_manager,
        bluetooth_manager,
        backend,
        upload_queue,
        mqtt_service=mqtt_service,
        config_path=path,
        settings_store=settings_store,
        settings_applier=settings_applier,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.local_api.host,
            port=config.local_api.port,
            log_level=config.logging.level.lower(),
        )
    )
    api_task = asyncio.create_task(server.serve())

    try:
        await asyncio.gather(config_task, schedule_task, retention_task, api_task)
    finally:
        await mqtt_service.stop()
        await wifi_manager.stop()
        await bluetooth_manager.stop()
        await health.stop()
        await upload_queue.stop()
        await replication_manager.stop()
        await backend.close()
        db.close()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in {
        "storage-usage",
        "chunks-list",
        "chunks-delete",
    }:
        from nilo_node.cli import cli_main

        raise SystemExit(cli_main(sys.argv[1:]))

    asyncio.run(run_async())


if __name__ == "__main__":
    main()
