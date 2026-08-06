# syntax=docker/dockerfile:1

# --- Base: common OS deps ---------------------------------------------------
FROM python:3.11-slim-bookworm AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Metadata only — keeps pip dependency layers cached when src/ changes
COPY pyproject.toml README.md ./

# --- Runtime: NILO-Node orchestrator (slim) ---------------------------------
FROM base AS runtime

RUN mkdir -p src/nilo_node \
    && printf '%s\n' '__version__ = "0.0.0"' > src/nilo_node/__init__.py \
    && pip install --no-cache-dir .

COPY src ./src
RUN pip install --no-cache-dir --no-deps .

COPY config/nilo-node.example.yaml /etc/nilo-node/nilo-node.yaml
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV NILO_CONFIG_PATH=/etc/nilo-node/nilo-node.yaml
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8080/api/v1/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]

# --- Hardware: OAK ToF + pose test GUIs + camera extras --------------------
FROM base AS hardware

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3-tk \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Heavy deps (depthai, mediapipe, ultralytics/torch) — cached until pyproject.toml changes
RUN mkdir -p src/nilo_node \
    && printf '%s\n' '__version__ = "0.0.0"' > src/nilo_node/__init__.py \
    && pip install --no-cache-dir ".[hardware]"

# Application code only — fast rebuild, no re-download of NVIDIA/torch wheels
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

COPY config/nilo-node.example.yaml /etc/nilo-node/nilo-node.yaml
COPY docker/entrypoint.sh /entrypoint.sh
COPY scripts/oak /app/scripts/oak
RUN chmod +x /entrypoint.sh /app/scripts/oak/*.py 2>/dev/null || true \
    && mkdir -p /app/scripts/oak/models

ENV NILO_CONFIG_PATH=/etc/nilo-node/nilo-node.yaml
ENV PYTHONPATH=/app/scripts
ENV DISPLAY=:0

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8080/api/v1/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
