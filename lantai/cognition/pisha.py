"""披沙（Pisha · 《世说新语·德行》「披沙拣金，往往见宝」）：候选记忆递归提纯去噪、多轨指代消解与认知引擎收敛 (ADR-0077)。

职责：
1. 建立结构化提纯契约 PishaResult（原置信度与重估置信度、指代消解明细、降噪标签等）；
2. 针对提取候选处于模糊带（0.2~0.5）或弱指代口语文本，提供多轨提纯去噪纯函数；
3. 支持结合上下文与实体线索（entity_hints）进行确定性消解与去粗取精；
4. 全面集成樊篱防注入围栏（wrap_as_data）；
5. 落实行级租户与属主安全隔离（宁漏不越权、宁 miss 不脏写）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from lantai.core.acl import viewer_of
from lantai.core.auth import Principal
from lantai.core.logger import logger
from lantai.llm import client as llm_client
from lantai.llm.fence import wrap_as_data
from lantai.models.tables import MemoryCandidate
from lantai.storage import db

# 常见口语化副词、助词、废话前缀列表（启发式去噪）
COLLOQUIAL_NOISE_PATTERNS = [
    r"^(嗯|啊|哦|好的|其实|那个|就是说|坦白讲|说实话|总的来说|我觉得吧)[，,\s]*",
    r"[，,\s]*(吧|呢|啊|哈|啦|喽)[！!？?。.]*$",
]

REFINE_SYSTEM_PROMPT = """你是一个严谨的记忆精炼与事实提纯专家（兰台记忆·披沙系统）。
你的任务是对提取出的模糊候选记忆进行指代消解、事实提纯与去噪。

【精炼要求】
1. 指代消解：将“他/它/那个/这件事”等弱指代根据上下文还原为具体的主语、实体名称或技术栈；
2. 去粗取精：剔除口语化副词、助词、客套修饰，提炼为精炼且信息完整的陈述句；
3. 原子化收敛：确保记忆表达单一、清晰的事实、偏好或规则；
4. 真实性与置信度重估：
   - 若提纯后事实确凿且实体完整：输出 is_valid=true，confidence 置为 0.70~0.95；
   - 若原内容纯属无意义社交客套、闲聊废话：输出 is_valid=false，confidence=0.0；
5. 必须返回严格的 JSON 格式：
{
  "refined_text": "精炼后的记忆陈述句",
  "confidence": 0.85,
  "is_valid": true,
  "lane": "general",
  "tags": ["标签1"],
  "reason": "精炼与提纯说明",
  "pronoun_resolutions": {"那个工具": "Docker"}
}
"""


@dataclass
class PishaResult:
    """披沙提纯去噪结果契约。"""

    original_text: str
    refined_text: str
    original_confidence: float
    refined_confidence: float
    is_valid: bool
    lane: str = "general"
    pronoun_resolutions: dict[str, str] = field(default_factory=dict)
    denoise_tags: list[str] = field(default_factory=list)
    reason: str = ""
    fenced_content: str = ""
    llm_failed: bool = False

    def to_dict(self, *, fenced: bool = True) -> dict[str, Any]:
        content_to_show = (
            self.fenced_content if (fenced and self.fenced_content) else self.refined_text
        )
        return {
            "original_text": self.original_text,
            "refined_text": self.refined_text,
            "fenced_content": content_to_show,
            "original_confidence": round(self.original_confidence, 4),
            "refined_confidence": round(self.refined_confidence, 4),
            "is_valid": self.is_valid,
            "lane": self.lane,
            "pronoun_resolutions": self.pronoun_resolutions,
            "denoise_tags": self.denoise_tags,
            "reason": self.reason,
            "llm_failed": self.llm_failed,
        }


class PishaEngine:
    """披沙（Pisha）候选记忆递归提纯去噪与指代消解引擎。"""

    def __init__(self, session: Session | None = None, principal: Principal | None = None):
        self.session = session
        self.principal = principal

    def _check_access(
        self,
        *,
        owner_id: str | None,
        tenant_id: str | None,
    ) -> bool:
        """行级安全隔离校验（宁漏不越权）。"""
        if self.principal is None:
            return True
        if getattr(self.principal, "is_admin", False):
            return True

        viewer = viewer_of(self.principal)
        current_tenant = getattr(self.principal, "tenant_id", None)

        if tenant_id and current_tenant and tenant_id != current_tenant:
            return False

        return not (owner_id is not None and owner_id != viewer)

    @staticmethod
    def _apply_heuristic_denoise(text: str) -> tuple[str, list[str]]:
        """启发式口语化去噪。"""
        cleaned = text.strip()
        tags: list[str] = []
        for pat in COLLOQUIAL_NOISE_PATTERNS:
            new_cleaned = re.sub(pat, "", cleaned)
            if new_cleaned != cleaned:
                tags.append("colloquial_pruned")
                cleaned = new_cleaned.strip()
        return cleaned, tags

    @staticmethod
    def _apply_deterministic_entity_resolution(
        text: str, entity_hints: dict[str, str]
    ) -> tuple[str, dict[str, str]]:
        """基于已知实体映射表的确定性指代消解。"""
        res_map: dict[str, str] = {}
        resolved_text = text
        for pronoun, entity in entity_hints.items():
            if pronoun in resolved_text:
                resolved_text = resolved_text.replace(pronoun, entity)
                res_map[pronoun] = entity
        return resolved_text, res_map

    def refine_text(
        self,
        text: str,
        context: str = "",
        entity_hints: dict[str, str] | None = None,
        initial_confidence: float = 0.4,
    ) -> PishaResult:
        """对单段记忆文本执行多轨提纯、指代消解与置信度重估。"""
        raw_text = (text or "").strip()
        if not raw_text:
            return PishaResult(
                original_text="",
                refined_text="",
                original_confidence=0.0,
                refined_confidence=0.0,
                is_valid=False,
                reason="输入文本为空",
                fenced_content=wrap_as_data("", score=0.0),
            )

        # 1. 启发式去噪与确定性实体消解（前置规则梯队）
        h_cleaned, h_tags = self._apply_heuristic_denoise(raw_text)
        pronoun_map: dict[str, str] = {}
        if entity_hints:
            h_cleaned, pronoun_map = self._apply_deterministic_entity_resolution(
                h_cleaned, entity_hints
            )

        # 2. 调用 LLM 提纯（若有模型可用）
        user_prompt = f"【待精炼候选文本】:\n{h_cleaned}\n"
        if context:
            user_prompt += f"\n【上下文背景】:\n{context}\n"
        if pronoun_map:
            user_prompt += f"\n【已知实体映射线索】:\n{pronoun_map}\n"

        try:
            res = llm_client.chat_json(REFINE_SYSTEM_PROMPT, user_prompt)
            if isinstance(res, dict) and "refined_text" in res:
                refined_text = str(res.get("refined_text", "")).strip() or h_cleaned
                conf = float(res.get("confidence", 0.75))
                is_val = bool(res.get("is_valid", True))
                lane = str(res.get("lane", "general"))
                extra_tags = list(res.get("tags", []))
                reason = str(res.get("reason", "披沙精炼成功"))
                llm_resolutions = res.get("pronoun_resolutions")
                if isinstance(llm_resolutions, dict):
                    pronoun_map.update({str(k): str(v) for k, v in llm_resolutions.items()})

                all_tags = list(
                    set(h_tags + [str(t).strip() for t in extra_tags if str(t).strip()])
                )
                final_conf = min(max(conf, 0.0), 1.0)
                fenced = wrap_as_data(refined_text, score=final_conf)

                return PishaResult(
                    original_text=raw_text,
                    refined_text=refined_text,
                    original_confidence=initial_confidence,
                    refined_confidence=final_conf,
                    is_valid=is_val,
                    lane=lane
                    if lane in ("general", "profile", "preference", "rule", "entity", "project")
                    else "general",
                    pronoun_resolutions=pronoun_map,
                    denoise_tags=all_tags,
                    reason=reason,
                    fenced_content=fenced,
                    llm_failed=False,
                )
        except Exception as exc:
            logger.warning("披沙：LLM 精炼调用异常，进入确定性降级规则: %s", exc)

        # 3. 确定性降级路径（宁 miss 不脏写）
        final_text = h_cleaned or raw_text
        # 若清洗后发现完全是无意义客套词
        if not final_text:
            return PishaResult(
                original_text=raw_text,
                refined_text="",
                original_confidence=initial_confidence,
                refined_confidence=0.0,
                is_valid=False,
                reason="文本清洗后无实质内容",
                fenced_content=wrap_as_data("", score=0.0),
                llm_failed=True,
            )

        fenced = wrap_as_data(final_text, score=initial_confidence)
        return PishaResult(
            original_text=raw_text,
            refined_text=final_text,
            original_confidence=initial_confidence,
            refined_confidence=initial_confidence,
            is_valid=True,
            lane="general",
            pronoun_resolutions=pronoun_map,
            denoise_tags=h_tags + ["fallback_rule"],
            reason="LLM 异常，确定性规则保底",
            fenced_content=fenced,
            llm_failed=True,
        )

    def refine_candidate(self, candidate_id: str) -> dict[str, Any]:
        """对数据库中单条候选执行披沙提纯与状态联动。"""
        cand_id = (candidate_id or "").strip()
        if not cand_id:
            raise ValueError("candidate_id 不能为空")

        if self.session is not None:
            return self._refine_candidate(self.session, cand_id)
        with db.get_session() as s:
            return self._refine_candidate(s, cand_id)

    def _refine_candidate(self, s: Session, cand_id: str) -> dict[str, Any]:
        cand = s.get(MemoryCandidate, cand_id)
        if not cand:
            raise ValueError(f"候选记录未找到: {cand_id}")

        if not self._check_access(owner_id=cand.user_id, tenant_id=cand.tenant_id):
            raise PermissionError(f"无权操作此候选记忆（行级租户/属主隔离）: {cand_id}")

        orig_text = cand.summary or (cand.claims[0] if cand.claims else "")
        initial_conf = float(getattr(cand, "extractor_confidence", 0.4) or 0.4)
        context_str = str(cand.provenance) if cand.provenance else ""

        result = self.refine_text(
            text=orig_text,
            context=context_str,
            initial_confidence=initial_conf,
        )

        if not result.is_valid:
            cand.status = "rejected"
            cand.extractor_confidence = 0.0
            cand.provenance = {
                **(cand.provenance or {}),
                "pisha_rejected": True,
                "pisha_reason": result.reason,
            }
        else:
            cand.summary = result.refined_text
            cand.claims = [result.refined_text]
            cand.extractor_confidence = result.refined_confidence
            cand.lane = result.lane
            cand.provenance = {
                **(cand.provenance or {}),
                "pisha_refined": True,
                "pronoun_resolutions": result.pronoun_resolutions,
                "denoise_tags": result.denoise_tags,
                "pisha_reason": result.reason,
            }

        s.add(cand)
        s.commit()
        s.refresh(cand)

        return {
            "candidate_id": cand.id,
            "status": cand.status,
            "refined_text": cand.summary,
            "confidence": cand.extractor_confidence,
            "is_valid": result.is_valid,
            "pisha_result": result.to_dict(fenced=True),
        }

    def batch_refine_candidates(
        self,
        min_conf: float = 0.2,
        max_conf: float = 0.6,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """批量扫描模糊带候选并执行披沙提纯。"""
        if self.session is not None:
            return self._batch_refine(self.session, min_conf, max_conf, limit)
        with db.get_session() as s:
            return self._batch_refine(s, min_conf, max_conf, limit)

    def _batch_refine(
        self, s: Session, min_conf: float, max_conf: float, limit: int
    ) -> list[dict[str, Any]]:
        stmt = (
            select(MemoryCandidate)
            .where(MemoryCandidate.status == "pending_review")
            .where(MemoryCandidate.extractor_confidence >= min_conf)
            .where(MemoryCandidate.extractor_confidence <= max_conf)
            .limit(limit)
        )
        cands = s.exec(stmt).all()
        refined_list: list[dict[str, Any]] = []
        for c in cands:
            if not self._check_access(owner_id=c.user_id, tenant_id=c.tenant_id):
                continue
            try:
                res = self._refine_candidate(s, c.id)
                refined_list.append(res)
            except Exception as e:
                logger.warning("批处理精炼候选失败 (id=%s): %s", c.id, e)
        return refined_list
