# OAK hardware tests — inside Docker (recommended)

All tools run in the **`nilo-node:hardware`** image. No host Python venv.

## Prerequisites (host only)

```bash
# Docker (deploy script installs this)
sudo ./scripts/install.sh

# Allow GUI from container (once per desktop session)
xhost +local:docker

# USB permissions for OAK
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | sudo tee /etc/udev/rules.d/99-oak-usb.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## Run tests in container

From repo root:

```bash
chmod +x scripts/oak/run-in-docker.sh

# Build image (first time)
./scripts/oak/run-in-docker.sh build

# 1) ToF viewer — connect, depth colormap, measure mm
./scripts/oak/run-in-docker.sh tof

# 2) Pose viewer — MediaPipe or YOLO
./scripts/oak/run-in-docker.sh pose

# 3) Model toolchain (inside container)
./scripts/oak/run-in-docker.sh model prepare --backend mediapipe
./scripts/oak/run-in-docker.sh model prepare --backend yolo --weights ./yolov8n-pose.pt
```

Models persist in `scripts/oak/models/` (bind-mounted).

## NILO-Node production on mini PC

Production compose uses the **same hardware image** (depthai + ffmpeg + camera deps):

```bash
sudo ./scripts/install.sh
# or explicitly:
docker compose -f docker-compose.prod.yml up -d --build
```

Image target: `hardware` (see `docker/Dockerfile`).

Alternative stack file:

```bash
docker compose -f docker-compose.hardware.yml up -d nilo-node-hw
```

## How it works

| Piece | Detail |
|-------|--------|
| Image | `docker/Dockerfile` target `hardware` — `[hardware]` pip extras + Tk + OpenGL |
| GUI | X11 socket `/tmp/.X11-unix` + `DISPLAY` from host |
| USB | `/dev/bus/usb` bind-mount, `privileged: true` |
| Scripts | `/app/scripts/oak/` inside container |

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `cannot open display` | Run `xhost +local:docker`, check `echo $DISPLAY` |
| Wayland session | Try `export DISPLAY=:0` or run from X11 session |
| No OAK device | udev rule, replug USB, `lsusb \| grep 03e7` |
| Tk window empty | Wait a few seconds for ToF warmup |
| Build slow | Normal first time (mediapipe, opencv, depthai) |

## Host venv (optional fallback)

Only if Docker GUI is impossible:

```bash
pip install -e ".[hardware]"
python scripts/oak/tof_viewer.py
```

## References

- [Luxonis ToF depth example](https://docs.luxonis.com/software/depthai/examples/tof_depth)
- [OAK-D-SR-PoE](https://shop.luxonis.com/products/oak-d-sr-poe)
