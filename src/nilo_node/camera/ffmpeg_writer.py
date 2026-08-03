"""FFmpeg pipe writers for RGB (H.264) and ToF (FFV1) streams."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np

from nilo_node.camera.writers import ChunkWriter

logger = logging.getLogger(__name__)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class FfmpegRgbWriter(ChunkWriter):
    """Encode BGR uint8 frames to H.264 MP4 via ffmpeg stdin pipe."""

    def __init__(
        self,
        output_dir: Path,
        *,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ) -> None:
        super().__init__(output_dir)
        self._width = width
        self._height = height
        self._fps = fps
        self._output_path = output_dir / "video.mp4"
        self._proc: subprocess.Popen[bytes] | None = None
        self._use_ffmpeg = ffmpeg_available()
        if self._use_ffmpeg:
            self._start_ffmpeg()

    def _start_ffmpeg(self) -> None:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(self._output_path),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write_frame(self, timestamp: float, data: object) -> None:
        self.timestamps.append(timestamp)
        self.frame_count += 1
        if not self._use_ffmpeg or self._proc is None or self._proc.stdin is None:
            return
        frame = self._coerce_bgr_frame(data)
        try:
            self._proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            logger.warning("FFmpeg RGB pipe closed unexpectedly")
            self._use_ffmpeg = False

    def _coerce_bgr_frame(self, data: object) -> np.ndarray:
        if isinstance(data, np.ndarray) and data.ndim == 3:
            if data.shape[0] == self._height and data.shape[1] == self._width:
                return data.astype(np.uint8, copy=False)
        return np.zeros((self._height, self._width, 3), dtype=np.uint8)

    def finalize(self) -> dict:
        if self._use_ffmpeg and self._proc is not None:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            self._proc.wait(timeout=120)
        elif not self._output_path.exists():
            self._output_path.write_bytes(b"")

        ts_file = self._save_timestamps("timestamps.npy")
        return {
            "path": f"sources/rgb/{self._output_path.name}",
            "timestamps_path": f"sources/rgb/{ts_file}",
            "frame_count": self.frame_count,
            "fps": self._fps,
            "codec": "h264",
            "width": self._width,
            "height": self._height,
            "mock": False,
            "encoder": "ffmpeg" if self._use_ffmpeg else "stub",
        }


class FfmpegTofWriter(ChunkWriter):
    """Encode uint16 depth frames to FFV1 MKV (lossless) or HEVC gray16 (compressed)."""

    def __init__(
        self,
        output_dir: Path,
        *,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        storage_mode: str = "lossless",
    ) -> None:
        super().__init__(output_dir)
        self._width = width
        self._height = height
        self._fps = fps
        self._storage_mode = storage_mode
        self._output_path = output_dir / "depth.mkv"
        self._proc: subprocess.Popen[bytes] | None = None
        self._use_ffmpeg = ffmpeg_available()
        if self._use_ffmpeg:
            self._start_ffmpeg()

    def _start_ffmpeg(self) -> None:
        if self._storage_mode == "compressed":
            codec_args = ["-c:v", "libx265", "-pix_fmt", "gray16le"]
        else:
            codec_args = ["-c:v", "ffv1", "-pix_fmt", "gray16le"]

        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray16le",
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            *codec_args,
            str(self._output_path),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write_frame(self, timestamp: float, data: object) -> None:
        self.timestamps.append(timestamp)
        self.frame_count += 1
        if not self._use_ffmpeg or self._proc is None or self._proc.stdin is None:
            return
        frame = self._coerce_depth_frame(data)
        try:
            self._proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            logger.warning("FFmpeg ToF pipe closed unexpectedly")
            self._use_ffmpeg = False

    def _coerce_depth_frame(self, data: object) -> np.ndarray:
        if isinstance(data, np.ndarray) and data.ndim == 2:
            if data.shape[0] == self._height and data.shape[1] == self._width:
                return data.astype(np.uint16, copy=False)
        return np.zeros((self._height, self._width), dtype=np.uint16)

    def finalize(self) -> dict:
        if self._use_ffmpeg and self._proc is not None:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            self._proc.wait(timeout=120)
        elif not self._output_path.exists():
            self._output_path.write_bytes(b"")

        ts_file = self._save_timestamps("timestamps.npy")
        codec = "ffv1" if self._storage_mode == "lossless" else "libx265"
        return {
            "path": f"sources/tof/{self._output_path.name}",
            "timestamps_path": f"sources/tof/{ts_file}",
            "frame_count": self.frame_count,
            "fps": self._fps,
            "codec": codec,
            "pixel_format": "gray16le",
            "depth_unit": "mm",
            "dtype": "uint16",
            "width": self._width,
            "height": self._height,
            "storage_mode": self._storage_mode,
            "mock": False,
            "encoder": "ffmpeg" if self._use_ffmpeg else "stub",
        }
