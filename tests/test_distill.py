"""咀华（Juhua，会话精华萃取）测试——v022 吸收票据 04（上游 session distill）。

不 mock 冒烟：真实内存 SQLite + 真实 MemoryItem/add_memory 管线，仅 mock
外部 LLM chat_json。验收（票据 DoD）：
- 3 条带 session 记忆 → 产出精华；2 条 → skipped/too_short（带原因）
- LLM 不可用 → fallback 产出非空且标 distill_mode=fallback
- 落库走完整闸门管线：store=true 后 evolve 链可产生候选/记忆
"""

from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.ids import new_id
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryItem
from lantai.storage.fts import init_fts, sync_fts


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(e)
    init_fts(e.raw_connection())
    return e


def _add_session_mem(
    engine, session_id: str, content: str, *, lane: str = "general", user_id: str | None = None
) -> str:
    mid = new_id("mem")
    with Session(engine) as s:
        mem = MemoryItem(
            id=mid,
            memory_type="general",
            key=mid,
            content=content,
            lane=lane,
            status="active",
            importance=0.5,
            use_count=0,
            decay_score=1.0,
            last_used_at=utcnow(),
            created_at=utcnow(),
            session_id=session_id,
            user_id=user_id,
        )
        s.add(mem)
        # 同事务写 FTS（生产路径同款）：commit 之后再写会落在未提交事务里，
        # Session 关闭即回滚，memory_fts 恒空
        sync_fts(s, mid, content)
        s.commit()
    return mid


def _distill(
    engine, session_id, *, store=False, llm_side_effect=None, llm_return=None, principal=None
):
    def gts():
        return Session(engine)

    ctx = patch.object(db_module, "get_session", gts)
    llm = patch(
        "lantai.services.distill_service.chat_json",
        return_value=llm_return,
        side_effect=llm_side_effect,
    )
    # store=true 会走 add_memory → _apply_dedup → embed(...)：这是真实外部网络调用，
    # 必须替身。不替身时本地有真 key 所以成功、CI 用 conftest 的假 key 拿到
    # AuthenticationError（catch 后回退 insert），两个环境走不同分支——
    # 这正是「本地绿 CI 红」的来源。embed 同时替 llm.client（源）与
    # memory_service（已绑定引用）两处，因为 `from X import embed` 会各存一份。
    emb = patch("lantai.llm.client.embed", side_effect=lambda texts: [[0.1] * 8 for _ in texts])
    emb_ms = patch(
        "lantai.services.memory_service.embed",
        side_effect=lambda texts: [[0.1] * 8 for _ in texts],
    )
    with ctx, llm, emb, emb_ms:
        from lantai.services.distill_service import distill_session

        return distill_session(session_id, store=store, principal=principal)


class TestDistill:
    def test_too_short_reports_reason(self, engine):
        """2 条 < 最少 3 条：skipped/too_short 且带原因（「怎么没精华」可查）。"""
        _add_session_mem(engine, "s1", "第一句话")
        _add_session_mem(engine, "s1", "第二句话")
        res = _distill(engine, "s1")
        assert res["status"] == "skipped"
        assert res["reason"] == "too_short"
        assert res["source_count"] == 2
        assert res["min_required"] == 3

    def test_llm_mode_summary(self, engine):
        """LLM 可用：产出提炼且 mode=llm。"""
        _add_session_mem(engine, "s2", "一起排查了文件系统软链接的权限问题")
        _add_session_mem(engine, "s2", "最终决定把日志目录挂到独立磁盘")
        _add_session_mem(engine, "s2", "用户确认下周回归测试")
        res = _distill(
            engine,
            "s2",
            llm_return={"summary": "与用户一起定位软链接权限问题并决定日志独立磁盘，下周回归。"},
        )
        assert res["status"] == "ok"
        assert res["mode"] == "llm"
        assert "软链接" in res["summary"]
        assert res["source_count"] == 3

    def test_fallback_deterministic_when_llm_down(self, engine):
        """LLM 挂：确定性降级（最长两条拼接），产出非空、mode=fallback。"""
        _add_session_mem(engine, "s3", "短句")
        _add_session_mem(engine, "s3", "这一段比较长的会话内容记录了完整排查过程与结论")
        _add_session_mem(engine, "s3", "另一条中等长度的会话记录")
        res = _distill(engine, "s3", llm_side_effect=RuntimeError("llm down"))
        assert res["status"] == "ok"
        assert res["mode"] == "fallback"
        assert res["summary"]
        # 可复现：两次降级产出一致（长度是代理指标）
        res2 = _distill(engine, "s3", llm_side_effect=RuntimeError("llm down"))
        assert res2["summary"] == res["summary"]

    def test_emotion_hits_bounded_salience(self, engine):
        """情绪词命中数 → 初始显著性有界加成（0.60~0.85）。"""
        _add_session_mem(engine, "s4", "今天很开心，一起解决了大问题，特别激动")
        _add_session_mem(engine, "s4", "用户表示非常感谢")
        _add_session_mem(engine, "s4", "下次继续保持这种满意的合作状态")
        res = _distill(engine, "s4", llm_side_effect=RuntimeError("down"))
        assert res["status"] == "ok"
        assert res["emotion_hits"] > 0
        assert 0.60 <= res["initial_salience"] <= 0.85

    def test_store_goes_through_full_pipeline(self, engine):
        """store=true：精华经 add_memory 建候选（lane=distill），可被 gate 链消费。"""
        _add_session_mem(engine, "s5", "和团队约定了新的发布流程")
        _add_session_mem(engine, "s5", "发布流程改为先跑全量测试再打包")
        _add_session_mem(engine, "s5", "下周一开始执行新流程")
        res = _distill(engine, "s5", store=True, llm_side_effect=RuntimeError("down"))
        assert res["status"] == "ok"
        assert res["store"]["candidate_id"]
        with Session(engine) as s:
            cand = s.get(MemoryCandidate, res["store"]["candidate_id"])
            assert cand.lane == "distill"
            assert cand.session_id == "s5"
            meta = cand.provenance
            assert meta  # provenance 已构造

    def test_empty_session_returns_skipped(self, engine):
        _add_session_mem(engine, "other", "别的会话的记忆")
        res = _distill(engine, "nosuch")
        assert res["status"] == "skipped"

    def test_stored_distill_recallable_via_hybrid_search(self, engine):
        """票据 04 DoD（整改票 06 去假阳性）：落库走完整管线 → hybrid_search
        召回的必须是**新落库的精华记忆本身**——按 lane=distill + 同 session
        锚定其 id 进命中集；原始记忆含公共词不能顶替断言。"""
        _add_session_mem(engine, "s6", "和团队约定了新的发布流程")
        _add_session_mem(engine, "s6", "发布流程改为先跑全量测试再打包")
        _add_session_mem(engine, "s6", "下周一开始执行新流程")
        res = _distill(
            engine, "s6", store=True, llm_return={"summary": "团队约定发布流程先全量测试再打包"}
        )
        assert res["store"]["candidate_id"]

        def gts():
            return Session(engine)

        # 闸门阈值必须显式钉住：distill 候选的 extractor_confidence 是 0.3，而
        # GATE_MIN_EXTRACTOR_CONF 的**代码默认值是 0.55**——CI 没有 .env，用默认值
        # 会把候选判为 low confidence 拒掉（走 pending_review，宁 miss 不脏写），
        # 于是没有提案、没有 MemoryItem，断言必红。本地 .env 是 0.25 所以一直绿。
        # 同 test_e2e.py 的口径：不让宿主 .env / 环境变量决定测试走哪条分支。
        with (
            patch.object(db_module, "get_session", gts),
            patch.object(settings, "GATE_MIN_EXTRACTOR_CONF", 0.25),
            patch(
                "lantai.evolution.proposer.chat_json",
                return_value={
                    "proposal_type": "add",
                    "target_key": "发布流程约定",
                    "new_content": "团队约定发布流程先全量测试再打包",
                    "memory_type": "semantic",
                    "reason": "r",
                    "confidence": 0.9,
                },
            ),
            patch("lantai.llm.client.embed", side_effect=lambda texts: [[0.1] * 8 for _ in texts]),
            patch(
                "lantai.evolution.promoter.embed",
                side_effect=lambda texts: [[0.1] * 8 for _ in texts],
            ),
            patch(
                "lantai.services.memory_service.embed",
                side_effect=lambda texts: [[0.1] * 8 for _ in texts],
            ),
            patch("lantai.gate.scorer.embed", side_effect=lambda texts: [[0.1] * 8 for _ in texts]),
            patch(
                "lantai.retrieval.hybrid.get_vector_store",
                return_value=Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock()),
            ),
        ):
            from lantai.workers.evolve_worker import run_evolve_once

            run_evolve_once()

        # 锚定新精华：经闸门管线落成的 distill 泳道记忆（candidate → proposal → MemoryItem）
        with Session(engine) as s:
            distill_mem = s.exec(
                select(MemoryItem).where(
                    MemoryItem.lane == "distill", MemoryItem.session_id == "s6"
                )
            ).first()
        assert distill_mem is not None, "精华应经闸门管线落为 distill 泳道记忆"

        # 召回走 keyword 腿（向量 store 空）：命中集必须包含该精华 id
        with (
            patch.object(db_module, "get_session", gts),
            patch("lantai.retrieval.intent.chat_json", return_value={"intent": "fact_lookup"}),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch(
                "lantai.retrieval.hybrid.get_vector_store",
                return_value=Mock(search=Mock(return_value=[]), add=Mock(), delete=Mock()),
            ),
        ):
            from lantai.retrieval import hybrid

            hits = hybrid.hybrid_search("发布流程 全量测试", top_k=5, use_rerank=False)
        assert distill_mem.id in [h["memory"]["id"] for h in hits], (
            f"新精华 {distill_mem.id} 未被召回，命中: {[h['memory']['id'] for h in hits]}"
        )


class TestDistillRoute:
    """REST POST /session/distill"""

    @pytest.fixture()
    def client(self, engine):
        def gts():
            return Session(engine)

        with (
            patch.object(db_module, "get_session", gts),
            patch("lantai.retrieval.intent.chat_json", return_value={"intent": "fact_lookup"}),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch("lantai.retrieval.reranker.rerank", return_value=[]),
            patch("lantai.gate.scorer.embed", return_value=[[0.1] * 8]),
            patch("lantai.retrieval.hybrid.get_vector_store"),
            patch("lantai.storage.vector_store.ChromaVectorStore"),
            patch(
                "lantai.services.distill_service.chat_json", return_value={"summary": "精华一句话"}
            ),
        ):
            from fastapi.testclient import TestClient

            from lantai.api.app import app

            with TestClient(app) as c:
                yield c

    def test_route_preview_only(self, client, engine):
        for i in range(3):
            _add_session_mem(engine, "sess_http", f"会话要点{i}：部署配置已确认")
        resp = client.post("/session/distill", json={"session_id": "sess_http"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["mode"] == "llm"
        assert "store" not in data

    def test_route_empty_session_id_422(self, client):
        resp = client.post("/session/distill", json={"session_id": ""})
        assert resp.status_code == 422


class TestDistillAuthz:
    """整改票 03：distill 泳道权限——默认放行（可写可召回）+ 受限密钥读写收窄。"""

    _SEED = ("和团队约定了新的发布流程", "发布流程改为先跑全量测试再打包", "下周一开始执行新流程")
    # 测试专用假 token（非真实凭据）：运行期拼接构造，避免字面量形态（同 test_auth 惯例）
    _ENV_KEY = "env-" + "secret"

    @pytest.fixture()
    def client(self, engine):
        def gts():
            return Session(engine)

        with (
            patch.object(db_module, "get_session", gts),
            patch("lantai.retrieval.intent.chat_json", return_value={"intent": "fact_lookup"}),
            patch("lantai.retrieval.hybrid.embed", return_value=[[0.1] * 8]),
            patch("lantai.retrieval.reranker.rerank", return_value=[]),
            patch("lantai.gate.scorer.embed", return_value=[[0.1] * 8]),
            patch("lantai.retrieval.hybrid.get_vector_store"),
            patch("lantai.storage.vector_store.ChromaVectorStore"),
            # 精华提炼 LLM 不可用 → 确定性 fallback（落库内容可复现，fastpath 可离线直写）
            patch(
                "lantai.services.distill_service.chat_json",
                side_effect=RuntimeError("llm down"),
            ),
        ):
            from fastapi.testclient import TestClient

            from lantai.api.app import app

            with TestClient(app) as c:
                yield c

    def test_default_lanes_include_distill(self):
        from lantai.core.auth import DEFAULT_LANES

        assert "distill" in DEFAULT_LANES

    def test_env_key_store_ok(self, client, engine, monkeypatch):
        """默认密钥（环境 key → DEFAULT_LANES）可落库精华（写入侧默认放行）。"""
        monkeypatch.setattr(settings, "API_KEY", self._ENV_KEY)
        for i, c in enumerate(self._SEED):
            _add_session_mem(engine, "s_auth", f"{c}{i}")
        resp = client.post(
            "/session/distill",
            json={"session_id": "s_auth", "store": True},
            headers={"X-API-Key": self._ENV_KEY},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["store"]["candidate_id"]

    def test_env_key_can_recall_distill_lane(self, client, engine, monkeypatch):
        """默认密钥可召回 distill 泳道记忆（检索出口默认放行）。

        关键词腿按 user_id 圈定：环境密钥主体 user_id=api_key，探针记忆需同主体。"""
        monkeypatch.setattr(settings, "API_KEY", self._ENV_KEY)
        _add_session_mem(
            engine, "s_probe", "奎章阁探针零四六精华句", lane="distill", user_id="api_key"
        )
        resp = client.post(
            "/search",
            json={"query": "奎章阁探针零四六", "top_k": 5, "force": True},
            headers={"X-API-Key": self._ENV_KEY},
        )
        assert resp.status_code == 200, resp.text
        assert any("奎章阁探针零四六" in r["document"] for r in resp.json()["results"])

    def test_restricted_bearer_store_403(self, client, engine):
        """受限密钥（泳道集无 distill）store=true → 403，拒绝而非放行。"""
        from lantai.core.auth import create_api_key

        raw, key = create_api_key("restricted", ["general"])
        with Session(engine) as s:
            s.add(key)
            s.commit()
        for i, c in enumerate(self._SEED):
            _add_session_mem(engine, "s_auth2", f"{c}{i}")
        resp = client.post(
            "/session/distill",
            json={"session_id": "s_auth2", "store": True},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 403, resp.text

    def test_restricted_bearer_with_distill_store_ok(self, client, engine):
        """显式授权 distill 的受限密钥可落库（不扩大未授权密钥权限）。"""
        from lantai.core.auth import create_api_key

        raw, key = create_api_key("distiller", ["general", "distill"])
        with Session(engine) as s:
            s.add(key)
            s.commit()
        for i, c in enumerate(self._SEED):
            _add_session_mem(engine, "s_auth3", f"{c}{i}")
        resp = client.post(
            "/session/distill",
            json={"session_id": "s_auth3", "store": True},
            headers={"Authorization": f"Bearer {raw}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["store"]["candidate_id"]

    def test_source_rows_scoped_by_allowed_lanes(self, engine):
        """读取收窄：非 admin 调用者的摘要只来自其泳道集内的源记忆。

        allowed 行数须 ≥ DISTILL_MIN_MEMORIES(3)，否则 too_short 跳过无法证收窄。"""
        _add_session_mem(engine, "s_scope", "公开要点：发布流程确认一")
        _add_session_mem(engine, "s_scope", "公开要点：发布流程确认二")
        _add_session_mem(engine, "s_scope", "公开要点：发布流程确认三")
        _add_session_mem(engine, "s_scope", "机密内容：薪酬数据绝密甲", lane="secret")
        _add_session_mem(engine, "s_scope", "机密内容：薪酬数据绝密乙", lane="secret")
        from lantai.core.auth import Principal

        principal = Principal(user_id="u1", allowed_lanes=["general"])
        res = _distill(engine, "s_scope", llm_side_effect=RuntimeError("down"), principal=principal)
        assert res["status"] == "ok"
        assert res["source_count"] == 3
        assert "薪酬" not in res["summary"]
