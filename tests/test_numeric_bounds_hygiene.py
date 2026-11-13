"""票 68：数值边界与散点卫生九位点——红测（可红臂 8 处）。

每位点真函数/真引擎直调（真内存库，不 mock 被测链路），修复前错误行为可断言
（红），修复后 ∈界/拒绝/正确判定（绿）。合法路径行为零变化（现状锁臂如实申报）。

位点→臂对照（票面九位点）：
1. conflicts._temporal_recency 未来时间无上钳 → recency 巨值碾压仲裁权重
   （红臂：3000 年 event_time 断言 rec ∈ [0,1]）
2. niyi deliberate confidence 裸 float → "high" 炸 500 面 / 1.5、-0.2 越界静默通过
   （红臂：越界与非数值断言 ValueError 带 confidence 指引）
3. lifecycle falsy-or 吞 0.0 ×2（confidence/decay_score）→ 零值记忆逃过 weaken/retire
   （红臂：0.0 断言仍触发扫描动作）
4. zuozheng `confidence or 0.8` → 零置信证据被静默抬成 0.8 权重
   （红臂：断言节点置信度保持 0.0）
5. shishi 写面 tenant 缺席 / ownerless 行可改写 / admin .first() 无序 —— **不入红测**：
   行为红不可确定性断言（NULL 行存量形态待查，票面自标 Fog），Answer 登记裁量
6. canshang 字符集假矛盾 + OPPOSITE_PAIRS 裸子串 → 无关主题/异主体主张判假矛盾
   （红臂：两组自然文本断言无 violation；现状锁臂：真矛盾仍检出）
7. evolution dedup_key check-then-act 竞态 → 并发同签名双行
   （确定性红臂：dedup_key 唯一索引缺失（现仅普通 index）；并发臂 ×20 为
   非确定性补充，红相通过=本次未撞窗口，如实记录）
8. cehou 终态窗口收样本无界 → graduated 后样本照收 + 报警风暴
   （红臂：毕业窗断言 ValueError；观察中收样本=同一流程内的现状锁）
9a. zhaji 截断硬编码 [:DEFAULT_MAX_CHARS] 忽略 max_chars 参数
   （红臂：max_chars=200 断言落库 200 字）
9b. diben 无 session_id 全表加载（limit 缺失）——**不入红测**：行为语义经 Python
    侧 ACL 过滤后正确（票 57 口径），病在效率面无行为红；修复走 ACL 下推 +
    回归现状锁，Answer 登记。

禁 mock：全部真引擎真会话；外部网络零涉及。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.cli.mcp import handle_cehou_observe
from lantai.models.tables import (
    CognitivePattern,
    MemoryEdge,
    MemoryItem,
    SessionScratchpad,
)


@pytest.fixture()
def mem_db():
    """真 SQLite 内存库 + get_session 指向它（不 mock 计算逻辑）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    import lantai.storage.fts as fts

    with eng.connect() as conn:
        fts.init_fts(conn.connection.driver_connection)

    original = db_module.get_session

    def session_factory() -> Session:
        return Session(eng)

    db_module.get_session = session_factory
    yield eng
    db_module.get_session = original


def _seed_memory(eng, mid: str, **kw) -> None:
    params = {
        "id": mid,
        "memory_type": "semantic",
        "key": f"key-{mid}",
        "content": kw.pop("content", f"{mid} 的正文断言"),
        "namespace": "default",
        "status": "active",
        "tier": "working",
        "importance": 0.9,
        "confidence": kw.pop("confidence", 0.9),
        "reason": "",
        "role": "OBSERVATION",
        "lane": "general",
        "domain": "general",
        "version": 1,
    }
    params.update(kw)
    with Session(eng) as s:
        s.add(MemoryItem(**params))
        s.commit()


# ─────────────────────────────────────────────
# 位点 1：conflicts recency 未来时间无上钳
# ─────────────────────────────────────────────


class TestRecencyBounds:
    def test_future_event_time_recency_clamped(self):
        """红：event_time 在未来 → days_old 为负 → recency 巨值（3000 年 ≈ 上万），
        单条时间异常行即可碾压仲裁权重。修复后 rec ∈ [0, 1]（未来时间不奖励）。"""
        from lantai.cognition.conflicts import _temporal_recency

        item = SimpleNamespace(event_time=datetime(3000, 1, 1, tzinfo=UTC), created_at=None)
        rec, axis = _temporal_recency(item)
        assert axis == "event"
        assert 0.0 <= rec <= 1.0, f"未来时间 recency 无上钳（病灶实锤）：{rec}"

    def test_past_recency_unchanged(self):
        """现状锁：正常过去时间语义零变化（10 天前 → 1 - 10/30）。"""
        from lantai.cognition.conflicts import _temporal_recency

        item = SimpleNamespace(event_time=datetime.now(UTC) - timedelta(days=10), created_at=None)
        rec, _ = _temporal_recency(item)
        assert rec == pytest.approx(1.0 - 10.0 / 30.0)


# ─────────────────────────────────────────────
# 位点 2：niyi confidence 裸 float
# ─────────────────────────────────────────────


class TestNiyiConfidenceBounds:
    def test_confidence_out_of_range_rejected(self, mem_db):
        """红：confidence=1.5/-0.2 越界静默通过（conf_delta 直接吃进风险评级）。
        修复后 ValueError（钳 [0,1]，拒绝而非静默钳制）。"""
        _seed_memory(mem_db, "mem-n68")
        from lantai.cognition.niyi import NiyiEngine

        with Session(mem_db) as s:
            engine = NiyiEngine(session=s)
            for bad in (1.5, -0.2):
                with pytest.raises(ValueError):
                    engine.deliberate_proposal("update", {"confidence": bad}, "mem-n68")

    def test_confidence_nonnumeric_clear_error(self, mem_db):
        """红："high" 走 float() 报「could not convert string to float」——
        调用方无从自查且 REST 面炸 500。修复后 ValueError 带 confidence 指引。"""
        _seed_memory(mem_db, "mem-n68b")
        from lantai.cognition.niyi import NiyiEngine

        with Session(mem_db) as s:
            engine = NiyiEngine(session=s)
            with pytest.raises(ValueError, match="confidence"):
                engine.deliberate_proposal("update", {"confidence": "high"}, "mem-n68b")

    def test_valid_confidence_unchanged(self, mem_db):
        """现状锁：合法 confidence 全链行为零变化（产出推演回执）。"""
        _seed_memory(mem_db, "mem-n68c")
        from lantai.cognition.niyi import NiyiEngine

        with Session(mem_db) as s:
            engine = NiyiEngine(session=s)
            res = engine.deliberate_proposal("update", {"confidence": 0.7}, "mem-n68c")
            assert isinstance(res, dict)


# ─────────────────────────────────────────────
# 位点 3：lifecycle falsy-or 吞 0.0
# ─────────────────────────────────────────────


class TestLifecycleFalsyOr:
    def _item(self, **kw) -> MemoryItem:
        params = {
            "id": kw.pop("id", "m-lc"),
            "memory_type": "semantic",
            "key": "key-lc",
            "content": "生命周期扫描零值臂正文",
            "namespace": "default",
            "status": "active",
            "tier": "working",
            "importance": 0.9,
            "confidence": 0.9,
            "reason": "",
            "role": "OBSERVATION",
            "lane": "general",
            "domain": "general",
            "version": 1,
        }
        params.update(kw)
        return MemoryItem(**params)

    def test_zero_confidence_still_weakened(self, mem_db):
        """红：confidence=0.0 经 `x or 1.0` 吞成 1.0 → 永不 weaken（零置信记忆
        逃过降级扫描）。修复后显式 None 判定，0.0 < 阈值照常触发。"""
        from lantai.cognition.lifecycle import KnowledgeLifecycleManager, LifecycleStatus

        item = self._item(id="m-lc0", confidence=0.0)
        with Session(mem_db) as s:
            s.add(item)
            s.commit()
            mgr = KnowledgeLifecycleManager(s)
            mgr.scan_and_weaken([item])
            assert item.lifecycle_status == LifecycleStatus.WEAKENED, (
                f"confidence=0.0 未触发 weaken（falsy-or 吞 0.0 病灶实锤）：{item.lifecycle_status}"
            )

    def test_zero_decay_still_retired(self, mem_db):
        """红：decay_score=0.0 经 `x or 1.0` 吞成 1.0 → 永不 retire。"""
        from lantai.cognition.lifecycle import KnowledgeLifecycleManager, LifecycleStatus

        item = self._item(id="m-lc1", decay_score=0.0)
        item.lifecycle_status = LifecycleStatus.WEAKENED
        with Session(mem_db) as s:
            s.add(item)
            s.commit()
            mgr = KnowledgeLifecycleManager(s)
            mgr.scan_and_retire([item])
            assert item.lifecycle_status == LifecycleStatus.RETIRED, (
                f"decay_score=0.0 未触发 retire（falsy-or 吞 0.0 病灶实锤）：{item.lifecycle_status}"
            )

    def test_normal_scan_unchanged(self, mem_db):
        """现状锁：正常值扫描语义零变化（0.9 不降级、0.9 decay 不退役）。"""
        from lantai.cognition.lifecycle import KnowledgeLifecycleManager, LifecycleStatus

        keep = self._item(id="m-lc2", confidence=0.9)
        keep2 = self._item(id="m-lc3", decay_score=0.9)
        keep2.lifecycle_status = LifecycleStatus.WEAKENED
        with Session(mem_db) as s:
            s.add(keep)
            s.add(keep2)
            s.commit()
            mgr = KnowledgeLifecycleManager(s)
            mgr.scan_and_weaken([keep])
            mgr.scan_and_retire([keep2])
            assert keep.lifecycle_status == LifecycleStatus.ACTIVE
            assert keep2.lifecycle_status == LifecycleStatus.WEAKENED


# ─────────────────────────────────────────────
# 位点 4：zuozheng `confidence or 0.8`
# ─────────────────────────────────────────────


class TestZuozhengConfidenceOr:
    def test_zero_confidence_source_not_lifted(self, mem_db):
        """红：源记忆 confidence=0.0 经 `or 0.8` 抬成 0.8 → 证据节点权重失真。
        修复后节点置信度保持 0.0（min(edge.conf, 0.0)）。"""
        _seed_memory(mem_db, "mem-z68t", confidence=0.9)
        _seed_memory(mem_db, "mem-z68s", confidence=0.0)
        with Session(mem_db) as s:
            s.add(
                MemoryEdge(
                    id="e-z68",
                    source_memory_id="mem-z68s",
                    target_memory_id="mem-z68t",
                    relation="supports",
                    confidence=0.9,
                )
            )
            s.commit()
        from lantai.cognition.zuozheng import ZuozhengEngine

        with Session(mem_db) as s:
            chain = ZuozhengEngine(session=s).inspect_evidence("mem-z68t")
        node = next(n for n in chain["evidence_nodes"] if n["id"] == "mem-z68s")
        assert node["confidence"] == 0.0, (
            f"零置信源记忆被抬成 {node['confidence']}（or 0.8 病灶实锤）"
        )

    def test_normal_confidence_unchanged(self, mem_db):
        """现状锁：正常置信度取 min 语义零变化（0.7 源 × 0.9 边 → 0.7）。"""
        _seed_memory(mem_db, "mem-z68t2", confidence=0.9)
        _seed_memory(mem_db, "mem-z68s2", confidence=0.7)
        with Session(mem_db) as s:
            s.add(
                MemoryEdge(
                    id="e-z68b",
                    source_memory_id="mem-z68s2",
                    target_memory_id="mem-z68t2",
                    relation="supports",
                    confidence=0.9,
                )
            )
            s.commit()
        from lantai.cognition.zuozheng import ZuozhengEngine

        with Session(mem_db) as s:
            chain = ZuozhengEngine(session=s).inspect_evidence("mem-z68t2")
        node = next(n for n in chain["evidence_nodes"] if n["id"] == "mem-z68s2")
        assert node["confidence"] == 0.7


# ─────────────────────────────────────────────
# 位点 6：canshang 字符集假矛盾 + 裸子串
# ─────────────────────────────────────────────


class TestCanshangFalseContradiction:
    def test_unrelated_topics_not_flagged(self, mem_db):
        """红：两段无关主题文本共享 ≥3 个常用字 + 各含一对互斥词——字符集
        交叉判定判「假矛盾」。修复后动词性开关对须词级主题交叉，不再误报。"""
        _seed_memory(mem_db, "mem-c68a", content="系统管理员启用定时备份功能模块")
        _seed_memory(mem_db, "mem-c68b", content="下班后管理员关闭照明电源设备")
        from lantai.cognition.canshang import CanshangEngine

        with Session(mem_db) as s:
            a = s.get(MemoryItem, "mem-c68a")
            b = s.get(MemoryItem, "mem-c68b")
            v = CanshangEngine(session=s).detect_pairwise_contradiction(a, b)
        assert v is None, f"无关主题误报直接矛盾（假矛盾病灶实锤）：{v.description if v else ''}"

    def test_different_subjects_not_flagged(self, mem_db):
        """红：主语不同（功能 vs 鼠标硬件）的非矛盾主张——裸子串命中
        (支持, 不支持) 词对（B 的「不支持」含子串「支持」双向命中）+ 字符集
        交叉 ≥3 → 判假矛盾。修复后否定衍生对从严（词级主题交叉），不再误报。"""
        _seed_memory(mem_db, "mem-c68c", content="该功能支持自定义快捷键设置项")
        _seed_memory(mem_db, "mem-c68d", content="鼠标硬件不支持自定义宏按键编程")
        from lantai.cognition.canshang import CanshangEngine

        with Session(mem_db) as s:
            a = s.get(MemoryItem, "mem-c68c")
            b = s.get(MemoryItem, "mem-c68d")
            v = CanshangEngine(session=s).detect_pairwise_contradiction(a, b)
        assert v is None, f"异主体主张误报矛盾（假矛盾病灶实锤）：{v.description if v else ''}"

    def test_true_contradiction_still_detected(self, mem_db):
        """现状锁：同主题真矛盾（启用 vs 关闭同一对象）修复后仍须检出。"""
        _seed_memory(mem_db, "mem-c68e", content="系统已启用自动备份功能")
        _seed_memory(mem_db, "mem-c68f", content="系统已关闭自动备份功能")
        from lantai.cognition.canshang import CanshangEngine

        with Session(mem_db) as s:
            a = s.get(MemoryItem, "mem-c68e")
            b = s.get(MemoryItem, "mem-c68f")
            v = CanshangEngine(session=s).detect_pairwise_contradiction(a, b)
        assert v is not None, "真矛盾被误放行（修复矫枉过正）"


# ─────────────────────────────────────────────
# 位点 7：evolution dedup_key check-then-act 竞态
# ─────────────────────────────────────────────


class TestEvolutionDedupRace:
    def _experiences(self) -> list[MemoryItem]:
        return [
            MemoryItem(
                id=f"m-e68-{i}",
                memory_type="episodic",
                key=f"key-e68-{i}",
                content="知识图谱架构支持增量更新与回放审计",
                namespace="default",
                status="active",
                tier="working",
                importance=0.9,
                confidence=0.9,
                reason="",
                role="OBSERVATION",
                lane="general",
                domain="general",
                version=1,
            )
            for i in range(2)
        ]

    def test_dedup_key_unique_index(self, mem_db):
        """确定性红：dedup_key 仅有普通 index 无唯一约束——check-then-act 在
        并发下可双行落库，schema 层无兜底。修复后唯一索引/唯一约束存在。
        （表名为 SQLModel 默认小写 cognitivepattern，migrations v29 同名。）"""
        inspector = sa_inspect(mem_db)
        uniques = [
            idx["name"]
            for idx in inspector.get_indexes("cognitivepattern")
            if idx["unique"] and "dedup_key" in idx["column_names"]
        ]
        uniques += [
            c["name"]
            for c in inspector.get_unique_constraints("cognitivepattern")
            if "dedup_key" in c["column_names"]
        ]
        assert uniques, "cognitivepattern.dedup_key 无唯一索引/约束（竞态无 schema 兜底，病灶实锤）"

    def test_concurrent_same_signature_single_row(self, tmp_path):
        """并发臂（非确定性）：双线程 ×20 同签名 detect_patterns——修复后唯一
        索引兜底恒单行。红相若通过=本次未撞 check-then-act 窗口（确定性红证
        由 schema 臂承担），如实记录不强判。不用 Barrier：worker 异常时
        barrier.wait() 永久等待会挂死整跑（首跑实测），改异常捕获式并发。
        各线程独立连接（临时文件库）：StaticPool 共享单连接下双 Session
        begin/commit 交错互踩事务（cannot commit 实测）——那测的是装置不是
        产品并发路径。"""
        import threading

        from lantai.cognition.evolution import EvolutionEngine

        eng = create_engine(f"sqlite:///{tmp_path / 'race68.db'}")
        SQLModel.metadata.create_all(eng)

        worker_errors: list[Exception] = []

        def _worker():
            try:
                with Session(eng) as s:
                    engine = EvolutionEngine(db=s)
                    for _ in range(20):
                        engine.detect_patterns(self._experiences())
            except Exception as exc:  # noqa: BLE001 — 测试装置收集线程异常
                worker_errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "worker 线程 60s 未退出（挂死）"
        assert not worker_errors, f"worker 异常：{worker_errors!r}"

        with Session(eng) as s:
            rows = s.exec(select(CognitivePattern)).all()
        assert len(rows) == 1, f"同签名并发落库 {len(rows)} 行（双行实锤）"


# ─────────────────────────────────────────────
# 位点 8：cehou 终态窗口收样本无界
# ─────────────────────────────────────────────


def _create_window(window_id: str, **kw) -> dict:
    params = {
        "create": True,
        "window_id": window_id,
        "target_type": "param",
        "target_id": "t-68",
    }
    params.update(kw)
    return handle_cehou_observe(params)


class TestCehouTerminalSample:
    def test_graduated_window_rejects_samples(self, mem_db):
        """红：窗口毕业（终态）后样本照收、计数照涨、报警面无条数上限——
        大库单窗口报警风暴。修复后终态窗口收样本 ValueError（观察中收样本
        不受影响，由同一流程的前置样本臂锁住）。"""
        _create_window("w-t68", required_days=1, min_samples=1)
        rec = handle_cehou_observe(
            {
                "record_sample": True,
                "window_id": "w-t68",
                "success": True,
                "latency_ms": 100.0,
                "score_delta": 0.5,
            }
        )
        assert rec.get("success") is True, "观察中收样本须正常（现状锁）"
        ins = handle_cehou_observe({"window_id": "w-t68", "mock_days_observed": 1})
        assert ins.get("state") == "graduated", f"毕业流程未走通：{ins}"
        with pytest.raises(ValueError):
            handle_cehou_observe(
                {
                    "record_sample": True,
                    "window_id": "w-t68",
                    "success": True,
                    "latency_ms": 100.0,
                    "score_delta": 0.5,
                }
            )


# ─────────────────────────────────────────────
# 位点 9a：zhaji 截断硬编码
# ─────────────────────────────────────────────


class TestZhajiTruncation:
    def test_stored_content_honors_max_chars(self, mem_db):
        """红：落库内容硬编码 [:DEFAULT_MAX_CHARS]（1000），忽略 max_chars
        参数——调用方传入的上界被静默无视。修复后落库 [:max_chars]。"""
        from lantai.cognition.zhaji import ZhajiEngine

        raw = "工作便签内容甲乙丙丁" * 200  # 2000 字
        with Session(mem_db) as s:
            ZhajiEngine(session=s).save_and_digest("s-z68", raw, write=True, max_chars=200)
            row = s.get(SessionScratchpad, "s-z68")
        assert row is not None
        assert len(row.content) == 200, (
            f"max_chars=200 被无视，落库 {len(row.content)} 字（硬编码病灶实锤）"
        )

    def test_default_truncation_unchanged(self, mem_db):
        """现状锁：默认上界语义零变化（1200 字原文 → 落库 1000）。"""
        from lantai.cognition.zhaji import ZhajiEngine

        raw = "工作便签内容子丑寅卯" * 240  # 2400 字
        with Session(mem_db) as s:
            ZhajiEngine(session=s).save_and_digest("s-z68b", raw, write=True)
            row = s.get(SessionScratchpad, "s-z68b")
        assert row is not None
        assert len(row.content) == 1000
