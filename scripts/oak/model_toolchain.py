#!/usr/bin/env python3
"""
Prepare pose backends for OAK hardware tests.

MediaPipe: bundled with pip package (host CPU).
YOLO pose:  .pt → ONNX → OpenVINO IR (host or Myriad blob via blobconverter).

Usage:
  python scripts/oak/model_toolchain.py prepare --backend mediapipe
  python scripts/oak/model_toolchain.py prepare --backend yolo --weights yolov8n-pose.pt
  python scripts/oak/model_toolchain.py export --weights yolov8n-pose.pt --name yolo-pose-n
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODELS_DIR = Path(__file__).resolve().parent / "models"


@dataclass
class ModelManifest:
    backend: str
    name: str
    weights_pt: str | None = None
    onnx: str | None = None
    openvino_xml: str | None = None
    openvino_bin: str | None = None
    blob: str | None = None
    run_on: str = "host"  # host | device
    notes: str = ""


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_mediapipe(output_dir: Path, name: str = "mediapipe") -> ModelManifest:
    try:
        import mediapipe as mp  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Install mediapipe: pip install mediapipe") from exc

    manifest = ModelManifest(
        backend="mediapipe",
        name=name,
        run_on="host",
        notes="Landmarks on host CPU from RGB frames (NILO-Node default).",
    )
    _write_manifest(output_dir / name, manifest)
    logger.info("MediaPipe ready (no export needed)")
    return manifest


def export_yolo_to_onnx(weights: Path, output_dir: Path, name: str, imgsz: int = 640) -> Path:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("Install ultralytics: pip install ultralytics") from exc

    _ensure_dir(output_dir)
    model = YOLO(str(weights))
    export_path = model.export(format="onnx", imgsz=imgsz, simplify=True)
    export_path = Path(export_path)
    dest = output_dir / f"{name}.onnx"
    if export_path.resolve() != dest.resolve():
        shutil.copy2(export_path, dest)
    logger.info("ONNX export: %s", dest)
    return dest


def export_onnx_to_openvino(onnx_path: Path, output_dir: Path, name: str) -> tuple[Path, Path]:
    try:
        from openvino.tools.mo import convert_model
        from openvino import save_model
    except ImportError:
        # Fallback: CLI mo
        xml_path = output_dir / f"{name}.xml"
        bin_path = output_dir / f"{name}.bin"
        import subprocess

        cmd = [
            "mo",
            "--input_model",
            str(onnx_path),
            "--output_dir",
            str(output_dir),
            "--model_name",
            name,
        ]
        logger.info("Running: %s", " ".join(cmd))
        subprocess.run(cmd, check=True)
        return xml_path, bin_path

    ov_model = convert_model(str(onnx_path))
    xml_path = output_dir / f"{name}.xml"
    save_model(ov_model, str(xml_path))
    bin_path = output_dir / f"{name}.bin"
    if not bin_path.exists():
        bin_path = output_dir / f"{name}.bin"  # save_model may use .bin sibling
    logger.info("OpenVINO IR: %s + .bin", xml_path)
    return xml_path, bin_path


def export_openvino_to_blob(
    xml_path: Path,
    output_dir: Path,
    name: str,
    *,
    shaves: int = 6,
) -> Path | None:
    """Optional: Myriad X blob via Luxonis blobconverter (needs network)."""
    try:
        import blobconverter
    except ImportError:
        logger.warning(
            "blobconverter not installed — skip device blob. "
            "pip install blobconverter  (for on-camera inference)"
        )
        return None

    blob_path = output_dir / f"{name}.blob"
    blob_file = blobconverter.from_openvino(
        xml=str(xml_path),
        bin=str(xml_path.with_suffix(".bin")),
        shaves=shaves,
    )
    shutil.copy2(blob_file, blob_path)
    logger.info("DepthAI blob: %s", blob_path)
    return blob_path


def prepare_yolo(
    weights: Path,
    output_dir: Path,
    name: str,
    *,
    openvino: bool = True,
    blob: bool = False,
    imgsz: int = 640,
) -> ModelManifest:
    if not weights.exists():
        raise FileNotFoundError(weights)

    model_dir = _ensure_dir(output_dir / name)
    pt_copy = model_dir / weights.name
    if weights.resolve() != pt_copy.resolve():
        shutil.copy2(weights, pt_copy)

    onnx_path = export_yolo_to_onnx(pt_copy, model_dir, name, imgsz=imgsz)
    manifest = ModelManifest(
        backend="yolo",
        name=name,
        weights_pt=str(pt_copy),
        onnx=str(onnx_path),
        run_on="host",
        notes="Host inference via ultralytics. OpenVINO paths for device export.",
    )

    if openvino:
        try:
            xml_path, _ = export_onnx_to_openvino(onnx_path, model_dir, name)
            manifest.openvino_xml = str(xml_path)
            manifest.openvino_bin = str(xml_path.with_suffix(".bin"))
            manifest.notes += " OpenVINO IR generated."
        except Exception as exc:
            logger.warning("OpenVINO export failed (%s) — ONNX still usable on host", exc)

    if blob and manifest.openvino_xml:
        blob_path = export_openvino_to_blob(Path(manifest.openvino_xml), model_dir, name)
        if blob_path is not None:
            manifest.blob = str(blob_path)
            manifest.run_on = "device"
            manifest.notes += " Myriad blob for DepthAI NN node."

    _write_manifest(model_dir, manifest)
    return manifest


def _write_manifest(model_dir: Path, manifest: ModelManifest) -> None:
    path = model_dir / "manifest.json"
    path.write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")
    logger.info("Manifest: %s", path)


def load_manifest(model_dir: Path) -> ModelManifest | None:
    path = model_dir / "manifest.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return ModelManifest(**data)


def cmd_prepare(args: argparse.Namespace) -> None:
    out = Path(args.output_dir)
    if args.backend == "mediapipe":
        prepare_mediapipe(out, name=args.name)
    elif args.backend == "yolo":
        if not args.weights:
            raise SystemExit("--weights required for yolo (e.g. yolov8n-pose.pt)")
        prepare_yolo(
            Path(args.weights),
            out,
            args.name,
            openvino=not args.skip_openvino,
            blob=args.blob,
            imgsz=args.imgsz,
        )
    else:
        raise SystemExit(f"Unknown backend: {args.backend}")


def cmd_export(args: argparse.Namespace) -> None:
    prepare_yolo(
        Path(args.weights),
        Path(args.output_dir),
        args.name,
        openvino=not args.skip_openvino,
        blob=args.blob,
        imgsz=args.imgsz,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="NILO-Node OAK pose model toolchain")
    parser.add_argument("--output-dir", default=str(DEFAULT_MODELS_DIR))

    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="Prepare backend (mediapipe or yolo pipeline)")
    p_prepare.add_argument("--backend", choices=["mediapipe", "yolo"], required=True)
    p_prepare.add_argument("--weights", help="YOLO .pt weights path")
    p_prepare.add_argument("--name", default="default")
    p_prepare.add_argument("--imgsz", type=int, default=640)
    p_prepare.add_argument("--skip-openvino", action="store_true")
    p_prepare.add_argument("--blob", action="store_true", help="Also build Myriad blob (blobconverter)")
    p_prepare.set_defaults(func=cmd_prepare)

    p_export = sub.add_parser("export", help="Export YOLO .pt → ONNX → OpenVINO")
    p_export.add_argument("--weights", required=True)
    p_export.add_argument("--name", default="yolo-pose")
    p_export.add_argument("--imgsz", type=int, default=640)
    p_export.add_argument("--skip-openvino", action="store_true")
    p_export.add_argument("--blob", action="store_true")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
