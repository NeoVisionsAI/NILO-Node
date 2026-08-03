from nilo_node.sources.camera_stream import CameraStreamSource
from nilo_node.sources.registry import get_camera_manager


class RgbSource(CameraStreamSource):
    def __init__(self, source_id: str, camera=None) -> None:
        super().__init__(source_id, camera or get_camera_manager())
