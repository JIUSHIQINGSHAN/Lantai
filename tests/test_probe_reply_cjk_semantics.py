"""探颐中文答复语义回归（续审 2026-10-31 P0-2）。

修复前实测：分支 B（模糊记忆求证）对「对，没变」「是的，还是喝乌龙茶」「我下周放假再说」
全部判否定并归档记忆；分支 A 对「是的，但其实我还没决定」直接用新说法覆盖旧正文。
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.models.tables  # noqa: F401
from lantai.models.tables import ConflictEvent, FactAtomic, MemoryItem
from lantai.services import probing_service
from lantai.services.probing_service import resolve_probe_response
from lantai.storage.fts import init_fts


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    with e.connect() as c:
        init_fts(c.connection.driver_connection)
    return e


def _probe_memory(engine, reply: str):
    with Session(engine) as s:
        s.add(MemoryItem(id="m1", content="用户偏好乌龙茶", status="active", confidence=0.5))
        s.commit()
        r = resolve_probe_response(probe_id="probe_m1", user_reply=reply, session=s)
        m = s.get(MemoryItem, "m1")
        return r["action"], m.status, m.content


def _probe_conflict(engine, reply: str):
    with Session(engine) as s:
        s.add(MemoryItem(id="m2", content="用户偏好乌龙茶", status="active"))
        s.add(
            ConflictEvent(
                id="c1",
                memory_id="m2",
                incoming_ref="用户偏好改成龙井",
                rule_name="x",
                status="open",
            )
        )
        s.commit()
        r = resolve_probe_response("c1", reply, session=s)
        return r["action"], s.get(MemoryItem, "m2").content


@pytest.mark.parametrize(
    "reply",
    ["对，没变", "是的，还是喝乌龙茶", "对，一直保持这个习惯", "没有变，依然是这样"],
)
def test_branch_b_unchanged_is_confirmation(engine, reply):
    action, status, _ = _probe_memory(engine, reply)
    assert action == "confirmed"
    assert status == "active"


@pytest.mark.parametrize(
    "reply", ["嗯，我下周放假再说", "没有问题，是这样", "是不是假期改了我也不清楚"]
)
def test_branch_b_unclear_never_archives(engine, reply):
    action, status, _ = _probe_memory(engine, reply)
    assert action == "pending"
    assert status == "active"


@pytest.mark.parametrize("reply", ["不对，我早就不喝了", "写错了", "no, that's wrong"])
def test_branch_b_real_negation_still_archives(engine, reply):
    action, status, _ = _probe_memory(engine, reply)
    assert action == "archived"
    assert status == "archived"


@pytest.mark.parametrize("reply", ["是的，但其实我还没决定", "是吧，我也说不好"])
def test_branch_a_hedge_does_not_overwrite(engine, reply):
    action, content = _probe_conflict(engine, reply)
    assert action == "pending"
    assert content == "用户偏好乌龙茶"


def test_branch_a_clear_yes_still_applies(engine):
    action, content = _probe_conflict(engine, "对，改成龙井了")
    assert action == "applied"
    assert content == "用户偏好改成龙井"


def test_branch_a_keep_old_still_dismisses(engine):
    action, content = _probe_conflict(engine, "不是，还是乌龙茶")
    assert action != "applied"
    assert content == "用户偏好乌龙茶"


# ==================== 票 02（v0.66）扩充：probe reply/resolve 的 CJK 语义边界 ====================
#
# 修复前实测复现（真 SQLite + 真 FTS，判定逻辑不 mock）：
#   1. 「你是不是记错了？」→ 分支 B 把**正确记忆**归档（status=archived,
#      confidence=0.1）。根因：`_CLAIM_NEGATIVE_PATTERNS` 用无边界 `re.search`，
#      `错了` 命中「记错了」；疑问句从来没被识别为「用户没有在回答」。
#   2. 「是不是还在用 VSCode？」「有没有可能你搞错了？」→ 分支 A dismiss 掉未决
#      冲突（新说法被丢弃）。根因：分支 A 用裸 `不是`/`搞错`，既没有分支 B/C 的
#      `(?<!是)` 守卫，也没有疑问句守卫——同一病灶在两处口径不一致。
#   3. 「没有问题」→ 分支 A 命中裸 `没有` 子串 → dismissed（工单证据 3）。
#   4. 「还是改成绿茶吧」→ 分支 A 命中裸 `还是` 被判成「留旧」→ dismissed；
#      分支 B 命中 `_UNCHANGED_PATTERNS` 被判成「确认」→ confidence 0.95。
#      同一个粘连短语在两个方向上都错。
#   5. 「「对」」「对；」「对——」「正确」「嗯，对的」→ 肯定句不识别（工单修复
#      目标 2：全角标点边界类缺失，字面全角字符被源码格式化破坏）。
#   6. 「请勿记录这条」「不可采信这条」「禁止把它当成我的偏好」「未采纳过这个
#      说法」「这个说法不成立」→ 明确否定却落到 pending，永远挂审。
#
# 断言口径（与「宁 miss 不脏写」一致）：疑问句/粘连含糊**一律 pending** 且零写
# 操作；明确否定必须被承认；明确肯定必须被确认。


COUNTERQUESTIONS = [
    "你是不是记错了？",
    "是不是还在用 VSCode？",
    "你说的是不是这条？",
    "有没有可能你搞错了？",
    "是不是假期改了我也不清楚",
]

CLEAR_DENIALS = [
    "并非如此，我早就不喝乌龙茶了",
    "这个说法不成立",
    "请勿记录这条",
    "别记这个，我已经不用了",
    "不可采信这条",
    "禁止把它当成我的偏好",
    "未采纳过这个说法",
]

COMMON_AFFIRMATIONS = [
    "「对」",
    "对；",
    "对——",
    "正确",
    "嗯，对的",
    "确认；",
    "是的。",
    "是对的",
    "对，就是这样",
]

OLD_MEM = "用户偏好乌龙茶"
INCOMING_CLAIM = "用户偏好改成龙井"


def _seed_memory(engine, mid, content=OLD_MEM, confidence=0.5):
    with Session(engine) as s:
        s.add(MemoryItem(id=mid, content=content, status="active", confidence=confidence))
        s.commit()


def _resolve_memory(engine, mid, reply):
    """分支 B（模糊记忆求证）：返回 (res, status, confidence)。"""
    with Session(engine) as s:
        res = resolve_probe_response(probe_id=f"probe_{mid}", user_reply=reply, session=s)
        item = s.get(MemoryItem, mid)
        return res, item.status, item.confidence


def _seed_fact(engine, fid, statement="用户偏好乌龙茶", confidence=0.5):
    with Session(engine) as s:
        s.add(
            FactAtomic(
                id=fid,
                statement=statement,
                predicate="喜欢",
                object="乌龙茶",
                confidence=confidence,
                user_id="u1",
            )
        )
        s.commit()


def _resolve_fact(engine, fid, reply):
    """分支 C（原子事实澄清）：返回 (res, confidence, valid_to)。"""
    with Session(engine) as s:
        res = resolve_probe_response(probe_id=f"probe_{fid}", user_reply=reply, session=s)
        fact = s.get(FactAtomic, fid)
        return res, fact.confidence, fact.valid_to


def _seed_conflict(engine, cid, mid, content=OLD_MEM, incoming=INCOMING_CLAIM, status="open"):
    with Session(engine) as s:
        s.add(MemoryItem(id=mid, content=content, status="active"))
        s.add(
            ConflictEvent(
                id=cid, memory_id=mid, incoming_ref=incoming, rule_name="x", status=status
            )
        )
        s.commit()


def _read_conflict(engine, cid, mid):
    """读回落库状态：(conflict_status, memory_content, memory_status)。"""
    with Session(engine) as s:
        conf = s.get(ConflictEvent, cid)
        item = s.get(MemoryItem, mid)
        return conf.status, item.content, item.status


def _resolve_conflict(engine, cid, reply):
    """分支 A（冲突消解）：返回 (action, conflict_status, memory_content)。"""
    with Session(engine) as s:
        res = resolve_probe_response(cid, reply, session=s)
    conflict_status, content, _ = _read_conflict(engine, cid, "ma1")
    return res["action"], conflict_status, content


class TestCounterQuestionMustNeverTriggerWrites:
    """反问句不是答复：任何分支都不得据此归档 / 撤回 / 废弃。"""

    @pytest.mark.parametrize("reply", COUNTERQUESTIONS)
    def test_branch_b_question_is_deferred_not_archived(self, engine, reply):
        _seed_memory(engine, "mq1")
        res, status, confidence = _resolve_memory(engine, "mq1", reply)
        assert res["action"] == "pending", f"反问句被判成 {res['action']}: {reply}"
        assert status == "active", f"反问句把正确记忆归档了: {reply}"
        assert confidence == 0.5, f"反问句改写了置信度: {reply}"

    @pytest.mark.parametrize("reply", ["你是不是搞错了？", "是不是记错了？", "有没有搞错？"])
    def test_branch_c_question_is_deferred_not_retracted(self, engine, reply):
        _seed_fact(engine, "fact_q1")
        res, confidence, valid_to = _resolve_fact(engine, "fact_q1", reply)
        assert res["action"] == "pending", f"反问句撤回了原子事实: {reply}"
        assert confidence == 0.5, f"反问句改写了事实置信度: {reply}"
        assert valid_to is None, f"反问句给事实盖上失效时间: {reply}"

    @pytest.mark.parametrize("reply", COUNTERQUESTIONS)
    def test_branch_a_question_keeps_conflict_open(self, engine, reply):
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", reply)
        assert action != "dismissed", f"反问句废弃了未决冲突: {reply} -> {action}"
        assert conflict_status == "open", f"冲突状态被改写: {reply} -> {conflict_status}"
        assert content == OLD_MEM, f"记忆正文被改写: {reply}"

    @pytest.mark.parametrize("reply", ["这个记忆不对吗？", "你不是说要问我确认吗？"])
    def test_branch_b_questions_about_the_probe_stay_pending(self, engine, reply):
        _seed_memory(engine, "mq2")
        res, status, confidence = _resolve_memory(engine, "mq2", reply)
        assert res["action"] == "pending", reply
        assert status == "active"
        assert confidence == 0.5


class TestExplicitChineseNegationsAreHonored:
    """明确否定必须被承认：落到 pending 就是「永远挂审」的活缺陷。"""

    @pytest.mark.parametrize("reply", CLEAR_DENIALS)
    def test_branch_b_denial_archives(self, engine, reply):
        _seed_memory(engine, "md1")
        res, status, confidence = _resolve_memory(engine, "md1", reply)
        assert res["action"] == "archived", f"明确否定没被承认: {reply} -> {res['action']}"
        assert status == "archived"
        assert confidence == 0.1

    @pytest.mark.parametrize("reply", CLEAR_DENIALS)
    def test_branch_c_denial_retracts(self, engine, reply):
        _seed_fact(engine, "fact_d1")
        res, confidence, valid_to = _resolve_fact(engine, "fact_d1", reply)
        assert res["action"] == "retracted", f"明确否定没撤回事实: {reply} -> {res['action']}"
        assert confidence == 0.0
        assert valid_to is not None

    @pytest.mark.parametrize(
        "reply",
        ["请勿记录这条", "别改，还是乌龙茶", "不可采信", "禁止改这条", "并非如此", "no"],
    )
    def test_branch_a_denial_dismisses(self, engine, reply):
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", reply)
        assert action == "dismissed", f"明确否定没废弃冲突: {reply} -> {action}"
        assert conflict_status == "dismissed"
        assert content == OLD_MEM, "否定分支不得改写正文"


class TestCjkBoundaryGluing:
    """无空格粘连：短语边界决定语义，裸子串一律不算判据。"""

    def test_meiyou_wenti_is_not_read_as_negative(self, engine):
        """「没有问题」是应允，命中裸「没有」曾被 dismiss（工单证据 3）。"""
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", "没有问题")
        assert action != "dismissed", "「没有问题」被裸「没有」子串误判为否定"
        assert conflict_status == "open"
        assert content == OLD_MEM

    def test_meiyou_wenti_is_not_confirmation_in_branch_b(self, engine):
        """B 分支里「没有问题，是这样」仍是含糊（既有契约，不得反向扩大成 confirmed）。"""
        _seed_memory(engine, "mb0")
        res, status, confidence = _resolve_memory(engine, "mb0", "没有问题，是这样")
        assert res["action"] == "pending"
        assert confidence == 0.5

    def test_haishi_plus_change_verb_is_not_keep_old(self, engine):
        """「还是改成绿茶吧」= 采纳新说法，不得读成「留旧」而废弃冲突。"""
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", "还是改成绿茶吧")
        assert action != "dismissed", "「还是改成…」被裸「还是」判成留旧"
        assert conflict_status == "open"
        assert content == OLD_MEM

    def test_haishi_plus_change_verb_is_not_confirmation(self, engine):
        """同一句话在 B 分支也不得被判成「记忆仍然正确」。"""
        _seed_memory(engine, "mb1")
        res, status, confidence = _resolve_memory(engine, "mb1", "还是改成绿茶吧")
        assert res["action"] == "pending", "「还是改成…」被当成确认，置信度被提振"
        assert confidence == 0.5
        assert status == "active"

    def test_haishi_keep_old_still_dismisses(self, engine):
        """既有用法不回归：「不是，还是乌龙茶」= 留旧。"""
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", "不是，还是乌龙茶")
        assert action == "dismissed"
        assert content == OLD_MEM

    @pytest.mark.parametrize("reply", ["假期再说", "下周放假了", "放年假之前先别动"])
    def test_jiaqi_does_not_archive_memory(self, engine, reply):
        """裸「假」的旧伤：「放假/假期」不得命中否定（收窄为「假的」后的锁）。"""
        _seed_memory(engine, "mb2")
        res, status, confidence = _resolve_memory(engine, "mb2", reply)
        assert status == "active", f"「放假」类词把记忆归档了: {reply}"
        assert res["action"] != "archived"


class TestCommonAffirmationsAreRecognized:
    """工单修复目标 2：全角标点边界不得破坏常见肯定句。"""

    @pytest.mark.parametrize("reply", COMMON_AFFIRMATIONS)
    def test_branch_b_affirmation_confirms(self, engine, reply):
        _seed_memory(engine, "ma0")
        res, status, confidence = _resolve_memory(engine, "ma0", reply)
        assert res["action"] == "confirmed", f"常见肯定句不被识别: {reply} -> {res['action']}"
        assert confidence == 0.95
        assert status == "active"

    @pytest.mark.parametrize("reply", ["「是的」", "确认；没问题", "对。"])
    def test_branch_a_affirmation_applies(self, engine, reply):
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", reply)
        assert action == "applied", f"明确肯定没有替换正文: {reply} -> {action}"
        assert conflict_status == "resolved"
        assert content == INCOMING_CLAIM


class TestEmptyInputsAndAbsentCandidates:
    """空答复 / 空候选 / 未知目标：只回错误，绝不落任何写操作。"""

    @pytest.mark.parametrize("reply", ["", "   ", "\u3000", "\u3000\n"])
    def test_blank_reply_errors_without_touching_memory(self, engine, reply):
        _seed_memory(engine, "me1")
        res, status, confidence = _resolve_memory(engine, "me1", reply)
        assert res["status"] == "error", repr(reply)
        assert "user_reply" in res["message"]
        assert status == "active"
        assert confidence == 0.5

    def test_missing_target_id_errors(self, engine):
        with Session(engine) as s:
            res = resolve_probe_response(
                conflict_id=None, probe_id=None, user_reply="是的", session=s
            )
        assert res["status"] == "error"

    def test_unknown_probe_id_is_not_found_and_writes_nothing(self, engine):
        _seed_memory(engine, "me2")
        with Session(engine) as s:
            r = resolve_probe_response(
                probe_id="probe_mem_does_not_exist", user_reply="是的，改了", session=s
            )
            assert r["status"] == "not_found", r
            assert s.get(MemoryItem, "me2").status == "active"
            assert s.get(MemoryItem, "me2").confidence == 0.5

    def test_conflict_without_memory_item_errors_without_dismissing(self, engine):
        """空候选：冲突挂在丢失的记忆上，只能报错，不得改冲突状态。"""
        with Session(engine) as s:
            s.add(
                ConflictEvent(
                    id="c_orphan",
                    memory_id="mem_missing",
                    incoming_ref=INCOMING_CLAIM,
                    rule_name="x",
                    status="open",
                )
            )
            s.commit()
            res = resolve_probe_response("c_orphan", "是的，改了", session=s)
            assert res["status"] == "error"
            assert s.get(ConflictEvent, "c_orphan").status == "open"

    def test_already_resolved_conflict_is_idempotent(self, engine):
        _seed_conflict(engine, "c_done", "m_done", status="dismissed")
        with Session(engine) as s:
            res = resolve_probe_response("c_done", "是的，改了", session=s)
        assert res["status"] == "already_resolved", res
        conflict_status, content, _ = _read_conflict(engine, "c_done", "m_done")
        assert conflict_status == "dismissed"
        assert content == OLD_MEM


_GUARDED_GLOBALS = (
    "_AFFIRMATIVE_PATTERNS",
    "_NEGATIVE_PATTERNS",
    "_CLAIM_NEGATIVE_PATTERNS",
    "_UNCHANGED_PATTERNS",
    "_HEDGE_PATTERNS",
)

_ORDER_MATRIX = [
    "你是不是记错了？",
    "没有问题",
    "还是改成绿茶吧",
    "对，没变",
    "并非如此",
    "嗯，我下周放假再说",
    "「对」",
    "no",
    "noted, 已记录",
    "禁止把它当成我的偏好",
]


def _global_snapshot():
    return {name: list(getattr(probing_service, name)) for name in _GUARDED_GLOBALS}


def _global_identities():
    return {name: id(getattr(probing_service, name)) for name in _GUARDED_GLOBALS}


class TestNoGlobalMutationAndOrderIndependence:
    """票 01 同类约束落到本工单入口：消解路径不得改写生产模块全局，判定不得依赖顺序。"""

    def test_resolve_does_not_mutate_production_module_globals(self, engine):
        contents_before = _global_snapshot()
        identities_before = _global_identities()
        get_session_before = probing_service.db.get_session

        for i, reply in enumerate(_ORDER_MATRIX + COUNTERQUESTIONS + CLEAR_DENIALS):
            mid = f"mg{i}"
            _seed_memory(engine, mid)
            _resolve_memory(engine, mid, reply)

        assert _global_snapshot() == contents_before, "消解路径改写了否定/肯定词库（全局污染）"
        assert _global_identities() == identities_before, "词库被整体重新绑定"
        assert probing_service.db.get_session is get_session_before, "db.get_session 被替换未复原"

    def test_verdicts_are_independent_of_reply_order(self, engine):
        forward = {}
        for i, reply in enumerate(_ORDER_MATRIX):
            mid = f"f{i}"
            _seed_memory(engine, mid)
            forward[reply] = _resolve_memory(engine, mid, reply)[0]["action"]
        backward = {}
        for j, reply in enumerate(reversed(_ORDER_MATRIX)):
            mid = f"r{j}"
            _seed_memory(engine, mid)
            backward[reply] = _resolve_memory(engine, mid, reply)[0]["action"]
        assert forward == backward, "同一答复在不同顺序下判定不同（状态泄漏）"

    def test_classifier_is_pure_across_interleaved_calls(self):
        expected = {r: probing_service._classify_claim_reply(r) for r in _ORDER_MATRIX}
        for r in reversed(_ORDER_MATRIX):
            assert probing_service._classify_claim_reply(r) == expected[r], r


class TestKeepOldAndUndecidedAreNotDenials:
    """「别改 / 勿改 / 未确认」在「这条记忆还对吗」的问句下是**留旧或未决**，不是否定。

    票 02 判定收紧前实测到过：`勿改` 进了 B/C 否定库，于是一句「勿改」把正确
    记忆归档掉。分支 A 问的是「要不要替换」，同一个词在那里才是「留旧」= dismissed。
    """

    @pytest.mark.parametrize("reply", ["别改", "勿改", "未确认，先放着", "先别动，我还没想好"])
    def test_branch_b_undecided_never_archives(self, engine, reply):
        _seed_memory(engine, "mk1")
        res, status, confidence = _resolve_memory(engine, "mk1", reply)
        assert res["action"] != "archived", f"留旧/未决被读成否定并归档: {reply}"
        assert status == "active"
        assert confidence == 0.5

    @pytest.mark.parametrize("reply", ["别改", "勿改", "未确认这个说法"])
    def test_branch_c_undecided_never_retracts(self, engine, reply):
        _seed_fact(engine, "fact_k1")
        res, confidence, valid_to = _resolve_fact(engine, "fact_k1", reply)
        assert res["action"] != "retracted", f"留旧/未决被读成否定并撤回: {reply}"
        assert confidence == 0.5
        assert valid_to is None

    @pytest.mark.parametrize("reply", ["未确认，先放着", "还没定", "再说吧"])
    def test_branch_a_undecided_does_not_dismiss(self, engine, reply):
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", reply)
        assert action != "dismissed", f"未决答复被当成否定并关闭冲突: {reply}"
        assert conflict_status == "open"
        assert content == OLD_MEM

    @pytest.mark.parametrize("reply", ["勿改", "别改", "别记录这条"])
    def test_branch_a_dont_change_dismisses(self, engine, reply):
        """分支 A 语义（要不要替换旧说法）下，「勿改/别改」才是留旧 → dismissed。"""
        _seed_conflict(engine, "ca1", "ma1")
        action, conflict_status, content = _resolve_conflict(engine, "ca1", reply)
        assert action == "dismissed", f"{reply} 没有按留旧废弃冲突"
        assert content == OLD_MEM
