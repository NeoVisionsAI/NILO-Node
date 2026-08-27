"""Pydantic configuration models."""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from nilo_node.backend.endpoints import BackendEndpoints

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


class RetryConfig(BaseModel):
    max_attempts: int = 5
    backoff_sec: float = 2.0


class AdapterToggle(BaseModel):
    enabled: bool = True


class BackendAuthConfig(BaseModel):
    """JWT / API-key authentication settings."""

    mode: Literal["none", "api_key", "jwt"] = "jwt"
    client_id: str = ""
    client_secret: str = ""
    token_store_path: str = "/data/backend/auth_tokens.json"
    refresh_skew_sec: int = 60
    login_grant: Literal["client_credentials", "node_credentials"] = "client_credentials"


class UploadQueueConfig(BaseModel):
    enabled: bool = True
    process_interval_sec: int = 60
    max_attempts: int = 10


class BackendConfig(BaseModel):
    enabled: bool = True
    base_url: str = "https://api.nilo.example"
    api_key: str = ""
    heartbeat_interval_sec: int = 30
    config_poll_interval_sec: int = 300
    request_timeout_sec: int = 15
    retry: RetryConfig = Field(default_factory=RetryConfig)
    auth: BackendAuthConfig = Field(default_factory=BackendAuthConfig)
    endpoints: BackendEndpoints = Field(default_factory=BackendEndpoints)
    adapters: dict[str, AdapterToggle] = Field(default_factory=dict)
    upload_queue: UploadQueueConfig = Field(default_factory=UploadQueueConfig)


class MonitoringConfig(BaseModel):
    offline_grace_sec: int = 3600
    default_chunk_duration_sec: int = 300
    schedule_tick_sec: int = 5
    dev_campaign: dict[str, Any] | None = None
    recover_on_startup: bool = True


class SourceConfig(BaseModel):
    enabled: bool = True
    plugin: str = "nilo_node.sources.stub.StubSource"


class CameraDefaultsConfig(BaseModel):
    rgb_enabled: bool = True
    tof_enabled: bool = True
    pose_enabled: bool = True


class CameraConfig(BaseModel):
    enabled: bool = True
    device_id: str = ""
    device_ip: str = ""
    connection_mode: Literal["auto", "usb", "poe"] = "auto"
    auto_connect: bool = True
    mock_when_unavailable: bool = True
    rgb_fps: int = 30
    tof_fps: int = 30
    pose_fps: int = 15
    pose_backend: Literal["mediapipe", "yolo", "custom"] = "mediapipe"
    pose_model: str = "mediapipe"
    pose_plugin: str = ""
    tof_storage_mode: Literal["lossless", "compressed"] = "lossless"
    reconnect_enabled: bool = True
    reconnect_interval_sec: int = 15
    defaults: CameraDefaultsConfig = Field(default_factory=CameraDefaultsConfig)


class StorageConfig(BaseModel):
    base_path: str = "/data"
    recordings_dir: str = "recordings"
    max_usage_percent: int = 85
    retention_days: int = 30
    retention_check_interval_sec: int = 3600
    delete_only_if_replicated: bool = False


class BackendReplicationTarget(BaseModel):
    enabled: bool = False


class NasReplicationTarget(BaseModel):
    enabled: bool = False
    mount_path: str = "/mnt/nilo-nas"
    relative_path: str = "nilo-node"
    method: Literal["copy", "rsync"] = "copy"


class ReplicationTargetsConfig(BaseModel):
    backend: BackendReplicationTarget = Field(default_factory=BackendReplicationTarget)
    nas: NasReplicationTarget = Field(default_factory=NasReplicationTarget)


class ReplicationConfig(BaseModel):
    enabled: bool = False
    mode: Literal["realtime", "scheduled", "manual"] = "realtime"
    daily_at: str = "02:00"
    process_interval_sec: int = 60
    max_attempts: int = 5
    delete_local_after_replicated: bool = False
    targets: ReplicationTargetsConfig = Field(default_factory=ReplicationTargetsConfig)


class CardmedConfig(BaseModel):
    max_upload_size_mb: int = 10
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp"]
    )
    forward_to_backend: bool = True


class BluetoothConfig(BaseModel):
    enabled: bool = True
    adapter: str = "hci0"
    mock_when_unavailable: bool = True
    auto_power_on: bool = True
    scan_timeout_sec: int = 10
    sample_rate: int = 16000
    channels: int = 1
    format: Literal["flac"] = "flac"
    default_record_on_connect: bool = True


class WifiConfig(BaseModel):
    enabled: bool = True
    ssid_prefix: str = "nilo-node"
    password: str = ""
    interface: str = "auto"
    channel: int = 6
    country_code: str = "ES"
    mock_when_unavailable: bool = True
    dhcp_range_start: str = "192.168.50.10"
    dhcp_range_end: str = "192.168.50.100"
    ap_ip: str = "192.168.50.1"
    netmask: str = "255.255.255.0"
    # Ethernet stays on DHCP/static for internet; AP is isolated on wlan0.
    configure_interface_ip: bool = True


class MqttConfig(BaseModel):
    enabled: bool = False
    broker_host: str = "nilomed.eu"
    broker_port: int = 8883
    username: str = ""
    password: str = ""
    use_tls: bool = True
    topic_template: str = "nilo/node/{node_id}"
    events_topic_template: str = "nilo/node/{node_id}/events"
    require_message_token: bool = True
    reconnect_interval_sec: int = 10
    qos: int = 1
    mock_when_unavailable: bool = True


class LocalApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    auth_token: str = ""
    setup_enabled: bool = True
    setup_username: str = ""
    setup_password: str = ""


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: Literal["json", "text"] = "json"


class NodeConfig(BaseModel):
    id: str = ""
    name: str = "nilo-node"


class AppConfig(BaseModel):
    node: NodeConfig = Field(default_factory=NodeConfig)
    backend: BackendConfig = Field(default_factory=BackendConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    replication: ReplicationConfig = Field(default_factory=ReplicationConfig)
    wifi: WifiConfig = Field(default_factory=WifiConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)
    cardmed: CardmedConfig = Field(default_factory=CardmedConfig)
    bluetooth: BluetoothConfig = Field(default_factory=BluetoothConfig)
    local_api: LocalApiConfig = Field(default_factory=LocalApiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("sources", mode="before")
    @classmethod
    def _default_sources(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value:
            return value
        return {
            "rgb": {"enabled": True},
            "tof": {"enabled": True},
            "pose": {"enabled": True},
            "audio": {"enabled": True},
            "physiology": {"enabled": True},
        }

    def adapter_enabled(self, name: str) -> bool:
        toggle = self.backend.adapters.get(name)
        return toggle.enabled if toggle else False
