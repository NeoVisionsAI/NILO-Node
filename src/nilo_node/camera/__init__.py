"""OAK camera capture — PoE/USB discovery, DepthAI v2/v3 pipeline, production manager."""

from nilo_node.camera.device_connect import (
    DEFAULT_POE_CAMERA_IP,
    DEFAULT_POE_HOST_IP,
    device_info_for_ip,
    format_devices_report,
    list_devices,
    resolve_device_info,
    should_use_depthai_hardware,
    uses_depthai_v2,
)
from nilo_node.camera.discovery import depthai_available, discover_devices
from nilo_node.camera.manager import CameraManager
from nilo_node.camera.models import CameraConnectionState, CameraDeviceInfo, CameraStatus, CaptureFlags
from nilo_node.camera.oak_sr_session import OakFrameSet, OakSrSession
from nilo_node.camera.oak_tof_pipeline import OakSrGraph, depth_to_colormap, open_oak_sr_graph

__all__ = [
    "DEFAULT_POE_CAMERA_IP",
    "DEFAULT_POE_HOST_IP",
    "CameraConnectionState",
    "CameraDeviceInfo",
    "CameraManager",
    "CameraStatus",
    "CaptureFlags",
    "OakFrameSet",
    "OakSrGraph",
    "OakSrSession",
    "depth_to_colormap",
    "depthai_available",
    "device_info_for_ip",
    "discover_devices",
    "format_devices_report",
    "list_devices",
    "open_oak_sr_graph",
    "resolve_device_info",
    "should_use_depthai_hardware",
    "uses_depthai_v2",
]
