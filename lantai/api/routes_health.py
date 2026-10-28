from pathlib import Path

from fastapi import APIRouter, Depends
from sqlmodel import select

from lantai.core import scheduler
from lantai.core.auth import Principal, get_current_user
from lantai.core.settings import settings
from lantai.ingestion.coalesce import get_coalesce_buffer
from lantai.models.tables import MemoryItem
from lantai.storage import db

# 公共路由（不需要鉴权）
router = APIRouter()
# 受保护路由（需要 API Key）
protected_router = APIRouter()

# git_sha 短缓存（票 J）：/health 是 Docker HEALTHCHECK 高频路径，
# 不得每次请求起子进程。60s 内复用；失败结果同样缓存，避免坏环境
# 下每次探活都 spawn 一个注定失败的进程。
_GIT_SHA_TTL = 60.0
_git_sha_cache: tuple[float, str | None] | None = None


def _find_git() -> str | None:
    """PATH 优先，回退常见安装路径（git 装了但不在 PATH 的场景）。"""
    import shutil

    exe = shutil.which("git")
    if exe:
        return exe
    for cand in (
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        "/usr/bin/git",
    ):
        if Path(cand).exists():
            return cand
    return None


def _current_git_sha() -> str | None:
    """当前 commit sha；非 git 环境 / git 不可用时返回 None，绝不抛错。"""
    global _git_sha_cache
    import subprocess
    import time as _time

    now = _time.monotonic()
    if _git_sha_cache is not None and now - _git_sha_cache[0] < _GIT_SHA_TTL:
        return _git_sha_cache[1]
    sha: str | None = None
    exe = _find_git()
    if exe:
        try:
            r = subprocess.run(
                [exe, "rev-parse", "HEAD"],
                cwd=Path(__file__).resolve().parents[2],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                sha = r.stdout.strip() or None
        except Exception:
            sha = None
    _git_sha_cache = (now, sha)
    return sha


@router.get("/health")
def health():
    """简单存活探针——Docker HEALTHCHECK 用，公开；不暴露内部状态。

    git_sha（票 J）：非敏感的构建定位信息，运维对表「跑的是哪个 commit」；
    取不到时为 null（Docker 镜像内可能无 git），不影响 ok 判定。
    """
    return {"ok": True, "git_sha": _current_git_sha()}


@router.get("/api/memory/health")
def memory_health():
    """旧兼容端点"""
    return {"ok": True, "service": "lantai"}


@protected_router.get("/health/deep")
def health_deep():
    """深度健康检查——检查 SQLite/ChromaDB/LLM 端点"""
    checks = {}

    # SQLite 可写
    try:
        with db.get_session() as s:
            s.exec(select(MemoryItem).limit(1))
        checks["sqlite"] = "ok"
    except Exception as e:
        checks["sqlite"] = f"fail: {e}"

    # ChromaDB
    try:
        from lantai.storage.vector_store import get_vector_store

        get_vector_store()
        checks["chromadb"] = "ok"
    except Exception as e:
        checks["chromadb"] = f"fail: {e}"

    # FTS5 词汇召回通道（票 .scratch/fts-availability/01）
    # 只查 sqlite 表存在与 chromadb 可达都探不出 FTS 坏——而 FTS 坏的表现是
    # 「检索结果变少」，用户会以为记忆没存进去。故必须单独核验
    # memory_fts 的真实定义（IF NOT EXISTS 对错表是静默 no-op，只能读 sqlite_master）。
    try:
        with db.get_session() as s:
            row = (
                s.connection()
                .exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'memory_fts'")
                .fetchone()
            )
        ddl = (row[0] or "").lower() if row else ""
        if row and "fts5" in ddl and "trigram" in ddl:
            checks["fts"] = "ok"
        else:
            checks["fts"] = (
                "fail: memory_fts missing or not an FTS5/trigram table (lexical recall degraded)"
            )
    except Exception as e:
        checks["fts"] = f"fail: {e}"

    # LLM 端点（未配置 key 时跳过，避免每次探活触发外部调用）
    if not settings.OPENAI_API_KEY:
        checks["llm"] = "skipped (no key)"
    else:
        try:
            from lantai.llm.client import _client

            _client.models.list()
            checks["llm"] = "ok"
        except Exception as e:
            checks["llm"] = f"fail: {e}"

    # 迁移链是否跑到头（票 .scratch/migration-chain/01）
    # 原来是 25 个线性 if 块，任何一步抛异常后续全部静默跳过、服务照常启动，
    # 坏了半个月没人知道，直到某个功能 500 才顺藤摸到这里。同 fts 的形状：
    # sqlite 能连、chromadb 能读都探不出迁移没跑完——缺表缺列只在用到的
    # 那一刻炸。故单独核验 version 是否到达目标版本，并把失败跳报出来。
    try:
        with db.get_session() as s:
            current = s.connection().exec_driver_sql("PRAGMA user_version").scalar()
        failures = db.get_migration_failures()
        if failures:
            detail = "; ".join(f"v{f['version']}: {f['error']}" for f in failures)
            checks["migrations"] = f"fail: {len(failures)} block(s) failed ({detail})"
        elif current < db.TARGET_SCHEMA_VERSION:
            checks["migrations"] = (
                f"fail: schema at v{current}, target v{db.TARGET_SCHEMA_VERSION} "
                "(migration chain did not complete)"
            )
        else:
            checks["migrations"] = "ok"
    except Exception as e:
        checks["migrations"] = f"fail: {e}"

    all_ok = all(v in ("ok", "skipped (no key)") for v in checks.values())
    return {"ok": all_ok, "checks": checks}


@protected_router.get("/stats")
def stats():
    """记忆统计——SQL 聚合，避免全表加载到内存"""
    from sqlmodel import func

    with db.get_session() as s:
        total = s.exec(select(func.count()).select_from(MemoryItem)).one()
        lane_rows = s.exec(select(MemoryItem.lane, func.count()).group_by(MemoryItem.lane)).all()
        status_rows = s.exec(
            select(MemoryItem.status, func.count()).group_by(MemoryItem.status)
        ).all()
        tier_rows = s.exec(select(MemoryItem.tier, func.count()).group_by(MemoryItem.tier)).all()
        domain_rows = s.exec(
            select(MemoryItem.domain, func.count()).group_by(MemoryItem.domain)
        ).all()
        decay_rows = s.exec(
            select(MemoryItem.decay_class, func.count()).group_by(MemoryItem.decay_class)
        ).all()
        from lantai.evolution.reflector import health_scan

        h_scan = health_scan(s)
        health_snapshot = h_scan.get("snapshot", {})

    buffer = get_coalesce_buffer().water_level()
    return {
        "total_memories": total,
        "by_lane": {k: v for k, v in lane_rows},
        "by_status": {k: v for k, v in status_rows},
        "by_tier": {k: v for k, v in tier_rows},
        "by_domain": {k: v for k, v in domain_rows},
        "by_decay_class": {k: v for k, v in decay_rows},
        "coalesce_buffer": buffer,
        "workers": scheduler.WORKER_LAST_RUN,
        "health_snapshot": health_snapshot,
    }


@protected_router.get("/usage")
def usage(ctx: Principal = Depends(get_current_user)):
    """最近 7 天每日新增记忆数——单条 GROUP BY，不整表加载；缺日补零。"""
    from lantai.ops.usage import collect_usage

    # 归属（票 .scratch/mcp-identity-gaps/01b）：service 层补了 principal
    # 形参（MCP mem_usage 收窄），HTTP 侧同源受益——A 不能看见 B 每天
    # 写了多少记忆（行为画像素材）
    return collect_usage(days=7, principal=ctx)
