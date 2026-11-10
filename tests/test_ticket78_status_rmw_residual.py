"""票 78：MemoryItem 状态/版本快照 RMW 残留——票 23 收口后全仓巡检的票外同族面。

病灶（`.scratch/v0.68-next-window-2026-11/issues/78-memoryitem-status-rmw-residual.md`，
全部亲核 HEAD `2c88a937`）：

1. **P1 探颐消解丢用户纠正 ×2 位点**：
   - `tanyi.py:201-204`（execute_tanyi_resolution）：conf.status!=open 门与
     item 扫描（:150）之后、`item.content/version/decay_score/updated_at`
     整组 ORM 写 → :228 commit——与并发 correct_memory 交错时按扫描期快照
     整组写回，并发更正刚写的正文被探颐的旧语义正文覆盖（票 23 S8 同级：
     用户显式纠正丢失）、version 重号；
   - `probing_service.py:551-554`（resolve_probe_response 肯定分支）同型：
     `item.content = conf.incoming_ref; item.version += 1; item.decay_score = 1.0`
     整组写。修复：版本前置 CAS（`WHERE version=扫描值`，写阶段第一条 DML），
     败方如实返回 conflict 形状（探颐冲突保留 open，交人工重探）。
2. **P2 record_ops revive/unconsolidate 版本重号**：
   - `_revive_one`（record_ops_service.py:555-556，consolidated→active）；
   - unconsolidate 主记忆 retracted 路（:635-636）。
   `item.version = (item.version or 1) + 1` 裸 ORM 写。与并发 correct_memory
   交错 → 两个逻辑迁移共写同一 version 号 → checkpoint version_at 链断。
   修复：认领式条件 UPDATE（`WHERE version=扫描值 AND status=扫描值`），
   败方跳过该碎片并 warnings 如实记（revive 幂等语义天然兼容）。
3. **P2 lifecycle.py（KnowledgeLifecycleManager）**：票 23 时代已核零调用方，
   HEAD 复核仍零生产调用（grep 全仓仅 self + tests）——按票 23 对
   late_correction.py 的残留先例口径登记不修；风险单列：test_ticket62 把
   `KnowledgeLifecycleManager.supersede` 当「真实写列路径」消费，接线前
   必须先加闸（cas_update WHERE lifecycle_status=from_phase）。
4. **P3 状态覆写族**：
   - probing_service.py:521/:567（conf.status = dismissed/resolved 裁决翻转
     ——票 22 S6 已给 conflict_service.py:126-135 蓝图）；
   - probing_service.py:627（分支 B `target_mem.status = "archived"`）；
   - consolidation_service.py:450（碎片折叠 m.status="consolidated"）与
     :576（修剪 item.status="archived"）；
   - promoter.py:385/:466/:595（deprecate/merge/consolidation 分支
     status 写 + version += 1 RMW 同窗）。
   sanxing.py:439-443 票 67 已条件化（重验下推 UPDATE），非残留。
   修复：同票 23 三层机械修——状态机迁移走 cas_update 期望态等值、判定类
   写时重验判定输入；败方跳过/如实冲突。

测试形态（票 23/22 同款拍板）：跨 session 交错注入——生产竞态窗口全部开在
函数体内部，Barrier 钉不进去；注入缝选真实外部边界（utcnow / 注入闸
assert_no_prompt_injection），垫片**先调真实现、再在返回前执行 B 侧完整
真实流程**（第二 session 全真状态变更）——被测链路零 mock。全程单线程
顺序执行、无 sleep、确定性。StaticPool 单连接下缝必须在 A 首条 DML 之前
（票 23 偏差一铁律）。

替身面声明：embed/chat_json 是外部 LLM 边界（param_env autouse 替身）；
sync_vector_upsert/sync_fts 走 record_ops 既有真实路径（内存库无向量库，
upsert 失败如实进 warnings，不 mock）。DB、状态机、判定规则全链全真。
"""

from unittest.mock import patch

import pytest
from sqlmodel import select

from lantai.cognition.tanyi import execute_tanyi_resolution
from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent, MemoryCheckpoint, MemoryItem
from lantai.services.probing_service import resolve_probe_response
from lantai.services.record_ops_service import (
    correct_memory,
    revive_consolidated,
)

_P = None  # tanyi/probing 的 principal=None 是内部调用既有口径（MCP handle_probe_resolve）


def _mem(mid: str, content: str = "大哥常驻上海徐汇", **over) -> MemoryItem:
    fields = dict(
        id=mid,
        key=f"key_{mid}",
        content=content,
        memory_type="semantic",
        lane="general",
        status="active",
        user_id="u_a",
        decay_score=0.9,
        source_ids=[],
        confidence=0.8,
        version=1,
    )
    fields.update(over)
    return MemoryItem(**fields)


def _conf(cid: str, mid: str, incoming: str = "其实大哥已搬到北京海淀") -> ConflictEvent:
    return ConflictEvent(
        id=cid,
        memory_id=mid,
        incoming_ref=incoming,
        rule_name="residence",
        kind="mutex",
        status="open",
    )


# ══ #1a：tanyi execute_tanyi_resolution × 并发 correct（P1）══════════════════


class TestTanyiResolutionCorrectionLoss:
    """tanyi.py:201-204：探颐消解按扫描期快照整组写回，并发更正的正文被覆盖。

    注入缝：tanyi 的 utcnow（:204 updated_at 赋值处，在 conf.status 门与
    item 写之间、commit :228 之前——StaticPool 单连接下 B 必须在 A 首条
    DML 之前完成，票 23 偏差一铁律）。B 侧完整真实纠错在缝内提交；
    A 继续按扫描期 version=1 整组写回——B 的更正消失、version 重号。
    修复语义：版本前置 CAS（WHERE version=扫描值），败方如实返回 conflict
    形状（探颐冲突保留 open，交人工重探）。
    """

    def test_concurrent_correction_survives_tanyi_resolution(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_t78a"))
            s.add(_conf("conf_t78a", "mem_t78a"))
            s.commit()

        import lantai.cognition.tanyi as tanyi_mod

        racing = {"done": False}
        real_utcnow = tanyi_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                out_b = correct_memory(
                    "mem_t78a", new_content="B方显式更正后的正文", reason="票78并发"
                )
                assert out_b.get("ok"), f"B 侧纠错应成功：{out_b}"
            return out

        with patch("lantai.cognition.tanyi.utcnow", side_effect=_utcnow_with_interleave):
            with session_factory() as s:
                out_a = execute_tanyi_resolution("conf_t78a", "是的，确认已搬到北京海淀", session=s)

        assert out_a.get("status") == "conflict", (
            f"A 侧应如实报冲突（版本已被并发推进），实际：{out_a}"
        )

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_t78a")
            assert m.content == "B方显式更正后的正文", (
                f"用户显式纠正被探颐旧语义正文覆盖成 {m.content!r}（票面 P1 现状）"
            )
            conf = s.get(ConflictEvent, "conf_t78a")
            assert conf.status == "open", (
                f"败方消解后冲突应保留 open 交人工重探，实际 {conf.status!r}"
            )
            # version 单调：B 纠错 1→2，A 败方不得重号
            assert m.version == 2, f"version={m.version}：纠错 +1 后探颐败方不得再写"

    def test_tanyi_resolution_winner_path_unchanged(self, param_env):
        """回归臂：无并发时探颐消解主路径零变化（肯定分支写 incoming_ref、
        version+1、checkpoint、冲突 resolved）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_t78r"))
            s.add(_conf("conf_t78r", "mem_t78r"))
            s.commit()

        with session_factory() as s:
            out = execute_tanyi_resolution("conf_t78r", "是的，确认已搬到北京海淀", session=s)
        assert out.get("status") == "resolved", f"无并发主路径应照常成功：{out}"

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_t78r")
            assert "北京海淀" in m.content
            assert m.version == 2
            conf = s.get(ConflictEvent, "conf_t78r")
            assert conf.status == "resolved"
            cps = s.exec(
                select(MemoryCheckpoint).where(MemoryCheckpoint.memory_id == "mem_t78r")
            ).all()
            assert len(cps) == 1 and cps[0].trigger == "proactive_probe_resolved"


# ══ #1b：probing resolve_probe_response 肯定分支 × 并发 correct（P1 同型）════


class TestProbeResolutionCorrectionLoss:
    """probing_service.py:551-554：肯定分支 `item.content = conf.incoming_ref;
    item.version += 1` 整组写，与 tanyi 同型。"""

    def test_concurrent_correction_survives_probe_affirmative(self, param_env):
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_t78b"))
            s.add(_conf("conf_t78b", "mem_t78b"))
            s.commit()

        import lantai.services.probing_service as probing_mod

        racing = {"done": False}
        real_utcnow = probing_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                out_b = correct_memory(
                    "mem_t78b", new_content="B方显式更正后的正文", reason="票78并发"
                )
                assert out_b.get("ok"), f"B 侧纠错应成功：{out_b}"
            return out

        with patch("lantai.services.probing_service.utcnow", side_effect=_utcnow_with_interleave):
            out_a = resolve_probe_response(conflict_id="conf_t78b", user_reply="是的没错")

        assert out_a.get("status") == "conflict", f"A 侧应如实报冲突，实际：{out_a}"

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_t78b")
            assert m.content == "B方显式更正后的正文", (
                f"用户显式纠正被探颐肯定分支覆盖成 {m.content!r}（票面 P1 同型现状）"
            )
            assert m.version == 2

    def test_probe_resolution_winner_path_unchanged(self, param_env):
        """回归臂：无并发时肯定分支主路径零变化。"""
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_t78r2"))
            s.add(_conf("conf_t78r2", "mem_t78r2"))
            s.commit()

        out = resolve_probe_response(conflict_id="conf_t78r2", user_reply="是的没错")
        assert out.get("status") == "resolved", f"无并发主路径应照常成功：{out}"

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_t78r2")
            assert "北京海淀" in m.content
            assert m.version == 2


# ══ #2：record_ops revive/unconsolidate 版本重号（P2）═════════════════════════


class TestReviveUnconsolidateVersionClaim:
    """record_ops_service.py:555-556 / :635-636：`version = (version or 1) + 1`
    裸 ORM 写，与并发 correct_memory 交错 → 两迁移重号、checkpoint 链断。"""

    def test_revive_fragment_concurrent_correction_versions_monotonic(self, param_env):
        """碎片起复 × 并发纠错：correct 认领 v2 后，revive 认领 v3 或跳过，
        不得写重号 v2。"""
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_t78c", status="consolidated"))
            s.commit()

        import lantai.services.record_ops_service as ro_mod

        racing = {"done": False}
        real_utcnow = ro_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                out_b = correct_memory("mem_t78c", new_content="并发纠错正文", reason="票78并发")
                assert out_b.get("ok"), f"B 侧纠错应成功：{out_b}"
            return out

        with patch(
            "lantai.services.record_ops_service.utcnow", side_effect=_utcnow_with_interleave
        ):
            # 缝在 _revive_one 内部 utcnow（status 写之前）——B 纠错提交后
            # A 继续按扫描期 version=1 写 v2 → 与 B 的 v2 重号
            revive_consolidated("mem_t78c", reason="票78起复")

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_t78c")
            # version 单调：两条逻辑迁移必须两号（现状双双写 2）
            ckpts = s.exec(
                select(MemoryCheckpoint)
                .where(MemoryCheckpoint.memory_id == "mem_t78c")
                .order_by(MemoryCheckpoint.version)
            ).all()
            versions = [c.version for c in ckpts]
            assert len(versions) == len(set(versions)), (
                f"checkpoint version 重号：{versions}（票面 P2 现状——两个逻辑迁移共写同一号）"
            )
            assert m.version >= 2

    def test_unconsolidate_master_concurrent_correction_versions_monotonic(self, param_env):
        """主记忆撤销 × 并发纠错：correct 认领 v2 后 unconsolidate 的 retracted
        迁移不得写重号 v2。"""
        session_factory, _ = param_env
        master = _mem("mem_t78d", status="active")
        master.source_ids = ["frag_t78d"]  # 主记忆形态标记
        frag = _mem("frag_t78d", status="consolidated")
        with session_factory() as s:
            s.add(master)
            s.add(frag)
            from lantai.models.tables import MemoryEdge

            s.add(
                MemoryEdge(
                    id="edge_t78d",
                    source_memory_id="mem_t78d",
                    target_memory_id="frag_t78d",
                    relation="supersedes",
                    confidence=0.9,
                )
            )
            s.commit()

        import lantai.services.record_ops_service as ro_mod

        racing = {"done": False}
        real_utcnow = ro_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                out_b = correct_memory("mem_t78d", new_content="并发纠错正文", reason="票78并发")
                assert out_b.get("ok"), f"B 侧纠错应成功：{out_b}"
            return out

        with patch(
            "lantai.services.record_ops_service.utcnow", side_effect=_utcnow_with_interleave
        ):
            revive_consolidated("mem_t78d", reason="票78撤销全簇")

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_t78d")
            ckpts = s.exec(
                select(MemoryCheckpoint)
                .where(MemoryCheckpoint.memory_id == "mem_t78d")
                .order_by(MemoryCheckpoint.version)
            ).all()
            versions = [c.version for c in ckpts]
            assert len(versions) == len(set(versions)), (
                f"主记忆 checkpoint version 重号：{versions}（票面 P2 现状）"
            )
            assert m.version >= 2


# ══ #4：状态覆写族逐点（P3）═══════════════════════════════════════════════════


class TestStatusOverwriteFamily:
    """P3 同型逐点：终态不得被扫描期旧判定覆盖（retract × 消解/折叠/修剪交错）。"""

    def test_probe_affirmative_conflict_claim_no_double_resolve(self, param_env):
        """conf.status 裁决翻转（:521/:567）× 并发裁决：open 门后 A 判定期间
        B 已裁 → A 不得覆写 B 的终态（票 22 S6 conflict_service 蓝图同款）。"""
        session_factory, _ = param_env
        with session_factory() as s:
            s.add(_mem("mem_t78e"))
            s.add(_conf("conf_t78e", "mem_t78e"))
            s.commit()

        import lantai.services.probing_service as probing_mod

        racing = {"done": False}
        real_utcnow = probing_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                from lantai.services.conflict_service import resolve_conflict_event

                resolve_conflict_event("conf_t78e", "dismissed", note="票78并发人工裁决")
            return out

        with patch("lantai.services.probing_service.utcnow", side_effect=_utcnow_with_interleave):
            resolve_probe_response(conflict_id="conf_t78e", user_reply="是的没错")

        with session_factory() as s:
            conf = s.get(ConflictEvent, "conf_t78e")
            # 人工裁决先落：dismissed 是终态，探颐肯定分支不得覆写成 resolved
            assert conf.status == "dismissed", (
                f"并发败方覆写人工裁决终态：{conf.status!r}（票面 P3 现状）"
            )
            # 败方不得把正文写进去（resolved_by 也属终态裁决的一部分）
            assert conf.resolved_by != "proactive_probe_user_confirmed" or conf.status == "resolved"

    def test_unconsolidate_master_claim_retracted_not_overwritten(self, param_env):
        """unconsolidate 主记忆 retracted 写 × 并发 retract：retract 已落时
        A 不得按扫描期快照重复写（幂等无害但要认领语义一致——直接验版本单号）。"""
        session_factory, _ = param_env
        master = _mem("mem_t78f", status="active")
        master.source_ids = ["frag_t78f"]
        frag = _mem("frag_t78f", status="consolidated")
        with session_factory() as s:
            s.add(master)
            s.add(frag)
            from lantai.models.tables import MemoryEdge

            s.add(
                MemoryEdge(
                    id="edge_t78f",
                    source_memory_id="mem_t78f",
                    target_memory_id="frag_t78f",
                    relation="supersedes",
                    confidence=0.9,
                )
            )
            s.commit()

        import lantai.services.record_ops_service as ro_mod

        racing = {"done": False}
        real_utcnow = ro_mod.utcnow

        def _utcnow_with_interleave():
            out = real_utcnow()
            if not racing["done"]:
                racing["done"] = True
                from lantai.services.record_ops_service import retract_memory

                out_b = retract_memory("mem_t78f", reason="票78并发撤回")
                assert out_b.get("ok"), f"B 侧撤回应成功：{out_b}"
            return out

        with patch(
            "lantai.services.record_ops_service.utcnow", side_effect=_utcnow_with_interleave
        ):
            revive_consolidated("mem_t78f", reason="票78撤销全簇")

        with session_factory() as s:
            m = s.get(MemoryItem, "mem_t78f")
            assert m.status == "retracted"
            ckpts = s.exec(
                select(MemoryCheckpoint)
                .where(MemoryCheckpoint.memory_id == "mem_t78f")
                .order_by(MemoryCheckpoint.version)
            ).all()
            versions = [c.version for c in ckpts]
            assert len(versions) == len(set(versions)), (
                f"checkpoint version 重号：{versions}（retract 与 unconsolidate 共写同一号）"
            )


# ══ #3：lifecycle.py 死代码登记臂（wontfix 锁）════════════════════════════════


def test_lifecycle_manager_zero_production_callers_lock():
    """#3 登记锁：KnowledgeLifecycleManager 生产零调用方现状锁定（票 23 对
    late_correction 残留先例口径——死代码登记不修，接线前必须先加闸）。
    本臂不锁「永远零调用」——它锁的是「若接线，先加闸」的票面提醒不被遗忘：
    未来任何生产 import 出现时此臂红，提醒接线人补 CAS。"""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for f in (root / "lantai").rglob("*.py"):
        rel = str(f.relative_to(root)).replace("\\", "/")
        if rel == "lantai/cognition/lifecycle.py":
            continue  # 定义本体豁免
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"KnowledgeLifecycleManager|LifecycleTransitionError", text):
            offenders.append(rel)
    assert not offenders, (
        f"lifecycle.py 出现生产消费方（登记解除、接线前必须先加闸 CAS）：{offenders}"
    )
