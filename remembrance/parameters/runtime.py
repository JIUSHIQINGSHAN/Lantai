"""
运行时参数层——DB 是事实源，settings 单例原位刷新。

关键约束（ADR-0001 门面铁律的延伸）：
- 保持 id(settings) 不变，只对六个白名单属性 setattr，旧 import 全绿。
- 启动时加载 DB head；跨进程用 revision 轮询（PARAM_OVERRIDE_REFRESH_SECONDS）。
- 损坏的 DB 快照不应用（校验失败即拒绝，保持上一有效配置）。
"""
from sqlmodel import select, func

from remembrance.core.logger import logger
from remembrance.core.settings import settings
from remembrance.models.tables import ParamOverride
from remembrance.parameters.registry import (
    default_snapshot,
    get_registry_version,
)
from remembrance.parameters.validation import (
    ParamValidationError,
    validate_snapshot,
)
from remembrance.storage import db

# 每个进程各自记住"已应用到的 revision"，跨进程不共享（DB 才是事实源）
_last_applied_revision: int = -1


def _head_override(session) -> ParamOverride | None:
    return session.exec(
        select(ParamOverride).order_by(ParamOverride.revision.desc())
    ).first()


def get_effective_param_snapshot(session) -> dict:
    """当前有效快照：head override 的 after_snapshot；无 override 时用静态默认。"""
    head = _head_override(session)
    if head is None:
        return default_snapshot()
    # 启动/刷新加载时校验——损坏则拒绝应用，保持上一有效配置
    try:
        return validate_snapshot(head.after_snapshot)
    except ParamValidationError as e:
        logger.error("DB head snapshot invalid, refusing to apply: %s", e)
        raise


def get_effective_state(session) -> dict:
    """返回 (snapshot, revision, hash, registry_version) 的完整状态。"""
    head = _head_override(session)
    if head is None:
        snap = default_snapshot()
        return {"snapshot": snap, "revision": 0,
                "snapshot_hash": _hash(snap),
                "registry_version": get_registry_version()}
    return {"snapshot": head.after_snapshot, "revision": head.revision,
            "snapshot_hash": head.after_snapshot_hash,
            "registry_version": head.registry_version}


def _hash(snapshot: dict) -> str:
    from remembrance.parameters.validation import snapshot_hash
    return snapshot_hash(snapshot)


def apply_snapshot_to_settings(snapshot: dict) -> bool:
    """
    将快照原位写入 settings 单例（白名单参数，先校验）。
    返回是否有实际变化。
    """
    from remembrance.parameters.registry import get_param_registry
    validated = validate_snapshot(snapshot, get_param_registry(),
                                  allow_partial=True)
    changed = False
    for name, value in validated.items():
        if getattr(settings, name) != value:
            setattr(settings, name, value)
            changed = True
    return changed


def refresh_runtime_params() -> dict:
    """
    周期刷新：读取 DB head revision，变化则原位应用。
    供 scheduler 每 PARAM_OVERRIDE_REFRESH_SECONDS 调用一次。
    """
    global _last_applied_revision
    with db.get_session() as s:
        head = _head_override(s)
        if head is None:
            revision, snapshot = 0, default_snapshot()
        else:
            revision, snapshot = head.revision, head.after_snapshot
    applied = False
    if revision != _last_applied_revision:
        try:
            applied = apply_snapshot_to_settings(snapshot)
            _last_applied_revision = revision
            logger.info("runtime params refreshed: revision=%d applied=%s",
                        revision, applied)
        except ParamValidationError as e:
            logger.error("runtime refresh rejected (keep previous config): %s", e)
            _last_applied_revision = revision  # 防刷屏，保持上一配置
    return {"revision": revision, "applied": applied}


def load_runtime_params_at_startup() -> None:
    """启动时强制加载 DB head（lifespan 调用）。"""
    global _last_applied_revision
    with db.get_session() as s:
        head = _head_override(s)
        if head is None:
            return
        snapshot = validate_snapshot(head.after_snapshot)
    applied = apply_snapshot_to_settings(snapshot)
    _last_applied_revision = head.revision
    if applied:
        logger.info("startup: loaded param override revision=%d", head.revision)
