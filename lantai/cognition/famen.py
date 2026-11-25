"""法门（Famen）程序性技能资产与执行流水线引擎 (ADR-0067)。

出处：《法华经》「深入法门，通达诸法」；喻指可循之法、可行之门与标准作业规程（SOP）。
职责：
1. 聚合全库程序性规则（MemoryItem procedural steps）与技能结晶（SkillCrystal）；
2. 提取标准 SOP 结构（名称、触发条件、步骤列表、参数插槽）；
3. 基于任务意图与关键词相关度执行动态触发规则匹配；
4. 步骤明细强制使用樊篱（wrap_as_data）防注入围栏包裹；
5. 落实租户、属主与公共共享范围行级 ACL 隔离（宁 miss 不脏写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import jieba
from sqlmodel import Session, col, select

from lantai.core.acl import federation_visible, is_system_viewer, viewer_of
from lantai.core.auth import Principal
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryItem, SkillCrystal
from lantai.storage import db


@dataclass
class SkillAsset:
    """归一化的法门程序性技能资产。"""

    id: str
    name: str
    trigger_rule: str
    procedure: str
    steps: list[str] = field(default_factory=list)
    source_type: str = "crystal"  # crystal | procedural_memory
    confidence: float = 1.0
    shared_scope: str = "private"
    user_id: str | None = None
    tenant_id: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sop: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, fenced: bool = False) -> dict[str, Any]:
        """序列化为字典，支持樊篱安全包裹。"""
        if fenced:
            safe_name = wrap_as_data(self.name, item_id=self.id)
            fenced_steps = [wrap_as_data(s, item_id=self.id) for s in self.steps]
            fenced_proc = wrap_as_data(self.procedure, item_id=self.id)
            fenced_trigger = wrap_as_data(self.trigger_rule, item_id=self.id)
            fenced_sop = _fence_sop(self.sop, item_id=self.id)
            return {
                "id": self.id,
                "name": safe_name,
                "fenced_name": safe_name,
                "trigger_rule": fenced_trigger,
                "procedure": fenced_proc,
                "fenced_procedure": fenced_proc,
                "steps": fenced_steps,
                "description": fenced_sop["description"],
                "sop": fenced_sop,
                "source_type": self.source_type,
                "confidence": self.confidence,
                "shared_scope": self.shared_scope,
                "user_id": self.user_id,
                "tenant_id": self.tenant_id,
                "metadata": self.metadata,
            }
        return {
            "id": self.id,
            "name": self.name,
            "trigger_rule": self.trigger_rule,
            "procedure": self.procedure,
            "steps": self.steps,
            "description": self.sop.get("description", ""),
            "sop": self.sop,
            "source_type": self.source_type,
            "confidence": self.confidence,
            "shared_scope": self.shared_scope,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
        }


_SOP_FIELDS = (
    "description",
    "preconditions",
    "steps",
    "parameters",
    "verification_steps",
    "rollback_steps",
)
_SOP_LIST_FIELDS = set(_SOP_FIELDS) - {"description"}
_SOP_HEADER_ALIASES = {
    "description": ("用途", "简介", "描述", "目的", "概述", "description", "overview", "purpose"),
    "preconditions": (
        "前置条件",
        "前置检查",
        "准备工作",
        "前提条件",
        "prerequisites",
        "preconditions",
        "preflight",
        "preflight checks",
        "requirements",
        "setup",
    ),
    "steps": (
        "执行步骤",
        "操作步骤",
        "具体步骤",
        "步骤",
        "流程",
        "steps",
        "procedure",
        "execution steps",
        "procedure steps",
        "instructions",
        "workflow",
        "actions",
    ),
    "parameters": (
        "参数插槽",
        "参数列表",
        "参数",
        "variables",
        "parameters",
        "parameter slots",
        "arguments",
        "inputs",
        "configuration",
    ),
    "verification_steps": (
        "验证步骤",
        "验证",
        "检查点",
        "验收",
        "verification",
        "validation",
        "verification steps",
        "validation steps",
        "acceptance criteria",
        "checks",
    ),
    "rollback_steps": (
        "回滚步骤",
        "回滚",
        "回退",
        "rollback",
        "recovery",
        "rollback steps",
        "recovery steps",
        "backout",
    ),
}
_LIST_MARKER_RE = re.compile(
    r"^(?:[-*+•]\s*|\d+[.)、]\s*|[一二三四五六七八九十]+[.)、．]\s*|"
    r"步骤\s*(?:\d+|[一二三四五六七八九十]+)\s*[:：.)、．]\s*)"
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_LATIN_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+#.-]*")
_EXCLUSION_RE = re.compile(
    r"(?:不适用于|不适用|不适合|不触发|不要用于|排除|not\s+applicable\s+for|not\s+for|except|exclude)"
    r"\s*[:：]?\s*([^。！？!?；;\n]+)",
    re.IGNORECASE,
)
_MATCH_STOPWORDS = frozenset(
    [
        "一个",
        "这个",
        "那个",
        "进行",
        "使用",
        "用于",
        "适用",
        "适用于",
        "触发",
        "任务",
        "情况",
        "问题",
        "执行",
        "处理",
        "相关",
        "相关的",
        "步骤",
        "流程",
        "操作",
        "检查",
        "确认",
        "可以",
        "需要",
        "如果",
        "当",
        "时",
        "在",
        "并",
        "与",
        "及",
        "或",
        "的",
        "了",
        "是",
        "和",
        "不",
        "要",
        "请",
        "the",
        "and",
        "for",
        "when",
        "with",
        "from",
        "this",
        "that",
        "use",
        "used",
        "using",
        "run",
        "task",
        "steps",
        "procedure",
        "applicable",
        "only",
        "not",
        "except",
        "exclude",
    ]
)


def _clean_list_item(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    text = _LIST_MARKER_RE.sub("", text, count=1).strip()
    return re.sub(
        r"^步骤\s*(?:\d+|[一二三四五六七八九十]+)\s*[:：.)、．]\s*",
        "",
        text,
    ).strip()


def _as_text_list(value: Any) -> list[str]:
    """把已有 SOP 列表字段规范成非空文本项。"""
    values: list[Any] | tuple[Any, ...]
    if isinstance(value, str):
        values = value.splitlines()
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        return []
    return [cleaned for item in values if (cleaned := _clean_list_item(item))]


def _section_header(line: str) -> tuple[str, str] | None:
    text = re.sub(r"^\s*#{1,6}\s*", "", line).strip().strip("*_` ")
    text = re.sub(r"^(?:\d+[.)、]\s*|[一二三四五六七八九十]+[.)、．]\s*)", "", text)
    text = re.sub(r"^(?:\d+[.)、]\s*|[一二三四五六七八九十]+[.)、．]\s*)", "", text)
    for field_name, aliases in _SOP_HEADER_ALIASES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            match = re.match(rf"^{re.escape(alias)}\s*(?::|：)\s*(.*)$", text, re.IGNORECASE)
            if match:
                return field_name, match.group(1).strip()
            if text.casefold() == alias.casefold():
                return field_name, ""
    return None


def _empty_sop() -> dict[str, Any]:
    return {name: "" if name == "description" else [] for name in _SOP_FIELDS}


def _extract_sop(procedure: str, structure: dict[str, Any] | None = None) -> dict[str, Any]:
    """从自由文本与既有 structure 中提取稳定的 SOP 栏目。"""
    parsed = _empty_sop()
    active_section: str | None = None
    fallback_steps: list[str] = []

    for raw_line in (procedure or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        header = _section_header(line)
        if header:
            active_section, inline_value = header
            if inline_value:
                if active_section in _SOP_LIST_FIELDS:
                    parsed[active_section].append(_clean_list_item(inline_value))
                else:
                    parsed[active_section] = inline_value
            continue

        marked = bool(_LIST_MARKER_RE.match(line))
        value = _clean_list_item(line)
        if active_section:
            if active_section in _SOP_LIST_FIELDS:
                parsed[active_section].append(value)
            else:
                parsed[active_section] = "\n".join(
                    part for part in (parsed[active_section], value) if part
                )
        elif marked:
            fallback_steps.append(value)
        else:
            parsed["description"] = "\n".join(
                part for part in (parsed["description"], value) if part
            )

    if not parsed["steps"]:
        parsed["steps"] = fallback_steps

    if isinstance(structure, dict):
        for field_name in _SOP_FIELDS:
            structured_value = structure.get(field_name)
            if field_name == "description":
                if isinstance(structured_value, str) and structured_value.strip():
                    parsed[field_name] = structured_value.strip()
            else:
                structured_values = _as_text_list(structured_value)
                if structured_values:
                    parsed[field_name] = structured_values

    for field_name in _SOP_LIST_FIELDS:
        parsed[field_name] = _as_text_list(parsed[field_name])
    return parsed


def _extract_steps(procedure: str) -> list[str]:
    """兼容入口：只返回明确编号/列表或 SOP 执行步骤小节。"""
    return _extract_sop(procedure)["steps"]


def _fence_sop(sop: dict[str, Any], *, item_id: str) -> dict[str, Any]:
    """围栏化所有进入执行投影的 SOP 正文。"""
    result: dict[str, Any] = {}
    for field_name in _SOP_FIELDS:
        value = sop.get(field_name, "" if field_name == "description" else [])
        if field_name == "description":
            result[field_name] = wrap_as_data(str(value or ""), item_id=item_id)
        else:
            result[field_name] = [
                wrap_as_data(str(item), item_id=item_id) for item in _as_text_list(value)
            ]
    return result


def _match_tokens(text: str) -> set[str]:
    """提取稳定的中英文意图词项，减少中文整句被当成单一 token 的漏配。"""
    normalized = (text or "").casefold()
    tokens = {word.strip() for word in jieba.cut(normalized) if word.strip()}
    latin_tokens = _LATIN_TOKEN_RE.findall(normalized)
    tokens.update(latin_tokens)
    tokens.update(part for token in latin_tokens for part in re.split(r"[-_.]+", token) if part)
    tokens.update(
        bigram
        for run in _CJK_RE.findall(normalized)
        for bigram in (run[index : index + 2] for index in range(len(run) - 1))
    )
    return {token for token in tokens if token not in _MATCH_STOPWORDS and len(token) > 1}


def _excluded_by_trigger(task_tokens: set[str], trigger_rule: str) -> bool:
    for match in _EXCLUSION_RE.finditer(trigger_rule or ""):
        excluded_tokens = _match_tokens(match.group(1))
        if not excluded_tokens:
            continue
        overlap = task_tokens & excluded_tokens
        if overlap and len(overlap) / len(excluded_tokens) >= 0.5:
            return True
    return False


def _trigger_text(value: Any, *, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (list, tuple)):
        values = [str(item).strip() for item in value if str(item).strip()]
        if values:
            return "；".join(values)
    return fallback


def _crystal_asset(crystal: SkillCrystal) -> SkillAsset:
    sop = _extract_sop(crystal.procedure)
    return SkillAsset(
        id=crystal.id,
        name=crystal.skill_name,
        trigger_rule=_trigger_text(
            crystal.trigger_rule,
            fallback=f"适用任务关键词: {crystal.skill_name}",
        ),
        procedure=crystal.procedure,
        steps=sop["steps"],
        source_type="crystal",
        sop=sop,
        confidence=1.0,
        shared_scope=crystal.shared_scope,
        user_id=crystal.user_id,
        tenant_id=crystal.tenant_id,
        created_at=crystal.created_at,
        metadata={"hit_count": crystal.hit_count, "success_count": crystal.success_count},
    )


def _memory_asset(memory: MemoryItem) -> SkillAsset:
    struct = memory.structure if isinstance(memory.structure, dict) else {}
    sop = _extract_sop(memory.content, struct)
    skill_name = struct.get("name") or memory.key or f"sop-{memory.id[:8]}"
    return SkillAsset(
        id=memory.id,
        name=str(skill_name),
        trigger_rule=_trigger_text(
            struct.get("trigger_rule") or struct.get("triggers"),
            fallback=memory.content[:100],
        ),
        procedure=memory.content,
        steps=sop["steps"],
        source_type="procedural_memory",
        sop=sop,
        confidence=memory.confidence if memory.confidence is not None else 0.9,
        shared_scope="private",
        user_id=memory.user_id,
        tenant_id=memory.tenant_id,
        created_at=memory.created_at,
        metadata={"lane": memory.lane, "decay_class": memory.decay_class},
    )


class FamenEngine:
    """法门（Famen）程序性技能资产与执行流水线引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
        federation_id: str | None = None,
        shared_scope: str = "private",
    ) -> bool:
        """行级安全隔离校验（宁 miss 不脏写）。

        - admin 全权放行；
        - shared_scope == 'swarm_shared' 全局公共共享放行；
        - 租户匹配且属主匹配放行。
        """
        if getattr(self.principal, "is_admin", False) or is_system_viewer(self.principal):
            return True

        # 公共结晶开放给集群伙伴
        # 票 57：无身份收敛 "default"（同 zhaji 口径），不再全放
        if self.principal is None:
            if tenant_id is not None or federation_id is not None:
                return False
            if shared_scope == "swarm_shared":
                return True
            return owner_id in (None, "default")

        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)

        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False
        if not federation_visible(self.principal, federation_id):
            return False
        if shared_scope == "swarm_shared":
            return True

        return not (owner_id is not None and owner_id != viewer)

    def _collect_all_skills(self, s: Session) -> list[SkillAsset]:
        """从 DB 中聚合 SkillCrystal 与 MemoryItem procedural 步骤。"""
        assets: list[SkillAsset] = []

        # 1. 扫描已审核/已晋升的 SkillCrystal
        crystals = s.exec(
            select(SkillCrystal).where(col(SkillCrystal.status).in_(("approved", "promoted")))
        ).all()
        for sc in crystals:
            if not self._check_access(
                owner_id=sc.user_id,
                tenant_id=sc.tenant_id,
                federation_id=getattr(sc, "federation_id", None),
                shared_scope=sc.shared_scope,
            ):
                continue
            assets.append(_crystal_asset(sc))

        # 2. 扫描 procedural 或带 structure.steps 的 MemoryItem
        memories = s.exec(
            select(MemoryItem).where(
                MemoryItem.status == "active",
                # 票 62：lifecycle 过滤——supersede 只写 lifecycle_status=superseded
                # （status 仍 active），只看 status 时已废除 SOP 照混进技能集。
                # weakened 语义「仍参与检索但权重降低」放行。
                col(MemoryItem.lifecycle_status).in_(("active", "weakened")),
                (col(MemoryItem.decay_class) == "procedural")
                | (col(MemoryItem.lane).in_(("rule", "skill"))),
            )
        ).all()
        for m in memories:
            if not self._check_access(
                owner_id=m.user_id,
                tenant_id=m.tenant_id,
                federation_id=getattr(m, "federation_id", None),
                shared_scope=getattr(m, "shared_scope", "private"),
            ):
                continue
            assets.append(_memory_asset(m))

        return assets

    def list_skills(self, limit: int = 50, *, fenced: bool = False) -> list[dict[str, Any]]:
        """获取当前租户权限范围内的法门列表。"""
        if self.session is not None:
            assets = self._collect_all_skills(self.session)
            return [a.to_dict(fenced=fenced) for a in assets[:limit]]

        with db.get_session() as s:
            assets = self._collect_all_skills(s)
            return [a.to_dict(fenced=fenced) for a in assets[:limit]]

    def get_skill_detail(self, skill_id: str) -> dict[str, Any] | None:
        """获取单个法门的详细 SOP，带樊篱保护包裹。"""

        def _find(s: Session) -> dict[str, Any] | None:
            # 1. 尝试找 SkillCrystal
            sc = s.exec(select(SkillCrystal).where(SkillCrystal.id == skill_id)).first()
            if sc:
                if not self._check_access(
                    owner_id=sc.user_id,
                    tenant_id=sc.tenant_id,
                    federation_id=getattr(sc, "federation_id", None),
                    shared_scope=sc.shared_scope,
                ):
                    return None
                return _crystal_asset(sc).to_dict(fenced=True)

            # 2. 尝试找 MemoryItem
            m = s.exec(select(MemoryItem).where(MemoryItem.id == skill_id)).first()
            if m:
                # 票 62：superseded/retired 行不再作为技能详情直取（同列表口径）
                if m.status != "active" or (m.lifecycle_status or "") not in (
                    "active",
                    "weakened",
                ):
                    return None
                if not self._check_access(
                    owner_id=m.user_id,
                    tenant_id=m.tenant_id,
                    federation_id=getattr(m, "federation_id", None),
                    shared_scope=getattr(m, "shared_scope", "private"),
                ):
                    return None
                return _memory_asset(m).to_dict(fenced=True)

            return None

        if self.session is not None:
            return _find(self.session)
        with db.get_session() as s:
            return _find(s)

    def match_skills(self, task: str, limit: int = 5) -> list[dict[str, Any]]:
        """根据任务描述与触发条件进行相关度匹配。"""
        if not task or not isinstance(task, str):
            return []
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return []
        task_tokens = _match_tokens(task)
        if not task_tokens:
            return []

        def _score(asset: SkillAsset) -> float:
            trigger_tokens = _match_tokens(asset.trigger_rule)
            name_tokens = _match_tokens(asset.name)
            step_tokens = _match_tokens(" ".join(asset.steps))
            if _excluded_by_trigger(task_tokens, asset.trigger_rule):
                return 0.0

            # 1. 任务意图与名称/触发规则/步骤的加权覆盖。
            name_overlap = task_tokens & name_tokens
            trigger_overlap = task_tokens & trigger_tokens
            step_overlap = task_tokens & step_tokens
            score = (
                (len(name_overlap) / max(len(name_tokens), 1)) * 4.0
                + (len(trigger_overlap) / max(len(trigger_tokens), 1)) * 3.0
                + (len(step_overlap) / max(len(step_tokens), 1)) * 1.0
            )

            # 短技术标识的精确出现应显著高于泛化中文字符重合。
            exact_terms = {
                token
                for token in task_tokens
                if token.isascii() and len(token) >= 2 and token in trigger_tokens | name_tokens
            }
            score += len(exact_terms) * 2.0

            return score

        if self.session is not None:
            all_assets = self._collect_all_skills(self.session)
        else:
            with db.get_session() as s:
                all_assets = self._collect_all_skills(s)

        scored: list[tuple[float, SkillAsset]] = []
        for a in all_assets:
            score = _score(a)
            if score >= 1.0:
                scored.append((score, a))

        scored.sort(key=lambda x: (-x[0], -(x[1].confidence or 0.0), x[1].id))
        results: list[dict[str, Any]] = []
        for score, a in scored[:limit]:
            d = a.to_dict(fenced=True)
            d["score"] = round(score, 2)
            results.append(d)

        return results
