# syntax=docker/dockerfile:1
# Locked, multi-stage runtime. Build with docker buildx build --load.
ARG PYTHON_IMAGE=python:3.14.10-slim-bookworm
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
RUN python -m pip install --no-cache-dir uv==0.13.8
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv UV_CACHE_DIR=/cache/uv
COPY pyproject.toml uv.lock ./
# Exact runtime versions from uv.lock; no resolver drift during production builds.
RUN --mount=type=cache,target=/cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY lantai/ ./lantai/
RUN --mount=type=cache,target=/cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    LANTAI_HOME=/data PORT=8767 HOST=0.0.0.0 \
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
COPY api_server.py ./
COPY scripts/ ./scripts/
RUN useradd -m -u 1001 appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8767/health')" || exit 1
EXPOSE 8767
CMD ["lantai-server"]
