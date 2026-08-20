# syntax=docker/dockerfile:1
#
# One image for both the API and the detection worker.
#
# That is forced, not a preference: app/services/jobs.py runs worker/batch.py on
# a thread inside the API process, so the API needs torch, ultralytics, OpenCV
# and ffmpeg exactly as the worker does. Two images would differ only in their
# command while carrying identical ~2 GB dependency trees.
#
# The command is supplied by compose:
#   API     uvicorn app.main:app --host 0.0.0.0 --port 8020
#   worker  python -m worker.main
# run_worker.sh is deliberately not the entrypoint — it says so itself: its
# pkill is unsafe in a shared PID namespace, and restarts are the runtime's job.

ARG PYTHON_VERSION=3.12

# --- builder ---------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

# Pinned rather than :latest so a rebuild resolves the same dependencies.
COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# uv.lock pins `clip` straight from a git URL (git+https://github.com/ultralytics/CLIP),
# so the sync below clones it — on a machine with a cold uv cache the build dies
# with "Git executable not found" without this. Builder-only; runtime needs no git.
RUN apt-get update \
    && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Dependencies resolve from the lockfile alone, so this layer is reused on every
# source-only change. --no-install-project because the app is not installed as a
# package here; it is copied in and run from /app.
#
# torch comes from the CPU index on linux (pyproject [tool.uv.sources]), which
# is the difference between ~250 MB and ~2.5 GB of unusable CUDA wheels.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- runtime ---------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# ffmpeg is called as a subprocess for capture, replay and clip writing.
# libGL/libglib are needed by opencv-python — note this is the full build, not
# opencv-python-headless, because the worker's --show debug window uses
# cv2.imshow. Switching to headless would drop both libraries and ~100 MB.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ffmpeg \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Everything written at runtime hangs off this, and compose mounts a volume on
# it. It must not change afterwards — uploads are recorded in the database as
# absolute paths, so moving it strands every existing source.
ENV DATA_DIR=/data

# Model caches. Without HF_HOME the ~600 MB CLIP checkpoint lands in the
# container's writable layer and is re-downloaded on every restart. Ultralytics
# and matplotlib both want a writable home directory and fail noisily without one.
ENV HF_HOME=/opt/models/huggingface \
    YOLO_CONFIG_DIR=/opt/models/ultralytics \
    MPLCONFIGDIR=/tmp/matplotlib

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# Weights baked into the image rather than fetched on first use. --skip-clip
# keeps this layer at ~19 MB; drop the flag to bake the CLIP checkpoint too, at
# roughly 600 MB, if semantic search needs to work offline from first boot.
# Both cache directories must exist before the fetch: ultralytics probes
# YOLO_CONFIG_DIR for writability and silently falls back to /tmp when it is
# missing, which puts its settings file in a layer that does not persist.
RUN mkdir -p /opt/models/huggingface /opt/models/ultralytics /data \
    && python -m scripts.fetch_models --skip-clip

# Non-root. This is what turned the log-directory mkdir into a startup crash
# during development, which is the point: run as the user that will run in
# production, so those failures surface here rather than on a deployment.
# A high uid rather than --system: system uids are capped at 999, and running
# above that range keeps this compatible with hosts that refuse low uids.
RUN useradd --create-home --uid 10001 vision24 \
    && chown -R vision24:vision24 /data /opt/models
USER vision24

EXPOSE 8020

# Liveness only. /api/health is deliberately DB-free, so it answers 200 while
# Postgres is down — useful for "is the process up", wrong as a readiness gate.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8020/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]
