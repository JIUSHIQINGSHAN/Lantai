# Dockerfile — Remembrance-System
# python:3.11-slim 多阶段构建

# ---- builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# 编译依赖 + 源码（必须 COPY remembrance/ 否则 wheel 为空）
COPY pyproject.toml ./
COPY remembrance/ ./remembrance/
RUN pip install --upgrade pip build && python -m build --wheel --outdir /wheels

# ---- runtime ----
FROM python:3.11-slim AS runtime

WORKDIR /app

# 安装运行时依赖（wheel 内含 remembrance 包，无需 COPY 源码）
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# 复制入口与脚本
COPY api_server.py ./
COPY scripts/ ./scripts/

# 数据卷
VOLUME ["/data"]

# 环境变量
ENV REMEMBRANCE_HOME=/data
ENV PORT=8767
# 容器需对外可达故绑 0.0.0.0——必须同时注入 API_KEY，否则 assert_secure_binding 拒绝启动
ENV HOST=0.0.0.0

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8767/health')" || exit 1

EXPOSE 8767

CMD ["python", "api_server.py"]
