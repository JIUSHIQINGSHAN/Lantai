"""探颐（ADR-0037）：记忆主动探针与自然交互消歧服务。

功能：
1. detect_memory_probes: 检索命中未决冲突时，生成主动澄清探针；
2. format_probing_context: 格式化 Prompt 注入上下文；
3. resolve_probe_response: 根据用户次轮自然语言答复自动闭环消解冲突。
"""

import re

import jieba
from sqlmodel import Session, col, select
from ulid import ULID

from lantai.core.logger import loggable, logger
from lantai.core.settings import settings
from lantai.core.time import iso_utc, utcnow
from lantai.models.tables import ConflictEvent, FactAtomic, MemoryCheckpoint, MemoryItem
from lantai.retrieval.wanbi_retriever import fact_source_alive_clause
from lantai.security.injection_guard import (
    assert_no_prompt_injection,
    is_blocked_by_injection_guard,
)
from lantai.services.record_ops_service import sync_vector_upsert
from lantai.storage import db
from lantai.storage.cas import atomic_update, cas_update
from lantai.storage.fts import sync_fts

# ==================== 票 02（v0.66）：CJK 语义判定基础件 ====================
#
# 「probe reply / resolve」的判据不能是裸子串包含。中文词间无空格，`re.search`
# 的「包含」等于把「是不是」读成「不是」、把「没有问题」读成「没有」、把
# 「还是改成…」读成「还是（留旧）」——前两条在实测里真的归档掉了一条正确记忆
# （分支 B：status=archived / confidence=0.1）并废弃掉未决冲突（分支 A：dismissed）。
# 判定因此拆成三层：**小句边界 → 疑问守卫 → 带守卫的短语**，含糊一律不写库。
#
# 全角标点一律用 Unicode 转义写进字符类（工单修复目标 2）：字面全角字符在源码
# 格式化中最容易被重排或吞掉，边界类少一个标点，「「对」」「对；」「对——」这类
# 常见肯定句就整体不识别。字符类里的标点是唯一需要转义的地方，词本身保持字面。
_PUNCT_FW = (
    "\u3002\uff0c\uff1b\uff1a\uff01\uff1f\u3001\uff5e"  # 。，；：！？、～
    "\uff08\uff09\u300c\u300d\u300e\u300f"  # （）「」『』
    "\u201c\u201d\u2018\u2019\u2014\u2026"  # “”‘’—…
)
_PUNCT_ASCII = r"\s,.!?;:"
_EDGE_CLASS = "[" + _PUNCT_FW + _PUNCT_ASCII + "]"
_EDGE_STRIP_RE = re.compile("^" + _EDGE_CLASS + "+|" + _EDGE_CLASS + "+$")
_CLAUSE_SPLIT_RE = re.compile(_EDGE_CLASS)
# 肯定词的后接边界：标点 + 确认语气词（口径同改动前，标点扩到全角转义类）。
_AFFIRM_BOUNDARY_CLASS = "[" + _PUNCT_FW + r"\s,.!?~" + "的啊呀吧呢]"
# 改变动词：「还是」后面跟着它们就不是「留旧」。
_CHANGE_VERB = "改成|改为|换成|改用|变成|换用|改动|改"
# 票 44：前瞻内惰性扫必须**有界**——无界 `[^标点\s]*?` 在「还是」长流 +
# 改动词压末尾的病理输入下每起点扫全段（O(n²)，40K 实测 7.4s、翻倍 ×4.0），
# 探颐每次回执消解必经 + 入口零上界 = 单请求卡死 threadpool 一线程。
# {0,16} 窗：「还是」与改动词的 realistic 间距远小于 16 字（CJK 小句以标点
# 收束）；窗外语主的改动词按「留旧」处理（宁 miss 不脏写口径，误判方向是
# 留旧不写库，不是误写）。每起点 O(16) → 全局线性。
_HAISHI_VERB_WINDOW = 16
_HAISHI_KEEP_OLD = (
    "还是(?![^" + _PUNCT_FW + r"\s]{0," + str(_HAISHI_VERB_WINDOW) + "}?(?:" + _CHANGE_VERB + "))"
)

# 正反问 / 是非问：用户把问题丢回来，这不是答复，任何分支都不得据此写库。
_INTERROGATIVE_PATTERNS = ["是不是", "有没有", "要不要", "能不能", "是否", "难道"]
_MOOD_QUESTION_RE = re.compile("吗" + _EDGE_CLASS + "*$")  # 句末「吗」


def _clauses(reply: str) -> list[str]:
    """按标点切小句：CJK 词与词之间唯一可靠的边界就是标点。"""
    return [c for c in _CLAUSE_SPLIT_RE.split(reply) if c]


def _strip_edges(reply: str) -> str:
    return _EDGE_STRIP_RE.sub("", reply)


def _is_interrogative(reply: str) -> bool:
    """反问/疑问判定：句尾问号、句末「吗」、或正反问格式（是不是/有没有/…）。"""
    body = _strip_edges(reply)
    if body.endswith("\uff1f") or body.endswith("?"):
        return True
    if _MOOD_QUESTION_RE.search(reply):
        return True
    return any(re.search(pat, reply, re.IGNORECASE) for pat in _INTERROGATIVE_PATTERNS)


# 肯定与否定词库模式
_AFFIRMATIVE_PATTERNS = [
    r"是",
    r"对",
    r"没错",
    r"确实",
    r"好的",
    r"更改了",
    r"已变更",
    r"变了",
    r"成了",
    r"改用",
    r"换成",
    r"改了",
    r"改好了",
    r"确认",
    r"确有此事",
    r"属实",
    r"准确",
    r"正确",
    r"是对的",
    r"yes",
    r"yep",
    r"yeah",
    r"correct",
    r"right",
    r"true",
]
_NEGATIVE_PATTERNS = [
    # 分支 A 问「新说法要不要替换旧的」。**每条都带边界守卫，禁止裸子串**（票 02
    # 病灶：裸 `不是` / `没有` / `还是` 分别把疑问句、应允话、采纳新说法读成否定，
    # 实测把未决冲突直接 dismissed 掉）。
    r"(?<!是)不是",  # 「是不是…」是正反问，不是否定（此前只有 B/C 有这条守卫）
    r"没有(?!问题)",  # 「没有问题」是应允，不是否定（工单证据 3）
    r"不对",
    r"写错",
    r"搞错",
    r"弄错",
    r"错了",
    r"并非",
    r"不成立",
    r"不属实",
    r"不准确",
    r"别(?:改|记|录|用|加|这样|这么|把)",
    r"勿(?:改|用|记|录|采|把)",
    r"不可(?:采信|信|当真|算|作为)",
    # 分支 A 问「要不要替换」，「禁止改这条」= 留旧；B/C 的否定组不含「改」。
    r"禁止(?:改|将|把|记|录|采|用|当|作)",
    r"未(?:变|采纳|使用|曾|记录)",  # 「未确认」是未决不是否认（票 02）
    _HAISHI_KEEP_OLD,  # 「还是」= 留旧；但「还是改成…」恰恰是采纳新说法
    r"没变",
    r"依然",
    r"保持",
    # CJK 否定词：整词语义、词间无子串误伤面（「没错」不含「没有」），保持裸匹配
    # 裸「假」会命中「放假/假期/请假」并把记忆归档（续审 2026-10-31 实测），收窄为「假的」
    r"假的",
    # 英文否定词：**整词匹配（\b 边界）**（票 `.scratch/v0.64-next-window-2026-11/09`
    # R-01）——裸子串 `no` 曾把 'noted, 已记录' / 'known issue' 误判为否定，
    # 探颐否定分支直接归档记忆。与肯定侧 `_is_affirmative_reply` 的锚定+
    # 边界口径对齐。
    r"\bno\b",
    r"\bnope\b",
    r"\bfalse\b",
    r"\bwrong\b",
]


def detect_memory_probes(
    query: str | None = None,
    session_id: str | None = None,
    session: Session | None = None,
    principal=None,
    *,
    db_session: Session | None = None,
    memory_ids: list[str] | None = None,
) -> list[dict]:
    """扫描未决冲突账本与模糊置信度事实，生成主动求证探针（ADR-0037 探颐）。

    归属收窄（票 .scratch/readside-gaps/07 修法口径 4）：此前
    `select(ConflictEvent).where(status=="open")` 全表捞，把别人的
    `existing_content` + `incoming_ref` **两份全文**吐出来，还渲染成
    "顺便向您求证确认下：关于「…」"的问句——等于把 B 的记忆原文
    包装成给 A 的提问。

    `ConflictEvent` 没有归属列，判据过渡推导：`conf.memory_id → MemoryItem.user_id`。
    口径同 `resolve_probe_response`：admin/system 全权；非 admin 只见自己
    记忆上的冲突。NULL 属主的老行**不可见**（宁可漏探，不可错探——探针
    会把正文渲染成问句送给用户，那是比列表更直接的泄漏面）。

    `principal=None`（`cli/mcp.py` 的 `handle_probe_detect`）不加过滤，
    与改动前逐字一致。
    """
    if not settings.ENABLE_PROACTIVE_PROBING:
        return []

    active_session = db_session if db_session is not None else session

    clean_q = (query or "").strip()
    if not clean_q and not memory_ids:
        return []

    tokens = (
        set(w.strip() for w in jieba.lcut(clean_q) if len(w.strip()) >= 2) if clean_q else set()
    )

    def _visible(s: Session, conf: ConflictEvent) -> bool:
        """冲突对当前 principal 是否可见（过渡到挂载记忆的属主）。"""
        if principal is None:
            return True
        if bool(getattr(principal, "is_admin", False)):
            return True
        item = s.get(MemoryItem, conf.memory_id)
        if item is None:
            # 挂载记忆已不存在：账本本身无归属可判，不泄露任何正文
            return False
        if item.status != "active":
            # X-01（P0-A）：挂载记忆已软删除（归档/拒绝）——探针会把正文包装成
            # 问句直接送给用户，比列表更直接的泄漏面；已删隐私不得再被求证。
            return False
        from lantai.services.work_item_service import _viewer_of

        return bool(item.user_id) and item.user_id == _viewer_of(principal)

    def _detect(s: Session) -> list[dict]:
        open_conflicts = s.exec(select(ConflictEvent).where(ConflictEvent.status == "open")).all()

        probes = []
        for conf in open_conflicts:
            if not _visible(s, conf):
                continue
            item = s.get(MemoryItem, conf.memory_id)
            existing_content = item.content if item else ""
            incoming_ref = conf.incoming_ref or ""
            # 獬豸（巡检 2026-11-06）：incoming_ref 是候选摘要（RSS/arxiv 等摄取不过闸），
            # 这里会被渲染成问句注入 Agent 上下文——命中注入载荷的冲突不出探针
            if is_blocked_by_injection_guard(incoming_ref):
                continue

            # 判断当前查询是否涉及冲突的主题词，或指定了 memory_ids
            all_text = f"{existing_content} {incoming_ref}"
            text_tokens = set(w.strip() for w in jieba.lcut(all_text) if len(w.strip()) >= 2)

            match_query = (not tokens) or bool(tokens & text_tokens)
            match_memory_id = bool(memory_ids and conf.memory_id in memory_ids)

            if match_query or match_memory_id:
                question = (
                    f"顺便向您求证确认下：关于「{existing_content[:30]}」，"
                    f"目前是否已变更为「{incoming_ref[:30]}」？"
                )
                probes.append(
                    {
                        "probe_id": f"probe_{conf.id}",
                        "conflict_id": conf.id,
                        "memory_id": conf.memory_id,
                        "existing_content": existing_content,
                        "incoming_ref": incoming_ref,
                        "question": question,
                        "suggested_action": "resolve_conflict",
                    }
                )

        # 2. 扫描临界模糊带事实/记忆（0.3 <= confidence <= 0.6）
        # 归属过滤原则：非 admin 只探查自身属主记录
        viewer = None
        if principal is not None and not bool(getattr(principal, "is_admin", False)):
            from lantai.services.work_item_service import _viewer_of

            viewer = _viewer_of(principal)

        # 已经生成过冲突解决探针的 memory_id 集合（优先冲突解决，不产生同条重复探针）
        conflicted_mem_ids = {conf.memory_id for conf in open_conflicts if conf.status == "open"}

        # 2a. 临界模糊记忆项（MemoryItem）
        mem_query = (
            select(MemoryItem)
            .where(MemoryItem.status == "active")
            .where(MemoryItem.confidence >= 0.3)
            .where(MemoryItem.confidence <= 0.6)
        )
        if viewer:
            mem_query = mem_query.where(MemoryItem.user_id == viewer)
        fuzzy_memories = s.exec(mem_query).all()

        for m in fuzzy_memories:
            if m.id in conflicted_mem_ids:
                continue
            m_text = m.content or ""
            m_tokens = set(w.strip() for w in jieba.lcut(m_text) if len(w.strip()) >= 2)
            match_q = (not tokens) or bool(tokens & m_tokens)
            match_mid = bool(memory_ids and m.id in memory_ids)

            if match_q or match_mid:
                question = f"顺便向您求证确认下：关于「{m_text[:30]}」，该事实是否准确？"
                probes.append(
                    {
                        "probe_id": f"probe_{m.id}",
                        "conflict_id": None,
                        "memory_id": m.id,
                        "existing_content": m_text,
                        "incoming_ref": "",
                        "question": question,
                        "suggested_action": "clarify_fact",
                    }
                )

        # 2b. 临界模糊原子事实（FactAtomic）
        # X-01（P0-A）：模糊记忆项一条 `status == "active"` 就挡住了软删除回流，
        # 事实这一路此前裸查 fact_atomic——已归档记忆的隐私照样被「顺便向您求证
        # 确认下：关于「…」」原样复述出来。JOIN 回来源判存活，口径同完璧。
        fact_query = (
            select(FactAtomic)
            .outerjoin(MemoryItem, col(FactAtomic.source_memory_id) == col(MemoryItem.id))
            .where(fact_source_alive_clause())
            .where(FactAtomic.confidence >= 0.3)
            .where(FactAtomic.confidence <= 0.6)
        )
        if viewer:
            fact_query = fact_query.where(FactAtomic.user_id == viewer)
        fuzzy_facts = s.exec(fact_query).all()

        for f in fuzzy_facts:
            if f.source_memory_id and f.source_memory_id in conflicted_mem_ids:
                continue
            f_text = f.statement or ""
            f_tokens = set(w.strip() for w in jieba.lcut(f_text) if len(w.strip()) >= 2)
            match_q = (not tokens) or bool(tokens & f_tokens)
            match_mid = bool(memory_ids and f.source_memory_id in memory_ids)

            if match_q or match_mid:
                question = f"顺便向您求证确认下：关于「{f_text[:30]}」，该事实是否属实？"
                probes.append(
                    {
                        "probe_id": f"probe_{f.id}",
                        "conflict_id": None,
                        "memory_id": f.source_memory_id,
                        "existing_content": f_text,
                        "incoming_ref": "",
                        "question": question,
                        "suggested_action": "clarify_fact",
                    }
                )

        # 票 50：clean_q 只 strip 边缘、内部换行存活，20 字窗也够伪造行——过 loggable
        logger.info(
            "探颐：针对查询 '%s' 检出 %d 个主动探针", loggable(clean_q, limit=20), len(probes)
        )
        return probes

    if active_session is not None:
        return _detect(active_session)
    with db.get_session() as s:
        return _detect(s)


def format_probing_context(probes: list[dict]) -> str:
    """将探针列表格式化为可注入 Prompt 的【探颐·待求证事项】上下文块。"""
    if not probes:
        return ""
    lines = ["【探颐·待求证事项】（若当前对话语境适宜，建议在回复末尾顺带向用户确认下述事实）："]
    for p in probes:
        item_id = p.get("conflict_id") or p.get("probe_id")
        lines.append(f"- 探针提示 [ID: {item_id}]: {p['question']}")
    return "\n".join(lines)


# 模糊记忆/原子事实求证（分支 B/C）专用口径（续审 2026-10-31 P0-2）。
# 分支 A 问的是「新说法要不要替换旧的」，「还是/依然/没变/保持」是「保留旧的」=否定新说法；
# 分支 B/C 问的是「这条记忆还对不对」，同样的词恰恰是**确认**。共用 _NEGATIVE_PATTERNS
# 会把「对，没变」「是的，还是喝乌龙茶」直接归档掉一条正确记忆。
_CLAIM_NEGATIVE_PATTERNS = [
    r"(?<!是)不是",  # 「是不是」是疑问不是否定
    r"不对",
    r"写错",
    r"搞错",
    r"弄错",
    r"错了",
    r"并非",
    r"不成立",
    r"不属实",
    r"不准确",
    r"假的",
    # 票 02：「并非/不成立/请勿记录/别记/不可采信/禁止当作/未采纳」都是**明确**
    # 否定，此前全部落到 unclear 而永远挂审；按短语边界承认，不用裸单字
    # （裸「别」会命中「别的」、裸「未」会命中「未来/未必」）。
    # 票 02：B/C 只承认「不要这条记忆」，不承认「不要改动这条记忆」——
    # 「别改/勿改/未确认」在「这条还对吗」的问句下是**留旧/未决**，
    # 据此归档就是把正确记忆删掉，全部落到 unclear。
    r"别(?:记|录|用|把|这样|这么)",
    r"勿(?:记|录|采|用|把)",
    r"不可(?:采信|信|当真|算|作为)",
    r"禁止(?:将|把|记|录|采|用|当|作)",
    r"未(?:采纳|使用|曾|记录)",  # 不含「未变/未确认」：前者是确认口径，后者是未决
    r"\bno\b",
    r"\bnope\b",
    r"\bfalse\b",
    r"\bwrong\b",
]

_UNCHANGED_PATTERNS = [
    r"没变",
    r"未变",
    r"没有变",
    _HAISHI_KEEP_OLD,  # 「还是喝乌龙茶」= 确认；「还是改成…」不是
    r"依然",
    r"保持",
    r"一直",
]
# 含糊/转折：任何分支都不自动改写或归档，留待审（宁可多问一次，不可错删一条）。
_HEDGE_PATTERNS = [
    r"但",
    r"不过",
    r"还没(?:决定|想好|定)",
    r"说不好",
    r"不确定",
    r"不清楚",
    r"再说",
    r"也许",
    r"可能",
    r"大概",
    r"是吧",
]


def _matches_any(patterns: list[str], text: str) -> bool:
    return any(re.search(pat, text, re.IGNORECASE) for pat in patterns)


def _is_noncommittal(reply: str) -> bool:
    """含糊（转折/未决）或反问：任何分支都不自动改写、不归档，留待审。"""
    return _matches_any(_HEDGE_PATTERNS, reply) or _is_interrogative(reply)


def _classify_claim_reply(reply: str) -> str:
    """分支 B/C：返回 'negative' / 'affirmative' / 'unclear'。两边都沾或含糊一律 unclear。"""
    if _is_noncommittal(reply):
        return "unclear"
    neg = _matches_any(_CLAIM_NEGATIVE_PATTERNS, reply)
    aff = _is_affirmative_reply(reply) or _matches_any(_UNCHANGED_PATTERNS, reply)
    if neg and not aff:
        return "negative"
    if aff and not neg:
        return "affirmative"
    return "unclear"


def _is_affirmative_reply(reply: str) -> bool:
    """小句开头的确认词才算肯定：其后接结束、标点或「的啊呀吧呢」等语气，而非另一个词。

    票 02：CJK 无空格，只判整句开头会把「嗯，对的」「「对」」这类答复整体漏判，
    而全角引号/分号一旦从边界类里被格式化吞掉，常见肯定句就识别不出来。改为
    **逐小句开头**判定，边界标点统一走 Unicode 转义类（见 `_PUNCT_FW`）。
    """
    clauses = _clauses(reply) or [_strip_edges(reply)]
    for clause in clauses:
        for pat in _AFFIRMATIVE_PATTERNS:
            if re.match(rf"(?:{pat})(?:$|{_AFFIRM_BOUNDARY_CLASS})", clause, re.IGNORECASE):
                return True
    return False


def resolve_probe_response(
    conflict_id: str | None = None,
    user_reply: str = "",
    session: Session | None = None,
    principal=None,
    *,
    probe_id: str | None = None,
) -> dict:
    """分析用户对探针的自然语言答复，自动执行冲突消解与版本更替。

    支持 conflict_id 与 probe_id（如 probe_conf_xxx、probe_mem_xxx、probe_fact_xxx）兼容解析。
    归属校验（票 .scratch/readside-gaps/07 修法口径 4）：**全轮最严重的一条**。
    肯定分支会 `item.content = conf.incoming_ref`——写的是正文，而此前一个
    身份都不取，A 用自己的 key 就能把 B 的记忆原文覆盖掉（`OBSERVED3.txt`
    实测：`mem-user-B.content` 被改成 A 传的 `incoming_ref`）。

    `ConflictEvent` **没有归属列**（`tables.py:371-383`），判据只能过渡推导：
    `conf.memory_id → MemoryItem.user_id`。这比加列更准——冲突账本的存在意义
    就是挂在某条记忆上，记忆换属主，冲突的可见性跟着换。

    按**破坏性写操作**处理，复用 `acl.ensure_can_delete` 单一真源（同票 02
    口径）：admin/system 全权；非 admin 只能消解自己记忆上的冲突。
    403 必须发生在任何 `s.commit()` 之前——不允许"拒绝了但已经落库"。

    `principal=None`（`cli/mcp.py` 的 `handle_probe_resolve` 没有调用方身份）
    不加校验，与改动前逐字一致；内部入口与 REST 暴露面分开。
    """
    clean_reply = user_reply.strip()
    if not clean_reply:
        return {"status": "error", "message": "user_reply 不能为空"}

    # 参数解析：兼顾 conflict_id 与 probe_id
    target_id = conflict_id or probe_id or ""
    if not target_id:
        return {"status": "error", "message": "conflict_id 或 probe_id 不能为空"}

    actual_conflict_id = target_id
    if (
        target_id.startswith("probe_conf_")
        or target_id.startswith("probe_")
        and not target_id.startswith("probe_mem_")
        and not target_id.startswith("probe_fact_")
    ):
        actual_conflict_id = target_id[len("probe_") :]

    def _resolve(s: Session) -> dict:
        # 分支 A：针对未决冲突（ConflictEvent）消解
        conf = s.get(ConflictEvent, actual_conflict_id)
        if conf:
            if conf.status != "open":
                return {"status": "already_resolved", "conflict_status": conf.status}

            item = s.get(MemoryItem, conf.memory_id)
            if not item:
                return {"status": "error", "message": f"MemoryItem {conf.memory_id} 不存在"}

            # 归属校验：在任何写操作之前（403 不能伴随落库）
            if principal is not None:
                from lantai.core.acl import ensure_can_delete

                ensure_can_delete(
                    principal,
                    resource_user_id=item.user_id,
                    resource_tenant_id=item.tenant_id,
                    lane=item.lane,
                )

            # 1. 优先匹配否定词（否定意图优先）
            hedged = _is_noncommittal(clean_reply)  # 含糊或反问一律不写库（票 02）
            is_neg = not hedged and _matches_any(_NEGATIVE_PATTERNS, clean_reply)
            is_aff = not hedged and _is_affirmative_reply(clean_reply)

            if is_neg:
                conf.status = "dismissed"
                conf.resolved_by = "proactive_probe_user_rejected"
                conf.resolved_at = utcnow()
                s.add(conf)
                s.commit()
                logger.info("探颐：用户否定探针，已废弃冲突 %s", actual_conflict_id)
                return {
                    "status": "resolved",
                    "action": "dismissed",
                    "conflict_id": actual_conflict_id,
                }

            elif is_aff:
                # 獬豸（巡检 2026-11-06）：肯定分支把 incoming_ref 写进正文，与
                # correct_memory / 终端编辑同族同闸。命中整条拒：不改正文、
                # 不动冲突账本、不 commit（拒绝了不落库）
                try:
                    assert_no_prompt_injection(conf.incoming_ref or "")
                except ValueError as e:
                    logger.warning("探颐：冲突 %s 的新说法未过獬豸闸，拒绝写入", actual_conflict_id)
                    return {
                        "status": "rejected",
                        "action": "guard_rejected",
                        "conflict_id": actual_conflict_id,
                        "memory_id": item.id,
                        "message": str(e),
                    }

                # 记录 Checkpoint 快照
                before_snapshot = item.model_dump(mode="json")
                # 票 78：版本前置 CAS（同 tanyi.py 修复）——并发 correct_memory
                # 交错时按扫描期快照整组写回会覆盖用户显式纠正、version 重号
                # （票 23 S8 同级）。rowcount=0 即并发败方：冲突保留 open 如实
                # 报 conflict（宁 miss 不脏写，交人工重探）。
                scan_version = item.version or 1
                claimed = atomic_update(
                    s,
                    MemoryItem,
                    item.id,
                    {
                        "content": conf.incoming_ref,
                        "version": scan_version + 1,
                        "decay_score": 1.0,
                        "updated_at": utcnow(),
                    },
                    MemoryItem.version == scan_version,
                    MemoryItem.status == "active",
                )
                if not claimed:
                    s.rollback()
                    logger.info(
                        "探颐：冲突 %s 的目标记忆已被并发修改，消解让路交人工重探",
                        actual_conflict_id,
                    )
                    return {
                        "status": "conflict",
                        "action": "concurrent_modification",
                        "conflict_id": actual_conflict_id,
                        "memory_id": item.id,
                        "message": "memory changed concurrently; conflict kept open for re-probe",
                    }
                # CAS 胜方：ORM 对象与库端对齐（checkpoint/冲突翻转都要用新值）
                s.refresh(item)

                cp = MemoryCheckpoint(
                    id=f"cp_{ULID()}",
                    memory_id=item.id,
                    version=item.version,
                    before=before_snapshot,
                    after=item.model_dump(mode="json"),
                    trigger="proactive_probe_resolved",
                    created_at=utcnow(),
                )
                s.add(cp)

                # 票 78：conf 翻转同走条件认领（票 22 S6 conflict_service 蓝图）——
                # 记忆 CAS 与 conf CAS 之间人工裁决已把冲突裁成终态时，败方不
                # 覆写终态；回滚整笔（含刚写的正文），冲突终态权威保留。
                conf_claimed = cas_update(
                    s,
                    ConflictEvent,
                    conf.id,
                    {"status": "open"},
                    {
                        "status": "resolved",
                        "resolved_by": "proactive_probe_user_confirmed",
                        "resolved_at": utcnow(),
                    },
                )
                if not conf_claimed:
                    s.rollback()
                    logger.info(
                        "探颐：冲突 %s 已被并发裁决成终态，消解让路（正文写入一并回滚）",
                        actual_conflict_id,
                    )
                    return {
                        "status": "conflict",
                        "action": "concurrent_adjudication",
                        "conflict_id": actual_conflict_id,
                        "memory_id": item.id,
                        "message": "conflict adjudicated concurrently; memory write rolled back",
                    }
                s.add(item)

                # FTS 同事务强一致（ADR-0008，同 correct_memory / promoter update 口径）
                sync_fts(s, item.id, item.content)

                s.commit()

                # 向量库独立于 SQL 事务：对替换后的新正文重建
                vector_synced = sync_vector_upsert(item.id, item)
                warnings: list[str] = []
                if not vector_synced:
                    warnings.append("vector resync failed; content updated in SQL/FTS only")
                    logger.warning(
                        "探颐：消解后向量重同步失败（正文与 FTS 已更新）memory=%s", item.id
                    )

                logger.info("探颐：用户肯定探针，已成功消解冲突并更新记忆 %s", item.id)
                return {
                    "status": "resolved",
                    "action": "applied",
                    "conflict_id": actual_conflict_id,
                    "memory_id": item.id,
                    "vector_synced": vector_synced,
                    "warnings": warnings,
                }

            else:
                logger.info("探颐：用户答复未明确表态，保持冲突待审状态")
                return {
                    "status": "deferred",
                    "action": "pending",
                    "conflict_id": actual_conflict_id,
                }

        # 分支 B：针对模糊记忆项澄清（probe_mem_xxx 或 MemoryItem）
        mem_target_id = target_id
        if mem_target_id.startswith("probe_"):
            mem_target_id = mem_target_id[len("probe_") :]
        target_mem = s.get(MemoryItem, mem_target_id)
        if target_mem:
            if principal is not None:
                from lantai.core.acl import ensure_can_delete

                ensure_can_delete(
                    principal,
                    resource_user_id=target_mem.user_id,
                    resource_tenant_id=target_mem.tenant_id,
                    lane=target_mem.lane,
                )
            verdict = _classify_claim_reply(clean_reply)
            is_neg = verdict == "negative"
            is_aff = verdict == "affirmative"

            if is_neg:
                # 否定：降低置信度或标记为废弃（0.15 跌破底线）
                target_mem.confidence = 0.1
                target_mem.status = "archived"
                s.add(target_mem)
                s.commit()
                return {"status": "resolved", "action": "archived", "memory_id": target_mem.id}
            elif is_aff:
                # 肯定：确信该事实，提振置信度至 0.95
                target_mem.confidence = 0.95
                s.add(target_mem)
                s.commit()
                return {"status": "resolved", "action": "confirmed", "memory_id": target_mem.id}
            else:
                return {"status": "deferred", "action": "pending", "memory_id": target_mem.id}

        # 分支 C：针对模糊原子事实澄清（probe_fact_xxx 或 FactAtomic）
        fact_target_id = target_id
        if fact_target_id.startswith("probe_"):
            fact_target_id = fact_target_id[len("probe_") :]
        target_fact = s.get(FactAtomic, fact_target_id)
        if target_fact:
            if principal is not None:
                from lantai.core.acl import ensure_can_delete

                ensure_can_delete(
                    principal,
                    resource_user_id=target_fact.user_id,
                    resource_tenant_id=target_fact.tenant_id,
                )
            verdict = _classify_claim_reply(clean_reply)
            is_neg = verdict == "negative"
            is_aff = verdict == "affirmative"

            if is_neg:
                # 软撤回原子事实
                target_fact.confidence = 0.0
                target_fact.valid_to = iso_utc(utcnow())
                s.add(target_fact)
                s.commit()
                return {"status": "resolved", "action": "retracted", "fact_id": target_fact.id}
            elif is_aff:
                target_fact.confidence = 0.95
                s.add(target_fact)
                s.commit()
                return {"status": "resolved", "action": "confirmed", "fact_id": target_fact.id}
            else:
                return {"status": "deferred", "action": "pending", "fact_id": target_fact.id}

        return {"status": "not_found", "message": f"未找到可消解的冲突或事实对象: {target_id}"}

    if session is not None:
        return _resolve(session)
    with db.get_session() as s:
        return _resolve(s)
