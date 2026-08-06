"""OAK-D-SR ToF + RGB pipeline — DepthAI 2.x (XLink) and 3.x (createOutputQueue) compatible."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

TOF_SOCKET_NAME = "CAM_A"


class _TofColormapDefaults:
    phaseUnwrappingLevel = 4


@dataclass
class OakSrGraph:
    """Live OAK-D-SR capture graph (v2 Device queue or v3 pipeline queue API)."""

    api: str
    pipeline: Any
    device: Any
    tof_config: Any | None
    depth_queue: Any
    rgb_queue: Any | None
    started: bool = False

    def close(self) -> None:
        if self.api == "v3" and self.started:
            try:
                self.pipeline.stop()
            except Exception as exc:
                logger.debug("pipeline.stop: %s", exc)
            try:
                if hasattr(self.pipeline, "wait"):
                    self.pipeline.wait()
            except Exception:
                pass
        if self.device is not None:
            try:
                self.device.close()
            except Exception as exc:
                logger.debug("device.close: %s", exc)

    def poll_depth(self) -> Any | None:
        frame = _queue_try_get(self.depth_queue)
        if frame is None:
            return None
        if hasattr(frame, "getFrame"):
            depth = frame.getFrame()
        else:
            depth = frame.getCvFrame()
        if depth.dtype.kind != "u":
            depth = depth.astype("uint16")
        return depth

    def poll_rgb_bgr(self) -> Any | None:
        frame = _queue_try_get(self.rgb_queue)
        if frame is None:
            return None
        bgr = frame.getCvFrame()
        if bgr is None:
            return None
        if len(bgr.shape) == 3 and bgr.shape[2] == 3:
            return bgr
        return bgr


from nilo_node.camera.device_connect import uses_depthai_v2


def _tof_socket(dai: Any) -> Any:
    return getattr(dai.CameraBoardSocket, TOF_SOCKET_NAME, dai.CameraBoardSocket.CAM_A)


def _pick_rgb_socket(dai: Any) -> Any:
    for name in ("CAM_C", "CAM_B"):
        socket = getattr(dai.CameraBoardSocket, name, None)
        if socket is not None:
            return socket
    return dai.CameraBoardSocket.CAM_B


def _set_rgb_resolution(cam_rgb: Any, dai: Any) -> None:
    props = dai.ColorCameraProperties.SensorResolution
    for name in ("THE_800_P", "THE_720_P", "THE_1080_P"):
        res = getattr(props, name, None)
        if res is None:
            continue
        try:
            cam_rgb.setResolution(res)
            return
        except Exception:
            continue


def _configure_tof_node_v2(tof: Any) -> Any:
    tof_config = tof.initialConfig.get()
    tof_config.enableOpticalCorrection = True
    tof_config.enablePhaseShuffleTemporalFilter = True
    tof_config.phaseUnwrappingLevel = 4
    tof_config.phaseUnwrapErrorThreshold = 300
    tof_config.enableTemperatureCorrection = False
    tof.initialConfig.set(tof_config)
    return tof_config


def _tof_profile(dai: Any) -> Any | None:
    profile_enum = getattr(getattr(dai, "ToFConfig", None), "Profile", None)
    if profile_enum is None:
        return None
    for name in ("MID_RANGE", "SHORT_RANGE", "LONG_RANGE"):
        value = getattr(profile_enum, name, None)
        if value is not None:
            return value
    return None


def _link_tof_camera_v2(dai: Any, pipeline: Any, tof: Any, *, fps: int) -> None:
    cam_tof = pipeline.create(dai.node.Camera)
    cam_tof.setBoardSocket(_tof_socket(dai))
    cam_tof.setFps(max(fps * 2, 30))
    if hasattr(cam_tof, "setImageOrientation"):
        cam_tof.setImageOrientation(dai.CameraImageOrientation.ROTATE_180_DEG)
    cam_tof.raw.link(tof.input)


def _call_build(node: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Call node.build() — v3 Python bindings often require positional args only."""
    build = node.build
    if args:
        try:
            built = build(*args)
            return built if built is not None else node
        except (TypeError, RuntimeError):
            if not kwargs:
                raise
    if kwargs:
        built = build(**kwargs)
        return built if built is not None else node
    built = build()
    return built if built is not None else node


def _build_tof_v3_node(pipeline: Any, dai: Any, *, fps: int) -> Any:
    tof_node = pipeline.create(dai.node.ToF)
    socket = _tof_socket(dai)
    auto_socket = getattr(dai.CameraBoardSocket, "AUTO", socket)
    profile = _tof_profile(dai)
    tof_fps = float(max(fps * 2, 30))

    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    if profile is not None:
        attempts.extend(
            [
                ((auto_socket, profile), {}),
                ((auto_socket, profile, tof_fps), {}),
                ((socket, profile), {}),
                ((socket, profile, tof_fps), {}),
                ((), {"boardSocket": auto_socket, "profile": profile, "fps": tof_fps}),
                ((), {"boardSocket": auto_socket, "profile": profile}),
            ]
        )
    attempts.extend(
        [
            ((auto_socket,), {}),
            ((auto_socket, tof_fps), {}),
            ((socket,), {}),
            ((socket, tof_fps), {}),
            ((), {"boardSocket": auto_socket, "fps": tof_fps}),
            ((), {"boardSocket": auto_socket}),
        ]
    )

    last_exc: Exception | None = None
    for args, kwargs in attempts:
        try:
            tof = _call_build(tof_node, *args, **kwargs)
            logger.info("ToF build OK args=%s kwargs=%s", args, kwargs)
            return tof
        except Exception as exc:
            last_exc = exc
            logger.debug("ToF build attempt failed args=%s kwargs=%s: %s", args, kwargs, exc)

    raise RuntimeError(f"ToF build() failed for OAK-D-SR: {last_exc}") from last_exc


def _request_camera_output(
    cam: Any,
    dai: Any,
    *,
    width: int,
    height: int,
    fps: int,
) -> Any:
    img_type = _rgb_img_type(dai)
    size = (width, height)
    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    if img_type is not None:
        attempts.extend(
            [
                ((size, img_type), {}),
                ((size, img_type, float(fps)), {}),
                ((), {"size": size, "type": img_type, "fps": float(fps)}),
                ((), {"size": size, "type": img_type}),
            ]
        )
    attempts.extend(
        [
            ((size,), {}),
            ((size, float(fps)), {}),
            ((), {"size": size, "fps": float(fps)}),
            ((), {"size": size}),
        ]
    )

    last_exc: Exception | None = None
    for args, kwargs in attempts:
        try:
            if kwargs:
                return cam.requestOutput(**kwargs)
            return cam.requestOutput(*args)
        except Exception as exc:
            last_exc = exc
            logger.debug("requestOutput attempt failed args=%s kwargs=%s: %s", args, kwargs, exc)

    raise RuntimeError(f"Camera requestOutput failed: {last_exc}") from last_exc


def _build_rgb_v3_node(pipeline: Any, dai: Any, *, fps: int, rgb_width: int, rgb_height: int) -> Any:
    cam_node = pipeline.create(dai.node.Camera)
    socket = _pick_rgb_socket(dai)

    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = [
        ((socket,), {}),
        ((socket, None, float(fps)), {}),
        ((), {"boardSocket": socket}),
        ((), {"boardSocket": socket, "fps": float(fps)}),
    ]

    last_exc: Exception | None = None
    for args, kwargs in attempts:
        try:
            cam = _call_build(cam_node, *args, **kwargs)
            rgb_out = _request_camera_output(
                cam, dai, width=rgb_width, height=rgb_height, fps=fps
            )
            logger.info("RGB camera build OK socket=%s", socket)
            return rgb_out
        except Exception as exc:
            last_exc = exc
            logger.debug("RGB build attempt failed args=%s kwargs=%s: %s", args, kwargs, exc)

    raise RuntimeError(f"RGB Camera build() failed: {last_exc}") from last_exc


def _create_output_queue(output: Any, *, max_size: int = 4, blocking: bool = False) -> Any:
    try:
        return output.createOutputQueue(maxSize=max_size, blocking=blocking)
    except TypeError:
        try:
            return output.createOutputQueue(max_size, blocking)
        except TypeError:
            return output.createOutputQueue()


def _queue_try_get(queue: Any | None) -> Any | None:
    if queue is None:
        return None
    if hasattr(queue, "tryGet"):
        return queue.tryGet()
    if hasattr(queue, "has") and queue.has():
        return queue.get()
    return None


def _rgb_img_type(dai: Any) -> Any | None:
    frame_type = getattr(dai, "ImgFrame", None)
    if frame_type is None:
        return None
    type_enum = getattr(frame_type, "Type", None)
    if type_enum is None:
        return None
    for name in ("BGR888p", "RGB888p", "BGR888i", "RGB888i"):
        value = getattr(type_enum, name, None)
        if value is not None:
            return value
    return None


def _build_pipeline_v2(
    dai: Any,
    *,
    fps: int,
    include_rgb: bool,
    rgb_width: int,
    rgb_height: int,
) -> tuple[Any, Any | None]:
    if not hasattr(dai.node, "ToF"):
        raise RuntimeError(
            "DepthAI SDK missing ToF node. Install depthai>=2.24 for OAK-D-SR (PoE or USB)."
        )

    pipeline = dai.Pipeline()
    tof = pipeline.create(dai.node.ToF)
    tof_config: Any | None = None

    if hasattr(tof, "initialConfig"):
        tof_config = _configure_tof_node_v2(tof)
        if hasattr(dai.node, "Camera"):
            _link_tof_camera_v2(dai, pipeline, tof, fps=fps)
    else:
        tof_config = _TofColormapDefaults()
        if hasattr(tof, "setNumShaves"):
            tof.setNumShaves(1)
        if hasattr(dai.node, "Camera"):
            _link_tof_camera_v2(dai, pipeline, tof, fps=fps)

    xout_depth = pipeline.create(dai.node.XLinkOut)
    xout_depth.setStreamName("depth")
    tof.depth.link(xout_depth.input)

    if include_rgb and hasattr(dai.node, "ColorCamera"):
        cam_rgb = pipeline.create(dai.node.ColorCamera)
        cam_rgb.setBoardSocket(_pick_rgb_socket(dai))
        _set_rgb_resolution(cam_rgb, dai)
        cam_rgb.setInterleaved(False)
        cam_rgb.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam_rgb.setFps(fps)
        cam_rgb.setPreviewSize(rgb_width, rgb_height)
        xout_rgb = pipeline.create(dai.node.XLinkOut)
        xout_rgb.setStreamName("rgb")
        cam_rgb.preview.link(xout_rgb.input)

    logger.info("Pipeline: OAK-D-SR ToF (DepthAI v2 XLink API)")
    return pipeline, tof_config


def _build_pipeline_v3(
    dai: Any,
    pipeline: Any,
    *,
    fps: int,
    include_rgb: bool,
    rgb_width: int,
    rgb_height: int,
) -> tuple[Any | None, Any, Any | None]:
    if not hasattr(dai.node, "ToF"):
        raise RuntimeError(
            "DepthAI SDK missing ToF node. Install depthai>=2.24 for OAK-D-SR (PoE or USB)."
        )

    tof_config = _TofColormapDefaults()
    tof = _build_tof_v3_node(pipeline, dai, fps=fps)
    depth_queue = _create_output_queue(tof.depth)
    rgb_queue = None

    if include_rgb and hasattr(dai.node, "Camera"):
        try:
            rgb_out = _build_rgb_v3_node(
                pipeline, dai, fps=fps, rgb_width=rgb_width, rgb_height=rgb_height
            )
            rgb_queue = _create_output_queue(rgb_out)
        except Exception as exc:
            logger.warning("RGB stream unavailable (%s) — depth-only mode", exc)

    logger.info("Pipeline: OAK-D-SR ToF (DepthAI v3 queue API)")
    return tof_config, depth_queue, rgb_queue


def open_oak_sr_graph(
    dai: Any,
    device_info: Any,
    *,
    fps: int = 30,
    include_rgb: bool = True,
    rgb_width: int = 640,
    rgb_height: int = 480,
) -> OakSrGraph:
    """Open a connected OAK-D-SR graph on the given device (PoE IP or USB MXID)."""
    if uses_depthai_v2(dai):
        pipeline, tof_config = _build_pipeline_v2(
            dai,
            fps=fps,
            include_rgb=include_rgb,
            rgb_width=rgb_width,
            rgb_height=rgb_height,
        )
        device = dai.Device(pipeline, device_info)
        depth_queue = device.getOutputQueue("depth", maxSize=4, blocking=False)
        rgb_queue = None
        if include_rgb:
            try:
                rgb_queue = device.getOutputQueue("rgb", maxSize=4, blocking=False)
            except Exception:
                rgb_queue = None
        return OakSrGraph(
            api="v2",
            pipeline=pipeline,
            device=device,
            tof_config=tof_config,
            depth_queue=depth_queue,
            rgb_queue=rgb_queue,
            started=True,
        )

    device = dai.Device(device_info)
    pipeline = dai.Pipeline(device)
    tof_config, depth_queue, rgb_queue = _build_pipeline_v3(
        dai,
        pipeline,
        fps=fps,
        include_rgb=include_rgb,
        rgb_width=rgb_width,
        rgb_height=rgb_height,
    )
    pipeline.start()
    return OakSrGraph(
        api="v3",
        pipeline=pipeline,
        device=device,
        tof_config=tof_config,
        depth_queue=depth_queue,
        rgb_queue=rgb_queue,
        started=True,
    )


def build_oak_sr_pipeline(
    dai: Any,
    *,
    fps: int = 30,
    include_rgb: bool = True,
    rgb_width: int = 640,
    rgb_height: int = 480,
) -> tuple[Any, Any | None]:
    """Build pipeline only (DepthAI v2). v3 callers should use open_oak_sr_graph()."""
    return _build_pipeline_v2(
        dai,
        fps=fps,
        include_rgb=include_rgb,
        rgb_width=rgb_width,
        rgb_height=rgb_height,
    )


def depth_to_colormap(depth_mm: Any, tof_config: Any | None = None) -> Any:
    import cv2
    import numpy as np

    if depth_mm.size == 0:
        return np.zeros((480, 640, 3), dtype=np.uint8)

    level = 4
    if tof_config is not None:
        level = int(getattr(tof_config, "phaseUnwrappingLevel", 4))
    max_depth = (level + 1) * 1500
    normalized = np.clip(depth_mm.astype(np.float32), 0, max_depth)
    scaled = (normalized / max_depth * 255).astype(np.uint8)
    return cv2.applyColorMap(scaled, cv2.COLORMAP_TURBO)
