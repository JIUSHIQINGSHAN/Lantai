"""mem: 会话指令 service（借鉴 TencentDB Agent Memory MemoryProxy mem-command）。

腾讯在代理层拦截 `mem:sync / mem:create-skill / mem:help`，让用户显式驱动记忆
维护；兰台以 MCP 命令式工具落地同构语义（mem_help / mem_sync / mem_create_skill）：

- mem_help：命令帮助（纯函数，零副作用）
- mem_sync：刷新注入资产 = scene 增量聚类补跑 + 今日 digest 快照重算
  （子步骤异常不阻断，宁 miss 不脏写）
- mem_create_skill：把会话主题显式沉淀为 Skill 资产（procedural 永不衰减、
  structure.steps 可被 shell_hook 以 ## Skill 块注入、进向量+FTS 可召回）

命令式 UX 价值：Agent 需要时主动触发维护动作，不依赖自动流程的时机。
"""
import hashlib
import time

from sqlmodel import select

from lantai.core.ids import new_id
from lantai.core.logger import logger
from lantai.core.settings import settings
from lantai.llm.client import embed  # 外部依赖：允许 mock
from lantai.models.enums import MemoryTier
from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import index_memory_item
from lantai.storage import db
from lantai.storage.fts import sync_fts

MEM_HELP_TEXT = """**支持的 mem: 命令（MCP 工具）：**

| 命令 | 说明 |
|------|------|
| `mem_help` | 显示本帮助 |
| `mem_sync` | 刷新注入资产：场景增量聚类补跑 + 今日 digest 重算 |
| `mem_create_skill` | 沉淀 Skill 资产：名称 + 描述 + 步骤（procedural 永不衰减） |

**示例（MCP tools/call）：**
- `mem_help`（无参数）
- `mem_sync`（无参数）
- `mem_create_skill` name="数据库迁移" description="迁移步骤与踩坑" steps=["备份库", "执行迁移", "验证"]"""


def mem_help() -> dict:
    """命令帮助（纯函数）：返回支持的命令表与示例。"""
    return {"ok": True, "command": "mem:help", "text": MEM_HELP_TEXT}


def mem_sync() -> dict:
    """刷新会话注入资产（借鉴腾讯 mem:sync）。

    scene 增量聚类补跑（SCENE_LAYER_ENABLED 时）+ 今日 digest 快照重算；
    子步骤异常只记日志不阻断（宁 miss 不脏写）。
    返回 {"ok", "scene", "digest", "took_ms"}。
    """
    started = time.monotonic()
    scene_res: dict = {}
    if settings.SCENE_LAYER_ENABLED:
        try:
            from lantai.services.scene_service import assign_unassigned
            scene_res = assign_unassigned()
        except Exception as exc:
            logger.warning("mem_sync scene 增量聚类失败（继续）: %s", exc)
            scene_res = {"ok": False, "error": str(exc)}
    else:
        scene_res = {"ok": True, "scanned": 0, "assigned": 0, "missed": 0,
                     "skipped": "SCENE_LAYER_ENABLED=false"}
    try:
        from lantai.workers.digest_worker import run_digest_once
        digest_res = run_digest_once()
    except Exception as exc:
        logger.warning("mem_sync digest 重算失败（继续）: %s", exc)
        digest_res = {"ok": False, "error": str(exc)}
    wiki_res: dict = {}
    if settings.WIKI_ENABLED:
        try:
            from lantai.services.wiki_service import run_wiki_update_once
            wiki_res = run_wiki_update_once()
        except Exception as exc:
            logger.warning("mem_sync wiki 刷新失败（继续）: %s", exc)
            wiki_res = {"ok": False, "error": str(exc)}
    else:
        wiki_res = {"ok": True, "skipped": "WIKI_ENABLED=false"}
    return {"ok": True, "command": "mem:sync", "scene": scene_res,
            "digest": digest_res, "wiki": wiki_res,
            "took_ms": int((time.monotonic() - started) * 1000)}


def create_skill(name: str, description: str = "", steps: list[str] | None = None,
                 tags: list[str] | None = None) -> dict:
    """显式沉淀 Skill 资产（借鉴腾讯 mem:create-skill）。

    结构化直落（零 LLM）：memory_type="skill" + structure{name,description,steps}
    + decay_class="procedural"（永不衰减），进向量库 + FTS5，可被 shell_hook 以
    ## Skill 块注入。幂等：内容 sha256 作 key，重复沉淀返回既有记忆。
    校验失败（name/steps 非法）不落库（宁 miss 不脏写）。
    """
    name = (name or "").strip()
    steps = [str(x).strip() for x in (steps or []) if str(x).strip()]
    if not name:
        return {"ok": False, "error": "name must be a non-empty string"}
    if not steps:
        return {"ok": False, "error": "steps must be a non-empty list"}
    description = (description or "").strip()
    content = f"{name}\n{description}\n" + "\n".join(
        f"{i + 1}. {s}" for i, s in enumerate(steps))
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with db.get_session() as s:
        existing = s.exec(select(MemoryItem).where(
            MemoryItem.memory_type == "skill",
            MemoryItem.key == h,
            MemoryItem.status == "active")).first()
        if existing:
            return {"ok": True, "memory_id": existing.id, "dedup": True}
        emb = embed([content])[0]
        mem = MemoryItem(
            id=new_id("mem"),
            memory_type="skill",
            key=h,
            content=content,
            structure={"name": name, "description": description, "steps": steps},
            tags=tags or [],
            lane="general",
            tier=MemoryTier.LONG_TERM,
            confidence=1.0,
            importance=0.5,
            decay_class="procedural",  # 技能资产常青，永不衰减
        )
        s.add(mem)
        s.flush()
        index_memory_item(mem.id, emb, {"key": mem.key, "memory_type": mem.memory_type})
        sync_fts(s, mem.id, mem.content)
        s.commit()
        return {"ok": True, "memory_id": mem.id, "dedup": False}