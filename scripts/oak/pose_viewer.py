#!/usr/bin/env python3
"""Tkinter pose viewer — MediaPipe or YOLO on host CPU from OAK RGB stream."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from oak.model_toolchain import DEFAULT_MODELS_DIR, load_manifest, prepare_mediapipe, prepare_yolo
from oak.pipeline_tof import OakSrSession, list_devices

logger = logging.getLogger(__name__)
PREVIEW_SIZE = (640, 480)


class MediapipeRunner:
    def __init__(self) -> None:
        import mediapipe as mp

        self._mp_drawing = mp.solutions.drawing_utils
        self._mp_pose = mp.solutions.pose
        self._pose = self._mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._pose.process(rgb)
        out = frame_bgr.copy()
        if result.pose_landmarks:
            self._mp_drawing.draw_landmarks(
                out,
                result.pose_landmarks,
                self._mp_pose.POSE_CONNECTIONS,
            )
        return out

    def close(self) -> None:
        self._pose.close()


class YoloHostRunner:
    def __init__(self, weights: Path) -> None:
        from ultralytics import YOLO

        self._model = YOLO(str(weights))

    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        results = self._model.predict(frame_bgr, verbose=False, conf=0.5)
        out = frame_bgr.copy()
        if results:
            out = results[0].plot()
        return out


class PoseViewerApp:
    def __init__(self, device_id: str | None = None) -> None:
        self._session = OakSrSession(device_id=device_id, fps=30)
        self._runner: MediapipeRunner | YoloHostRunner | None = None
        self._backend = tk.StringVar(value="mediapipe")
        self._model_dir = DEFAULT_MODELS_DIR
        self._running = False
        self._worker: threading.Thread | None = None

        self.root = tk.Tk()
        self.root.title("NILO-Node — OAK pose test")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Backend:").pack(side=tk.LEFT)
        ttk.Combobox(
            top,
            textvariable=self._backend,
            values=["mediapipe", "yolo"],
            state="readonly",
            width=12,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(top, text="Preparar modelo", command=self._prepare_model).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Importar .pt…", command=self._import_pt).pack(side=tk.LEFT, padx=4)
        self.btn_connect = ttk.Button(top, text="Conectar cámara", command=self._toggle_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=8)

        self.lbl_status = ttk.Label(top, text="Modelo no cargado")
        self.lbl_status.pack(side=tk.LEFT, padx=8)

        self.canvas = tk.Label(self.root)
        self.canvas.pack(padx=8, pady=8)

        info = ttk.LabelFrame(self.root, text="Pipeline de modelos", padding=8)
        info.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(
            info,
            text=(
                "YOLO: .pt → ONNX → OpenVINO (mo) → opcional .blob (blobconverter).\n"
                "MediaPipe: pip install mediapipe — inferencia en CPU host desde RGB.\n"
                "NILO-Node producción usa el mismo backend configurado en camera.pose_backend."
            ),
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        self._photo: ImageTk.PhotoImage | None = None
        self._try_load_default_model()

    def _try_load_default_model(self) -> None:
        backend = self._backend.get()
        manifest = load_manifest(self._model_dir / backend)
        if manifest is None and backend == "mediapipe":
            try:
                prepare_mediapipe(self._model_dir)
                manifest = load_manifest(self._model_dir / "mediapipe")
            except Exception:
                pass
        if manifest:
            self._load_runner_from_manifest(manifest)

    def _prepare_model(self) -> None:
        backend = self._backend.get()
        try:
            if backend == "mediapipe":
                manifest = prepare_mediapipe(self._model_dir, name="mediapipe")
            else:
                weights = filedialog.askopenfilename(
                    title="Seleccionar YOLO pose .pt",
                    filetypes=[("PyTorch", "*.pt"), ("All", "*.*")],
                )
                if not weights:
                    return
                self.lbl_status.config(text="Exportando .pt → ONNX → OpenVINO…")
                self.root.update()
                manifest = prepare_yolo(
                    Path(weights),
                    self._model_dir,
                    name="yolo-pose",
                    openvino=True,
                    blob=False,
                )
            self._load_runner_from_manifest(manifest)
            messagebox.showinfo("Modelo", f"Listo: {manifest.backend} ({manifest.name})")
        except Exception as exc:
            messagebox.showerror("Preparar modelo", str(exc))

    def _import_pt(self) -> None:
        self._backend.set("yolo")
        self._prepare_model()

    def _load_runner_from_manifest(self, manifest) -> None:
        if self._runner and hasattr(self._runner, "close"):
            self._runner.close()
        self._runner = None

        if manifest.backend == "mediapipe":
            self._runner = MediapipeRunner()
            self.lbl_status.config(text="MediaPipe cargado (host CPU)")
        elif manifest.backend == "yolo":
            weights = manifest.weights_pt
            if not weights or not Path(weights).exists():
                raise FileNotFoundError(f"Missing weights: {weights}")
            self._runner = YoloHostRunner(Path(weights))
            extra = []
            if manifest.onnx:
                extra.append("ONNX")
            if manifest.openvino_xml:
                extra.append("OpenVINO")
            if manifest.blob:
                extra.append("blob")
            self.lbl_status.config(text=f"YOLO cargado ({', '.join(extra) or 'host'})")
        else:
            raise ValueError(manifest.backend)

    def _toggle_connect(self) -> None:
        if self._running:
            self._stop()
            return
        if self._runner is None:
            messagebox.showwarning("Modelo", "Prepara o carga un modelo primero")
            return
        try:
            devices = list_devices()
            if not devices:
                raise RuntimeError("No OAK device on USB")
            self._session.connect()
        except Exception as exc:
            messagebox.showerror("Conexión", str(exc))
            return

        self._running = True
        self.btn_connect.config(text="Desconectar")
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def _loop(self) -> None:
        while self._running:
            try:
                frames = self._session.poll()
                if frames.rgb is not None and self._runner is not None:
                    overlay = self._runner.process(frames.rgb)
                    self.root.after(0, self._show_frame, overlay)
            except Exception as exc:
                self.root.after(0, lambda: messagebox.showerror("Captura", str(exc)))
                self.root.after(0, self._stop)
                break
            threading.Event().wait(0.03)

    def _show_frame(self, frame_bgr: np.ndarray) -> None:
        rgb = cv2.cvtColor(
            cv2.resize(frame_bgr, PREVIEW_SIZE),
            cv2.COLOR_BGR2RGB,
        )
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.config(image=self._photo)

    def _stop(self) -> None:
        self._running = False
        self._session.disconnect()
        self.btn_connect.config(text="Conectar cámara")

    def _on_close(self) -> None:
        self._stop()
        if self._runner and hasattr(self._runner, "close"):
            self._runner.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="OAK pose viewer (MediaPipe / YOLO)")
    parser.add_argument("--device-id", default=None)
    args = parser.parse_args()
    PoseViewerApp(device_id=args.device_id).run()


if __name__ == "__main__":
    main()
