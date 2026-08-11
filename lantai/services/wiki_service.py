"""记忆 Wiki 服务（借鉴 TencentDB Agent Memory LLM-Wiki ingest-v2 窄版落点）。

场景/技能 → 持续维护的知识库 docs/memory-wiki/：
  - pages/{slug}.md  每场景/技能一页（frontmatter: type/title/description/timestamp）
  - index.md         全页索引（按类型分组，稳定输出，先看目录再钻取）
  - overview.md      全局综述（LLM 优先，失败/关闭 → 确定性综述）+ [[wikilink]] 下钻

增量维护：run_wiki_update_once() 按当前库状态幂等重写（mem_sync 挂接），
页随场景/技能增删自动增删；核心渲染均为纯函数（不 mock 冒烟可测）。
"""
import re
import time
from datetime import datetime
from pathlib import Path

from sqlmodel import select

from lantai.core.settings import settings
from lantai.core.text import truncate_codepoints
from lantai.models.tables import MemoryItem, MemoryScene
from lantai.services.scene_service import cosine_sim
from lantai.storage import db

# 仓库根 = lantai/services/ → lantai/ → 仓库根
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_WIKI_DIR = _REPO_ROOT / "docs" / "memory-wiki"

_WIKI_OVERVIEW_SYS = (
    "你是知识库维护者。根据全部页面清单（标题 + 类型 + 描述），写一篇 2-5 段的全局综述，"
    "把各主题串成连贯叙事，帮助读者快速建立整体认知。要求："
    "1) 按主题组织，不要逐条罗列；"
    "2) 用 [[页面标题]] wikilink 指向具体页（只用标题，不带路径后缀）；"
    "3) 只输出 JSON {\"overview\": \"综述正文\"}。"
)


def wiki_dir() -> Path:
    """Wiki 输出目录：settings.WIKI_OUTPUT_DIR 为空时默认仓库 docs/memory-wiki。"""
    return Path(settings.WIKI_OUTPUT_DIR) if settings.WIKI_OUTPUT_DIR \
        else _DEFAULT_WIKI_DIR


def slugify(name: str) -> str:
    """页面 slug（纯函数）：保留字母数字/CJK/下划线，其余转 "-"；空 → "page"。"""
    if not isinstance(name, str):
        return "page"
    cleaned = re.sub(r"[^\w-]+", "-", name.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned or "page"


def _fmt_ts(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def render_scene_page(scene, members: list, related: list) -> str:
    """场景页（纯函数）：frontmatter + 摘要 + 成员记忆 + 相关场景 wikilink。"""
    member_total = len(members)
    shown = members[: max(0, settings.WIKI_PAGE_MAX_MEMBERS)]
    lines = [
        "---",
        "type: scene",
        f"title: {scene.name}",
        f"description: {scene.summary or ''}",
        f"timestamp: {_fmt_ts(scene.updated_at)}",
        f"member_count: {member_total}",
        "---",
        "",
        f"# {scene.name}",
    ]
    if scene.summary:
        lines += ["", scene.summary]
    lines += ["", "## 成员记忆"]
    if shown:
        for m in shown:
            snippet = truncate_codepoints(m.content or "", settings.WIKI_MEMBER_CHARS, "…")
            lines.append(f"- [{m.lane}] {m.key}: {snippet}")
        if member_total > len(shown):
            lines.append(f"_（共 {member_total} 条，仅列前 {len(shown)} 条）_")
    else:
        lines.append("_（暂无成员）_")
    if related:
        lines += ["", "## 相关场景"]
        for name, sim in related:
            lines.append(f"- [[{name}]]（相似度 {sim:.2f}）")
    return "\n".join(lines) + "\n"


def render_skill_page(item) -> str:
    """技能页（纯函数）：description + 步骤 + 标签（Skill 资产）。"""
    st = item.structure or {}
    name = st.get("name") or item.key or "技能"
    description = (st.get("description") or "").strip()
    steps = [s for s in (st.get("steps") or []) if isinstance(s, str) and s.strip()]
    lines = [
        "---",
        "type: skill",
        f"title: {name}",
        f"description: {description}",
        f"timestamp: {_fmt_ts(item.updated_at)}",
        "---",
        "",
        f"# {name}",
    ]
    if description:
        lines += ["", description]
    if steps:
        lines += ["", "## 步骤"]
        lines += [f"{i}. {s.strip()}" for i, s in enumerate(steps, 1)]
    if item.tags:
        lines += ["", "## 标签", ", ".join(str(t) for t in item.tags)]
    return "\n".join(lines) + "\n"


def render_wiki_index(briefs: list) -> str:
    """全页索引（纯函数）：按类型分组（场景 → 技能 → 其它），同组按标题排序。"""
    lines = ["# 记忆 Wiki 索引", "", f"共 {len(briefs)} 页。"]
    emitted = set()
    for ptype, heading in (("scene", "场景"), ("skill", "技能")):
        items = sorted((b for b in briefs if b["type"] == ptype), key=lambda b: b["title"])
        if not items:
            continue
        emitted.update(b["slug"] for b in items)
        lines += ["", f"## {heading}"]
        for b in items:
            desc = f" — {b['description']}" if b.get("description") else ""
            lines.append(f"- [{b['title']}](pages/{b['slug']}.md){desc}")
    others = [b for b in briefs if b["slug"] not in emitted]
    if others:
        lines += ["", "## 其它"]
        for b in sorted(others, key=lambda b: b["title"]):
            lines.append(f"- [{b['title']}](pages/{b['slug']}.md)")
    return "\n".join(lines) + "\n"


def render_overview_fallback(briefs: list, stats: dict) -> str:
    """确定性综述（纯函数）：LLM 不可用时保证 overview 仍存在（零侵入兜底）。"""
    n_scene = stats.get("scene_count", 0)
    n_skill = stats.get("skill_count", 0)
    n_member = stats.get("member_count", 0)
    tops = stats.get("top_scenes", [])
    lines = [
        "---",
        "type: overview",
        "title: Overview",
        "description: 记忆 Wiki 全局综述（确定性兜底）",
        "---",
        "",
        "# 记忆 Wiki 综述",
        "",
        f"当前知识库共 {n_scene} 个场景、{n_skill} 个技能、覆盖 {n_member} 条记忆。",
    ]
    if tops:
        lines += ["", "## 热门场景"]
        for name, heat in tops:
            lines.append(f"- [[{name}]]（heat {heat}）")
    skills = sorted((b for b in briefs if b["type"] == "skill"), key=lambda b: b["title"])
    if skills:
        lines += ["", "## 技能资产"]
        for b in skills:
            lines.append(f"- [[{b['title']}]]（{b.get('description') or '可复用步骤'}）")
    if not tops and not skills:
        lines += ["", "_知识库为空：先沉淀记忆并重建场景，Wiki 会自动跟进。_"]
    lines += ["", "---", "本综述由确定性渲染生成（LLM 综述失败/关闭时的兜底）。"]
    return "\n".join(lines) + "\n"


def _overview_llm(briefs: list) -> str | None:
    """LLM 综述（外部依赖：允许 mock；失败返回 None → 确定性兜底）。"""
    try:
        from lantai.llm.client import chat_json
        list_text = "\n".join(
            f"- {b['title']} [{b['type']}] {b.get('description') or ''}" for b in briefs)
        data = chat_json(_WIKI_OVERVIEW_SYS,
                         f"## Wiki Pages\n{list_text}\n\nWrite the global overview.")
        body = (data.get("overview") or "").strip()
        return body or None
    except Exception:
        return None



def _related_scenes(scene, scenes: list, top: int) -> list:
    """相关场景（纯计算）：按质心余弦取最相似的 top 个（>0），排除自身。"""
    if not scene.centroid:
        return []
    scored = []
    for other in scenes:
        if other.id == scene.id or not other.centroid:
            continue
        sim = cosine_sim(scene.centroid, other.centroid)
        if sim > 0:
            scored.append((other.name, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[: max(0, top)]


def run_wiki_update_once(overview_llm: bool | None = None) -> dict:
    """增量维护入口：场景/技能 → 页面 → index → overview（幂等，mem_sync 挂接）。

    返回 {"ok", "dir", "pages", "stale_removed", "overview", "took_ms"}；
    异常不静默吞——调用方（mem_sync/CLI）自行降级。
    """
    started = time.monotonic()
    out_dir = wiki_dir()
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    with db.get_session() as s:
        scenes = s.exec(select(MemoryScene).order_by(MemoryScene.heat.desc())).all()
        members_by_scene = {}
        for sc in scenes:
            members_by_scene[sc.id] = s.exec(select(MemoryItem).where(
                MemoryItem.scene_id == sc.id,
                MemoryItem.status == "active")).all()
        skills = s.exec(select(MemoryItem).where(
            MemoryItem.memory_type == "skill",
            MemoryItem.status == "active")).all()

    # ── 写页面（幂等覆盖）──
    briefs = _collect_pages_from_scenes(scenes, skills)
    current_slugs = set()
    for sc in scenes:
        members = members_by_scene.get(sc.id) or []
        related = _related_scenes(sc, scenes, settings.WIKI_RELATED_TOP)
        slug = slugify(sc.name)
        current_slugs.add(slug)
        (pages_dir / f"{slug}.md").write_text(
            render_scene_page(sc, members, related), encoding="utf-8")
    for item in skills:
        st = item.structure or {}
        title = st.get("name") or item.key or "技能"
        slug = slugify(title)
        current_slugs.add(slug)
        (pages_dir / f"{slug}.md").write_text(
            render_skill_page(item), encoding="utf-8")

    # ── 清理过期页（页随场景/技能删除而消失；仅限 pages 目录内）──
    stale_removed = 0
    for old in pages_dir.glob("*.md"):
        if old.resolve().parent != pages_dir.resolve():
            continue
        if old.stem not in current_slugs:
            old.unlink()
            stale_removed += 1

    # ── 索引 + 综述 ──
    (out_dir / "index.md").write_text(render_wiki_index(briefs), encoding="utf-8")
    use_llm = overview_llm if overview_llm is not None else settings.WIKI_OVERVIEW_LLM
    body = _overview_llm(briefs) if use_llm else None
    overview_source = "llm" if body else "fallback"
    if not body:
        member_total = sum(len(v) for v in members_by_scene.values())
        top_scenes = [(sc.name, sc.heat) for sc in scenes[: settings.WIKI_RELATED_TOP]]
        stats = {"scene_count": len(scenes), "skill_count": len(skills),
                 "member_count": member_total, "top_scenes": top_scenes}
        body = render_overview_fallback(briefs, stats)
    (out_dir / "overview.md").write_text(body, encoding="utf-8")

    return {"ok": True, "dir": str(out_dir), "pages": len(current_slugs),
            "stale_removed": stale_removed, "overview": overview_source,
            "took_ms": int((time.monotonic() - started) * 1000)}


def _collect_pages_from_scenes(scenes: list, skills: list) -> list:
    """场景/技能列表 → 页面 briefs（纯计算，供索引与综述）。"""
    briefs = []
    for sc in scenes:
        briefs.append({"slug": slugify(sc.name), "title": sc.name,
                       "type": "scene", "description": sc.summary or ""})
    for item in skills:
        st = item.structure or {}
        title = st.get("name") or item.key or "技能"
        briefs.append({"slug": slugify(title), "title": title,
                       "type": "skill", "description": (st.get("description") or "").strip()})
    return briefs


def read_wiki_page(slug: str) -> dict:
    """读取 Wiki 页（MCP wiki_read 用）：slug 白名单化 + 仅允许 pages 目录内。"""
    page_slug = slugify(slug) if isinstance(slug, str) else "page"
    pages_dir = (wiki_dir() / "pages").resolve()
    path = (pages_dir / f"{page_slug}.md").resolve()
    if pages_dir != path.parent:
        raise ValueError("slug 解析路径超出 wiki pages 目录")
    if not path.is_file():
        raise FileNotFoundError(f"wiki 页面不存在: {page_slug}.md")
    return {"slug": page_slug, "path": str(path),
            "content": path.read_text(encoding="utf-8")}