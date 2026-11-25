# syntax=docker/dockerfile:1
# Build from the repository root: docker compose builds it with context "." (see
# docker-compose.yml), because the face worker needs the shared libs next to it.

# --- build stage: install the face worker and its libs into a virtual environment ---
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY services ./services
# --frozen: install exactly what uv.lock says; --no-editable: copy the workspace
# packages into the environment, so the runtime image does not need /src.
RUN uv sync --frozen --no-dev --no-editable --package interpol-face-worker

# --- runtime stage: only the environment, run as an unprivileged user -----------------
FROM python:3.12-slim
RUN useradd --system --uid 10001 --no-create-home face-worker
COPY --from=build /app/.venv /app/.venv
# The face models of ADR 0012 from the OpenCV model zoo. Docker checks each SHA-256 and
# stops the build if a file differs, so a changed or tampered model is never used.
# --chmod: files fetched by ADD from a URL are readable by root only by default, and the
# service runs as an unprivileged user. The folder is made first (mode 755): a folder
# that ADD creates itself would get the files' mode, which has no "x", so it could not
# be entered.
RUN mkdir -p /app/models
ADD --chmod=644 --checksum=sha256:8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 \
    https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
    /app/models/face_detection_yunet_2023mar.onnx
ADD --chmod=644 --checksum=sha256:0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 \
    https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx \
    /app/models/face_recognition_sface_2021dec.onnx
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
USER face-worker
CMD ["interpol-face-worker"]
