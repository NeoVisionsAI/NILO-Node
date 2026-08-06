#!/usr/bin/env python3
"""Tkinter viewer for OAK-D-SR ToF — connect, preview RGB/depth, measure distance."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

# Allow running from repo root without install
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from oak.pipeline_tof import OakSrSession, list_devices  # noqa: E402

logger = logging.getLogger(__name__)

PREVIEW_SIZE = (640, 480)


def _resize_bgr(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    if frame.shape[1] == size[0] and frame.shape[0] == size[1]:
        return frame
    return cv2.resize(frame, size)


def _bgr_to_photo(frame: np.ndarray, size: tuple[int, int]) -> ImageTk.PhotoImage:
    rgb = cv2.cvtColor(_resize_bgr(frame, size), cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    return ImageTk.PhotoImage(image=image)


class TofViewerApp:
    def __init__(
        self,
        device_id: str | None = None,
        device_ip: str | None = None,
        prefer: str | None = None,
    ) -> None:
        self._session = OakSrSession(
            device_id=device_id,
            device_ip=device_ip,
            prefer=prefer,
        )
        self._running = False
        self._worker: threading.Thread | None = None
        self._click_xy: tuple[int, int] | None = None

        self.root = tk.Tk()
        self.root.title("NILO-Node — OAK ToF test")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=tk.X)

        self.btn_connect = ttk.Button(toolbar, text="Conectar cámara", command=self._toggle_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=4)

        self.btn_measure = ttk.Button(
            toolbar,
            text="Medir profundidad (centro)",
            command=self._measure_center,
            state=tk.DISABLED,
        )
        self.btn_measure.pack(side=tk.LEFT, padx=4)

        self.lbl_status = ttk.Label(toolbar, text="Desconectado")
        self.lbl_status.pack(side=tk.LEFT, padx=12)

        panes = ttk.Frame(self.root, padding=8)
        panes.pack(fill=tk.BOTH, expand=True)

        rgb_frame = ttk.LabelFrame(panes, text="RGB", padding=4)
        rgb_frame.grid(row=0, column=0, padx=4, pady=4)
        self.canvas_rgb = tk.Label(rgb_frame)
        self.canvas_rgb.pack()
        self.canvas_rgb.bind("<Button-1>", self._on_rgb_click)

        depth_frame = ttk.LabelFrame(panes, text="ToF depth (colormap)", padding=4)
        depth_frame.grid(row=0, column=1, padx=4, pady=4)
        self.canvas_depth = tk.Label(depth_frame)
        self.canvas_depth.pack()
        self.canvas_depth.bind("<Button-1>", self._on_depth_click)

        info = ttk.LabelFrame(self.root, text="Medición", padding=8)
        info.pack(fill=tk.X, padx=8, pady=8)
        self.lbl_measure = ttk.Label(
            info,
            text="Conecta la cámara y pulsa «Medir profundidad» o haz clic en la imagen.",
            font=("Sans", 11),
        )
        self.lbl_measure.pack(anchor=tk.W)

        self._photo_rgb: ImageTk.PhotoImage | None = None
        self._photo_depth: ImageTk.PhotoImage | None = None

        self._show_devices_hint()

    def _show_devices_hint(self) -> None:
        try:
            devices = list_devices()
            if devices:
                names = ", ".join(d["mxid"][:8] + "…" for d in devices)
                self.lbl_status.config(text=f"Detectadas: {names}")
        except Exception as exc:
            self.lbl_status.config(text=f"DepthAI: {exc}")

    def _toggle_connect(self) -> None:
        if self._running:
            self._stop()
            return
        try:
            self._session.connect()
        except Exception as exc:
            messagebox.showerror("Conexión", str(exc))
            return

        self._running = True
        self.btn_connect.config(text="Desconectar")
        self.btn_measure.config(state=tk.NORMAL)
        self.lbl_status.config(text=f"Conectado: {self._session.connection_meta.get('connection', '?')} "
                                   f"{self._session.device_id or ''}")
        self._worker = threading.Thread(target=self._capture_loop, daemon=True)
        self._worker.start()

    def _stop(self) -> None:
        self._running = False
        self._session.disconnect()
        self.btn_connect.config(text="Conectar cámara")
        self.btn_measure.config(state=tk.DISABLED)
        self.lbl_status.config(text="Desconectado")

    def _capture_loop(self) -> None:
        while self._running:
            try:
                frames = self._session.poll()
                self.root.after(0, self._update_frames, frames)
            except Exception as exc:
                logger.exception("Capture error")
                self.root.after(0, lambda: messagebox.showerror("Captura", str(exc)))
                self.root.after(0, self._stop)
                break
            threading.Event().wait(0.03)

    def _update_frames(self, frames) -> None:
        if frames.rgb is not None:
            self._photo_rgb = _bgr_to_photo(frames.rgb, PREVIEW_SIZE)
            self.canvas_rgb.config(image=self._photo_rgb)
        if frames.depth_colormap is not None:
            self._photo_depth = _bgr_to_photo(frames.depth_colormap, PREVIEW_SIZE)
            self.canvas_depth.config(image=self._photo_depth)
        if self._click_xy is not None:
            x, y = self._click_xy
            self._show_depth_at(x, y, source="click")
            self._click_xy = None

    def _measure_center(self) -> None:
        depth = self._session.depth_center_mm()
        if depth is None:
            self.lbl_measure.config(text="Sin lectura ToF en el centro (¿sensor listo?)")
            return
        self.lbl_measure.config(text=f"Profundidad en el centro: {depth} mm ({depth / 10:.1f} cm)")

    def _on_depth_click(self, event: tk.Event) -> None:
        if not self._running or self._session.latest_depth is None:
            return
        depth_h, depth_w = self._session.latest_depth.shape[:2]
        x = int(event.x / PREVIEW_SIZE[0] * depth_w)
        y = int(event.y / PREVIEW_SIZE[1] * depth_h)
        self._show_depth_at(x, y, source="clic depth")

    def _on_rgb_click(self, event: tk.Event) -> None:
        if not self._running or self._session.latest_depth is None:
            return
        # Map RGB click to depth coords (same preview size; depth may differ — use proportional)
        depth_h, depth_w = self._session.latest_depth.shape[:2]
        x = int(event.x / PREVIEW_SIZE[0] * depth_w)
        y = int(event.y / PREVIEW_SIZE[1] * depth_h)
        self._show_depth_at(x, y, source="clic RGB")

    def _show_depth_at(self, x: int, y: int, *, source: str) -> None:
        depth = self._session.depth_at(x, y)
        if depth is None:
            self.lbl_measure.config(
                text=f"({source}) pixel ({x},{y}): sin señal / fuera de rango"
            )
        else:
            self.lbl_measure.config(
                text=f"({source}) pixel ({x},{y}): {depth} mm ({depth / 10:.1f} cm)"
            )

    def _on_close(self) -> None:
        self._stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="OAK-D-SR ToF viewer (Tkinter)")
    parser.add_argument("--device-id", default=None, help="MXID (optional)")
    parser.add_argument(
        "--device-ip",
        default=None,
        help="PoE IP (e.g. 169.254.1.222). Or env OAK_DEVICE_IP",
    )
    parser.add_argument(
        "--prefer",
        choices=["auto", "usb", "poe"],
        default=None,
        help="Connection preference (default: auto or OAK_CONNECTION env)",
    )
    args = parser.parse_args()
    TofViewerApp(
        device_id=args.device_id,
        device_ip=args.device_ip,
        prefer=args.prefer,
    ).run()


if __name__ == "__main__":
    main()
