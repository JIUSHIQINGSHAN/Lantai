"""X-01 回归锁（票 05-v0660-release-gate.md · P0-A）：软删除隐私不得经读通道回流。

`memory.status="archived"` 是**软删除**，`fact_atomic` 不级联删除（既有契约，
见 `tests/test_x01_fact_cascade_regression.py`）。因此「删掉的隐私还能不能被
检索/注入」完全取决于读侧是否 JOIN 回 `memory` 判存活。评审清单
`docs/research/commit-review-v0.38-v0.60-2026-10.md` X-01 指出的三处未闭合：

1. `lantai/retrieval/graph_retriever.py`（贯珠二度事实扩散）两处 `select(FactAtomic)`
   不 JOIN 来源——已归档记忆的隐私原文以 `content` 字段回流图谱联想；
2. `lantai/services/probing_service.py`（探颐）模糊事实查询同上，且冲突可见性
   `_visible()` 只判归属、不判挂载记忆状态；
3. `scripts/shell_hook.py` 把 `atomic_facts` 拼进 prompt 前不断言来源存活。

纪律（AGENTS.md）：真 SQLite + 真生产函数，不 mock 被测逻辑；只用 `param_env`
（自带内存库与撤销，不污染生产全局）。每个"不泄漏"用例都配一个"仍要召回"
控制组，防止用"一律不返回"制造假绿。
"""

import importlib.util
import os

import pytest
from sqlmodel import Session

import lantai.storage.db as db_module
from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import ConflictEvent, FactAtomic, FactEntity, MemoryItem
from lantai.retrieval.graph_retriever import expand_graph_associations
from lantai.services.probing_service import detect_memory_probes

HOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "shell_hook.py"
)

SECRET = "我的银行卡密码是 864213"
QUERY = "我的银行卡密码"
ALIVE_MEM = "我在招商银行办了一张银行卡，余额三千元"
ALIVE_FACT = "我的银行卡在招商银行开户"
SOURCELESS_FACT = "银行卡年费于次年一月免除"
DANGLING_FACT = "我的银行卡绑定手机号已变更"


def _principal(user_id: str = "u1") -> Principal:
    return Principal(user_id=user_id, role="user", allowed_lanes=["general"], tenant_id=None)


def _memory(engine, mid, content, *, status="active", user_id="u1"):
    with Session(engine) as s:
        s.add(
            MemoryItem(
                id=mid,
                memory_type="semantic",
                key=f"k-{mid}",
                content=content,
                lane="general",
                user_id=user_id,
                status=status,
                importance=0.9,
                decay_score=1.0,
                confidence=0.9,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        s.commit()


def _fact(engine, fid, statement, source, *, conf=0.9, entity="银行卡", user_id="u1"):
    with Session(engine) as s:
        s.add(
            FactAtomic(
                id=fid,
                user_id=user_id,
                statement=statement,
                predicate="has",
                confidence=conf,
                source_memory_id=source,
            )
        )
        s.add(FactEntity(fact_id=fid, entity_name=entity, role="subject"))
        s.commit()


def _seed_facts(engine, *, secret_status="archived", fact_conf=0.9):
    """种子：活记忆 m-bank（事实 f-bank）+ 隐私记忆 m-secret（事实 f-secret）。

    两条事实共享实体「银行卡」，所以隐私事实既走 hop-1（来源在 visited 里）
    也走二度实体扩散——两条路都必须拦住。
    """
    _memory(engine, "m-bank", ALIVE_MEM, status="active")
    _memory(engine, "m-secret", SECRET, status=secret_status)
    _fact(engine, "f-bank", ALIVE_FACT, "m-bank", conf=fact_conf)
    _fact(engine, "f-secret", SECRET, "m-secret", conf=fact_conf)


# ── 1. 贯珠：图谱二度事实扩散 ──────────────────────────────────


class TestGraphRetrieverSourceAlive:
    def test_archived_source_fact_not_expanded(self, param_env):
        """软删除记忆的隐私事实不得作为图谱联想内容回流（修复前红）。"""
        _session_factory, engine = param_env
        _seed_facts(engine)
        with Session(engine) as s:
            exp = expand_graph_associations(["m-bank"], principal=_principal(), session=s)
        assert SECRET not in str(exp), f"已删隐私经贯珠回流: {exp!r}"
        assert "m-secret" not in [e["memory_id"] for e in exp]

    def test_dangling_source_fact_not_expanded(self, param_env):
        """来源行不存在（历史孤儿）同样不得扩散——口径同完璧 `_source_alive_clause`。"""
        _session_factory, engine = param_env
        _memory(engine, "m-bank", ALIVE_MEM)
        _fact(engine, "f-bank", ALIVE_FACT, "m-bank")
        _fact(engine, "f-dang", DANGLING_FACT, "m-gone")
        with Session(engine) as s:
            exp = expand_graph_associations(["m-bank"], principal=_principal(), session=s)
        assert DANGLING_FACT not in str(exp), f"孤儿事实经贯珠回流: {exp!r}"

    def test_active_source_fact_still_expands(self, param_env):
        """控制组：活来源事实照常扩散（防止用「一律不返回」变绿）。"""
        _session_factory, engine = param_env
        _seed_facts(engine, secret_status="active")
        with Session(engine) as s:
            exp = expand_graph_associations(["m-bank"], principal=_principal(), session=s)
        assert ALIVE_FACT in str(exp)

    def test_sourceless_fact_still_expands(self, param_env):
        """契约：`source_memory_id IS NULL` 的事实不受来源约束影响。"""
        _session_factory, engine = param_env
        _memory(engine, "m-bank", ALIVE_MEM)
        _fact(engine, "f-bank", ALIVE_FACT, "m-bank")
        _fact(engine, "f-none", SOURCELESS_FACT, None)
        with Session(engine) as s:
            exp = expand_graph_associations(["m-bank"], principal=_principal(), session=s)
        assert SOURCELESS_FACT in str(exp), f"无来源事实被误杀: {exp!r}"


# ── 2. 探颐：模糊事实与冲突可见性 ──────────────────────────────


class TestProbingSourceAlive:
    def test_fuzzy_fact_with_archived_source_yields_no_probe(self, param_env):
        """临界模糊事实（0.3<=conf<=0.6）的来源已归档 → 不得生成探针（修复前红）。"""
        _session_factory, engine = param_env
        _seed_facts(engine, fact_conf=0.5)
        probes = detect_memory_probes(
            query=QUERY, principal=_principal(), db_session=Session(engine)
        )
        assert SECRET not in str(probes), f"已删隐私被包装成求证探针: {probes!r}"
        assert "probe_f-secret" not in [p["probe_id"] for p in probes]

    def test_fuzzy_fact_with_active_source_still_probes(self, param_env):
        """控制组：来源存活时模糊事实照常探针（不放宽为「一律不探」）。"""
        _session_factory, engine = param_env
        _seed_facts(engine, secret_status="active", fact_conf=0.5)
        probes = detect_memory_probes(
            query=QUERY, principal=_principal(), db_session=Session(engine)
        )
        assert "probe_f-bank" in [p["probe_id"] for p in probes]

    def test_conflict_on_archived_memory_yields_no_probe(self, param_env):
        """冲突账本 open 但挂载记忆已软删除 → 正文不得再渲染成问句（修复前红）。"""
        _session_factory, engine = param_env
        _memory(engine, "m-secret", SECRET, status="archived")
        with Session(engine) as s:
            s.add(
                ConflictEvent(
                    id="conf-secret",
                    memory_id="m-secret",
                    incoming_ref="密码已改为 000000",
                    rule_name="r",
                    status="open",
                )
            )
            s.commit()
        probes = detect_memory_probes(
            query=QUERY, principal=_principal(), db_session=Session(engine)
        )
        assert SECRET not in str(probes), f"已删隐私经冲突探针回流: {probes!r}"
        assert "conf-secret" not in [p["conflict_id"] for p in probes]

    def test_conflict_on_active_memory_still_probes(self, param_env):
        """控制组：挂载记忆仍 active 的未决冲突照常探针。"""
        _session_factory, engine = param_env
        _memory(engine, "m-bank", ALIVE_MEM, status="active")
        with Session(engine) as s:
            s.add(
                ConflictEvent(
                    id="conf-bank",
                    memory_id="m-bank",
                    incoming_ref="我在工商银行办了一张银行卡，余额五千元",
                    rule_name="r",
                    status="open",
                )
            )
            s.commit()
        probes = detect_memory_probes(
            query=QUERY, principal=_principal(), db_session=Session(engine)
        )
        assert "conf-bank" in [p["conflict_id"] for p in probes]


# ── 3. shell_hook：注入前的最后一道闸（P0-A 要求 3）────────────


def _load_hook(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("shell_hook_x01", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod.settings, "API_KEY", "test-x01-key")
    monkeypatch.setattr(mod.settings, "RERANKER_ENABLED", False)
    monkeypatch.setattr(mod.settings, "LANTAI_HOME", str(tmp_path))
    return mod


class TestShellHookInjectionGuard:
    def test_hook_exposes_source_alive_guard(self, param_env, monkeypatch, tmp_path):
        mod = _load_hook(monkeypatch, tmp_path)
        assert hasattr(mod, "_live_atomic_facts"), "shell_hook 缺少注入前来源存活断言"

    def test_live_atomic_facts_drops_dead_and_dangling_source(
        self, param_env, monkeypatch, tmp_path
    ):
        mod = _load_hook(monkeypatch, tmp_path)
        _session_factory, engine = param_env
        _seed_facts(engine)
        facts = [
            {"statement": ALIVE_FACT, "source_memory_id": "m-bank"},
            {"statement": SECRET, "source_memory_id": "m-secret"},
            {"statement": DANGLING_FACT, "source_memory_id": "m-gone"},
        ]
        kept = mod._live_atomic_facts(facts)
        assert [f["statement"] for f in kept] == [ALIVE_FACT]

    def test_live_atomic_facts_keeps_sourceless_fact(self, param_env, monkeypatch, tmp_path):
        """口径与 `fact_source_alive_clause()` 一致：无来源事实放行。"""
        mod = _load_hook(monkeypatch, tmp_path)
        _session_factory, engine = param_env
        _memory(engine, "m-bank", ALIVE_MEM)
        kept = mod._live_atomic_facts([{"statement": SOURCELESS_FACT, "source_memory_id": None}])
        assert [f["statement"] for f in kept] == [SOURCELESS_FACT]

    def test_live_atomic_facts_is_fail_closed(self, param_env, monkeypatch, tmp_path):
        """来源查不动 = 无法断言 → 一条都不注入（宁 miss 不脏写）。

        撤销顺序纪律：`param_env` 已把 `db.get_session` 换成内存库工厂，本测试的
        patch 若挂在函数级 `monkeypatch` 上，抓到的「原值」就是 param_env 的替身；
        autouse fixture 使 monkeypatch 比 param_env **更晚**撤销，于是把替身当原值
        写回并泄漏给后续测试（实测被 conftest 的模块级绊线点名前置测试）。故必须
        用就地 `MonkeyPatch.context()`，并在断言里锁住「已就地撤销」。
        """
        session_factory, _engine = param_env
        mod = _load_hook(monkeypatch, tmp_path)

        def boom(*args, **kwargs):
            raise RuntimeError("injected db failure")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(db_module, "get_session", boom)
            kept = mod._live_atomic_facts([{"statement": ALIVE_FACT, "source_memory_id": "m-bank"}])

        assert kept == []
        assert db_module.get_session is session_factory, "patch 未就地撤销，会污染后续测试"

    def test_build_context_does_not_inject_dead_source_fact(self, param_env, monkeypatch, tmp_path):
        """端到端：即便上游把已删隐私事实递过来，prompt 里也不得出现。

        这里只替换**上游** `hybrid_search` 的返回载荷（泄漏注入面），被修的
        注入侧断言 `_live_atomic_facts` 走真库真逻辑。
        """
        mod = _load_hook(monkeypatch, tmp_path)
        _session_factory, engine = param_env
        _seed_facts(engine)
        with Session(engine) as s:
            mem_dict = s.get(MemoryItem, "m-bank").model_dump()
        payload = [
            {
                "score": 0.85,
                "memory": mem_dict,
                "atomic_facts": [
                    {"statement": ALIVE_FACT, "source_memory_id": "m-bank"},
                    {"statement": SECRET, "source_memory_id": "m-secret"},
                ],
            }
        ]
        monkeypatch.setattr(mod, "hybrid_search", lambda q, **kw: payload)
        ctx = mod.build_context("我的银行卡密码是多少来着", "s-x01").get("context", "")
        assert SECRET not in str(ctx), f"已删隐私注入 prompt: {ctx!r}"
        assert ALIVE_FACT in str(ctx), f"注入侧把事实整体抹掉（假绿）: {ctx!r}"
