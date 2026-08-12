"""冷启动导入 service（Ticket 07）：历史会话 JSONL 批量原文直存。

借鉴 TencentDB Agent Memory 冷启动导入（保留原始时间戳）：
逐行 JSONL → verbatim 直存（零 LLM，复用 Raw Drawer 语义），
内容 sha256 幂等去重，created_at/updated_at 保留原值。
parse_import_lines 为纯函数（测试直调不 mock）。
"""
import hashlib
import json
from datetime import datetime, timezone

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.enums import MemoryTier
from lantai.models.tables import MemoryItem
from lantai.storage import db
from lantai.storage.fts import sync_fts


def _parse_dt(value, field: str) -> datetime:
    """ISO8601 时间戳解析 → naive UTC（失败抛 ValueError，调用方记非法行）。

    与摄取链 normalize_timestamp 同语义：Z/±HH:MM 时区输入换算为 UTC 后去掉
    tzinfo，保证 SQLite 落库与 digest 等 naive UTC 区间比较一致（ADR-0018
    「时间线不再被压平」；票据 07 验收 2 含时区）。
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{field} 时间戳无法解析: {text!r}")
    else:
        raise ValueError(f"{field} 时间戳为空")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_import_lines(text: str) -> tuple[list[dict], list[dict]]:
    """逐行解析 JSONL → (valid_lines, invalid_records)。

    valid line 归一化为 {content, created_at, updated_at, lane, tags}；
    invalid record 为 {line, reason}。空行跳过，不记非法。
    """
    valid: list[dict] = []
    invalid: list[dict] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            invalid.append({"line": idx, "reason": f"JSON 解析失败: {e.msg}"})
            continue
        if not isinstance(obj, dict):
            invalid.append({"line": idx, "reason": "JSON 行必须是对象"})
            continue
        content = obj.get("content")
        if not isinstance(content, str) or not content.strip():
            invalid.append({"line": idx, "reason": "content 缺失或为空"})
            continue
        try:
            created_at = (
                _parse_dt(obj["created_at"], "created_at")
                if obj.get("created_at") is not None else None)
            updated_at = (
                _parse_dt(obj["updated_at"], "updated_at")
                if obj.get("updated_at") is not None else None)
        except ValueError as e:
            invalid.append({"line": idx, "reason": str(e)})
            continue
        lane = obj.get("lane")
        if lane is not None and (not isinstance(lane, str) or not lane.strip()):
            invalid.append({"line": idx, "reason": "lane 必须为非空字符串"})
            continue
        tags = obj.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            invalid.append({"line": idx, "reason": "tags 必须为字符串数组"})
            continue
        valid.append({
            "content": content.strip(),
            "created_at": created_at,
            "updated_at": updated_at,
            "lane": lane.strip() if lane else settings.RAW_MEMORY_DEFAULT_LANE,
            "tags": tags,
        })
    return valid, invalid


def _store_imported_memory(s, content, created_at, updated_at, lane, tags) -> str:
    """verbatim 直存单行：返回 'imported' | 'duplicate'。

    内容 sha256 幂等去重（与 add_raw_memory 同语义）；created_at/updated_at
    保留原始时间戳（updated_at 缺省取 created_at）。embedding/向量索引失败
    不阻断落库（FTS 仍可检索）。
    """
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = s.exec(
        select(MemoryItem)
        .where(MemoryItem.memory_type == "verbatim",
               MemoryItem.key == h,
               MemoryItem.status == "active")).first()
    if existing:
        return "duplicate"
    created = created_at or utcnow()
    updated = updated_at or created
    mem = MemoryItem(
        id=new_id("mem"),
        memory_type="verbatim",
        key=h,
        content=content,
        lane=lane,
        tier=MemoryTier.LONG_TERM,
        confidence=1.0,
        importance=0.5,
        tags=tags,
        decay_class="semantic",
        created_at=created,
        updated_at=updated,
    )
    s.add(mem)
    s.flush()
    try:
        from lantai.llm.client import embed
        from lantai.retrieval.hybrid import index_memory_item
        emb = embed([content])[0]
        index_memory_item(mem.id, emb, {"key": mem.key, "memory_type": mem.memory_type})
    except Exception:
        pass  # 向量索引失败不阻断落库（FTS 仍可检索）
    sync_fts(s, mem.id, content)
    s.commit()
    return "imported"


def import_memory_lines(lines: list[dict], agent_id: str | None = None) -> dict:
    """按文件顺序落库；单行异常记 errors 不中断（宁 miss 不脏写）。

    agent_id 非空时按 ACL 收窄：lane 不在绑定集的行记 errors 不导入（403 语义，
    宁 miss 不放行）；ACL 未启用时 agent_id 为 "no-acl" 哨兵 → lane_allowed 恒真。
    """
    report = {"imported": 0, "duplicates": 0, "errors": []}
    from lantai.core.acl import lane_allowed
    with db.get_session() as s:
        for line in lines:
            if agent_id is not None and not lane_allowed(agent_id, line["lane"]):
                report["errors"].append({
                    "content": line["content"][:60],
                    "reason": f"lane {line['lane']!r} 不在 agent {agent_id!r} 绑定集（ACL）",
                })
                continue
            try:
                result = _store_imported_memory(
                    s, line["content"], line["created_at"],
                    line["updated_at"], line["lane"], line["tags"])
            except Exception as e:  # noqa: BLE001 —— 单行失败只记报告
                report["errors"].append({
                    "content": line["content"][:60], "reason": str(e)})
                continue
            if result == "imported":
                report["imported"] += 1
            else:
                report["duplicates"] += 1
    return report


def run_jsonl_import(text: str, agent_id: str | None = None) -> dict:
    """解析 + 落库 + 汇总报告。非法行只报告不导入；agent_id 按 ACL 收窄。"""
    lines, invalid = parse_import_lines(text)
    report = import_memory_lines(lines, agent_id=agent_id)
    report["invalid"] = invalid
    report["ok"] = True
    return report
