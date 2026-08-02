# Dockerfile — Remembrance-System
# python:3.11-slim 多阶段构建

# ---- builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

# 编译依赖
COPY pyproject.toml ./
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /wheels

# ---- runtime ----
FROM python:3.11-slim AS runtime

WORKDIR /app

# 安装运行时依赖
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# 复制代码
COPY remembrance/ ./remembrance/
COPY api_server.py ./
COPY scripts/ ./scripts/

# 数据卷
VOLUME ["/data"]

# 环境变量
ENV REMEMBRANCE_HOME=/data
ENV PORT=8767

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8767/health')" || exit 1

EXPOSE 8767

CMD ["python", "api_server.py"]
