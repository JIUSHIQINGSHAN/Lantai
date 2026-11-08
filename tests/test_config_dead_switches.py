"""票 43：配置面死开关——`DIALOGUE_ENABLED` 没接线 + 察窗到期检查未调度。

两病灶（立案基线亲核，HEAD `02fde590` 时点仍成立）：
1. `settings.DIALOGUE_ENABLED` 全仓零消费点：四个对话摄取入口（REST
   `/dialogue`、MCP `add_dialogue`、潜移 async submit、冷启动导入）只受
   `DIALOGUE_MIN_CHARS` 等阈值约束——用户设 `DIALOGUE_ENABLED=false`
   对话照常摄取。修复口径：接线（票 34 先例），统一 guard 单一真源
   （漏斗 `ingest_dialogue` 首行 + 潜移提交时 fail-fast），禁止四入口散写。
2. `check_shadow_due`（parameters/runtime.py）仅有定义与直调测试，
   `core/scheduler.py` 的作业注册面零引用——影子观察窗打开后永不到期
   评估，promote/rollback 裁决在生产不会发生。修复口径：注册进调度器
   （`SHADOW_CHECK_INTERVAL_SECONDS` 真正生效，形态同 refresh_runtime_params
   先例：无总开关、空转廉价）。

禁 mock 被测链路：真内存库（param_env）+ 真摄取链；摄取文本全部取
fastpath 白名单形态（「记住：…」），全链确定性、不依赖外部 LLM。
调度器注册锁用 BackgroundScheduler 类壳（tests/test_receipt_miss_scrub.py
范式——被测行为是注册配置本身，壳是仓内既定替身面）。
"""

import json
import contextlib

import pytest
from sqlmodel import select

from lantai.core.settings import settings
from lantai.models.tables import MemoryCandidate

_FASTPATH_TEXT = "记住：票43-验收基准线是绿线"


# ── 病灶 1：DIALOGUE_ENABLED 接线（四入口 + 对照臂）───────────────────────


def test_dialogue_disabled_blocks_rest_ingest(param_env, monkeypatch):
    """红：开关 false 时 POST /dialogue 现状 200 + 候选真落库（关不掉）。
    修复后 409 Conflict + 零候选；/dialogue/async 同口径 409。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", False)
    sf, _engine = param_env

    from fastapi.testclient import TestClient
    from lantai.core.acl import Principal
    from lantai.core.auth import get_current_user

    principal = Principal(user_id="user-A", tenant_id=None, allowed_lanes=None, role="user")
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/dialogue", json={"text": _FASTPATH_TEXT})
            resp_async = c.post("/dialogue/async", json={"text": _FASTPATH_TEXT})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 409, (
        f"DIALOGUE_ENABLED=false 时 /dialogue 应 409（服务端配置拒绝，非调用方"
        f"输入问题）；现状 {resp.status_code} body={resp.text[:160]}（死开关：照常摄取）"
    )
    assert resp_async.status_code == 409, (
        f"/dialogue/async 应同口径 409；现状 {resp_async.status_code}"
    )
    with sf() as s:
        rows = s.exec(
            select(MemoryCandidate).where(MemoryCandidate.summary.contains("票43"))
        ).all()
        assert rows == [], "开关 false 不得摄取任何对话（候选真落库 = 死开关实锤）"


def test_dialogue_disabled_blocks_mcp_add_dialogue(param_env, monkeypatch):
    """红：MCP 入口现状照常摄取。修复后提交即拒（DialogueDisabledError）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", False)
    sf, _engine = param_env
    from lantai.cli.mcp import handle_add_dialogue

    with pytest.raises(Exception, match="DIALOGUE_ENABLED") as ei:
        handle_add_dialogue({"text": _FASTPATH_TEXT, "user_id": "user-A"})
    assert type(ei.value).__name__ == "DialogueDisabledError", (
        f"应抛专用 DialogueDisabledError（REST 侧译 409 依赖类型）；实得 {type(ei.value).__name__}"
    )
    with sf() as s:
        rows = s.exec(select(MemoryCandidate)).all()
        assert rows == [], "MCP 入口开关 false 不得摄取"


def test_dialogue_disabled_blocks_async_submit(param_env, monkeypatch):
    """红：潜移提交现状受理并入队。修复后提交即拒（fail-fast，不产生任务）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", False)
    sf, _engine = param_env
    from lantai.services.async_ingest_service import submit_async_dialogue

    with pytest.raises(Exception, match="DIALOGUE_ENABLED"):
        submit_async_dialogue(_FASTPATH_TEXT, user_id="user-A")
    with sf() as s:
        rows = s.exec(select(MemoryCandidate)).all()
        assert rows == [], "潜移提交被拒后不得有任何候选落库"


def test_dialogue_disabled_blocks_session_import(param_env, monkeypatch):
    """红：冷启动导入现状逐条照常摄取。修复后 imported=0 + 逐条计
    ingest_errors（宁 miss 不脏写，单行失败不拖停整批口径不变）。"""
    import tempfile
    from pathlib import Path

    monkeypatch.setattr(settings, "DIALOGUE_ENABLED", False)
    sf, _engine = param_env
    from lantai.ingestion.import_service import import_session_jsonl

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s.jsonl"
        p.write_text(
            "\n".join(
                json.dumps({"role": "user", "content": f"记住：票43导入{i}", "timestamp": "2026-01-01T08:00:00Z"})
                for i in range(2)
            ),
            encoding="utf-8",
        )
        res = import_session_jsonl(str(p), user_id="user-A")

    assert res["imported"] == 0, f"开关 false 时导入应 0 条；现状 {res['imported']}"
    assert res["ingest_errors"] == 2, (
        f"被拒行应逐条计入 ingest_errors（宁 miss 不脏写口径）；现状 {res['ingest_errors']}"
    )
    with sf() as s:
        rows = s.exec(select(MemoryCandidate)).all()
        assert rows == [], "导入入口开关 false 不得摄取"


def test_dialogue_enabled_control_unchanged(param_env):
    """对照臂（恒绿）：开关默认 true，摄取行为逐字不变——修复不得伤主路径。"""
    sf, _engine = param_env
    from lantai.ingestion.dialogue import ingest_dialogue

    res = ingest_dialogue(_FASTPATH_TEXT, user_id="user-A")
    assert res["ingested"] is True and res["status"] == "fastpath"
    with sf() as s:
        rows = s.exec(
            select(MemoryCandidate).where(MemoryCandidate.user_id == "user-A")
        ).all()
        assert len(rows) == 1, "对照臂：fastpath 候选应恰一条落库"


# ── 病灶 2：check_shadow_due 注册进调度器 ────────────────────────────────


class _FakeScheduler:
    """BackgroundScheduler 类壳（tests/test_receipt_miss_scrub.py 范式）。"""

    def __init__(self, **kwargs):
        self.jobs = []

    def add_job(self, fn, trigger=None, **kwargs):
        self.jobs.append({"id": kwargs.get("id"), "trigger": trigger, "kwargs": kwargs, "fn": fn})

    def start(self):
        pass


def _mount_fake_scheduler(monkeypatch):
    import lantai.core.scheduler as scheduler_mod

    fake = _FakeScheduler()
    # start_scheduler() 会重建 _scheduler，必须连 BackgroundScheduler 类一起替换
    monkeypatch.setattr(scheduler_mod, "BackgroundScheduler", lambda **kw: fake)
    monkeypatch.setattr(scheduler_mod, "_scheduler", fake)
    scheduler_mod.start_scheduler()
    return scheduler_mod, fake


def test_scheduler_registers_shadow_check(param_env, monkeypatch):
    """红：start_scheduler 注册面现状无 shadow_check 作业——
    SHADOW_CHECK_INTERVAL_SECONDS 零消费。修复后 interval 作业按配置挂载。"""
    _scheduler_mod, fake = _mount_fake_scheduler(monkeypatch)
    job = next((j for j in fake.jobs if j["id"] == "shadow_check"), None)
    assert job is not None, (
        f"察窗到期轮询未注册：现存作业 {[j['id'] for j in fake.jobs]}"
        f"（SHADOW_CHECK_INTERVAL_SECONDS={settings.SHADOW_CHECK_INTERVAL_SECONDS} 零消费）"
    )
    assert job["trigger"] == "interval"
    assert job["kwargs"].get("seconds") == settings.SHADOW_CHECK_INTERVAL_SECONDS
    assert job["kwargs"].get("replace_existing") is True
    from lantai.parameters.runtime import check_shadow_due

    assert job["fn"] is check_shadow_due, "注册的可调用必须是真 check_shadow_due"


def test_shadow_check_job_end_to_end_adjudicates(param_env, monkeypatch):
    """红（注册缺位）+ 真跑：经调度器注册表取出作业函数（真 check_shadow_due），
    对超过观察期的 observing 窗跑一个周期——必须产出到期裁决（状态离开
    observing），promote/hold/rollback 三态均算裁决（空指标保守 hold 是
    evaluate_window 的合法判定）。"""
    from datetime import timedelta

    scheduler_mod, fake = _mount_fake_scheduler(monkeypatch)
    job = next((j for j in fake.jobs if j["id"] == "shadow_check"), None)
    assert job is not None, "shadow_check 未注册，端到端臂无从谈起"
    run_one_cycle = job["fn"]

    sf, _engine = param_env
    from lantai.core.time import utcnow
    from lantai.parameters.runtime import open_shadow
    from lantai.parameters.trust_models import ShadowWindow

    w = open_shadow("rev_t43", {"RETRIEVAL_W_VECTOR": 0.7}, observe_days=1)
    with sf() as s:
        got = s.get(ShadowWindow, w.id)
        got.check_deadline = utcnow() - timedelta(days=1)
        s.add(got)
        s.commit()

    results = run_one_cycle()
    assert len(results) == 1, f"到期窗应被轮询评估；实得 {results}"
    assert results[0]["window_id"] == w.id
    assert results[0]["verdict"] in ("promote", "hold", "rollback")
    with sf() as s:
        updated = s.get(ShadowWindow, w.id)
        assert updated.status != "observing", "到期窗必须离开 observing（裁决已产出）"
        assert updated.finished_at is not None
    # 调度器模块未被臂污染（注册表为壳所替换，teardown 由 monkeypatch 自还）
    assert scheduler_mod._scheduler is not None
