"""插件通道放行零命中 event_id 帧（v0.68 票 74 / 巡检 2026-11-07 0700）。

病灶：`_call_hook` 只放行带 `context` 的响应（:226-228），零命中轮
（v0.68 票 01 起 shell_hook 零命中分支带 event_id、无常驻画像时无 context）
在 serve 模式被整体丢弃——`_on_pre_llm_call` 的挂载点到不了，被否决时
`retrieval_veto` 帧构造不出来（ADR-0084 否决链断口；票 10 的 missed 抹文
只是留存缓解，不恢复 vetoed 语义）。

测试形态：test 1 是**真子进程冒烟**（helper 进程真实应答一帧，不 mock）；
test 2/3 沿用 test_plugin_receipt_defer 惯例 patch 进程边界（`_call_hook`
等 `_call_*` 是子进程 I/O 面，非被测函数内部逻辑）。
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"
BLOCKED = "blocked_by_plugin_pre_llm_call"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("lantai_hook_zerohit", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._session_turns = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    return m


class TestCallHookPassthrough:
    def test_eventid_only_frame_reaches_caller(self, mod):
        """真子进程应答 `{"event_id": ...}`（无 context）——`_call_hook` 必须放行。

        零命中 + 无常驻画像部署的真实 serve 响应形状（探针
        `.scratch/_v068_probe_r9_zero_hit_eventid_drop.py` 实锤）；修复前
        `data.get("context")` 过滤把它丢成 None，否决链够不着 event_id。
        """
        helper = (
            "import json,sys;"
            "sys.stdout.write(json.dumps({'event_id':'rev-z1'})+chr(10));"
            "sys.stdout.flush()"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", helper],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        mod._proc = proc
        mod._proc_ready = True
        try:
            data = mod._call_hook("我对什么过敏？", session_id="s-z1")
        finally:
            proc.kill()
            proc.wait()
        assert data == {"event_id": "rev-z1"}

    def test_empty_frame_still_none(self, mod):
        """空帧/无元数据帧维持 None（不注入、不挂载）——放行条件只放宽到 event_id。"""
        helper = "import json,sys;sys.stdout.write(json.dumps({})+chr(10));sys.stdout.flush()"
        proc = subprocess.Popen(
            [sys.executable, "-c", helper],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        mod._proc = proc
        mod._proc_ready = True
        try:
            data = mod._call_hook("q", session_id="s-z2")
        finally:
            proc.kill()
            proc.wait()
        assert data is None


class TestZeroHitVetoChainThroughPlugin:
    def test_zero_hit_eventid_mounted_and_veto_sent(self, mod):
        """零命中帧（只带 event_id）→ 挂载到缓冲条目 → 被否决轮发 veto 帧不回执。"""
        with (
            patch.object(mod, "_call_hook", return_value={"event_id": "rev-z9"}),
            patch.object(mod, "_call_checkpoint", return_value=None),
            patch.object(mod, "_call_backfill") as bf,
        ):
            out = mod._on_pre_llm_call(user_message="我对什么过敏？", session_id="s1", turn_id="t9")
        # 无 context 无底本 → 不注入（None），但 event_id 已挂载
        assert out is None
        bf.assert_not_called()
        entries = mod._session_buffers["s1"]
        assert len(entries) == 1
        assert entries[0]["event_id"] == "rev-z9"

        with (
            patch.object(mod, "_call_dialogue") as dlg,
            patch.object(mod, "_call_checkpoint_write") as cw,
            patch.object(mod, "_call_backfill") as bf2,
            patch.object(mod, "_call_veto") as vt,
        ):
            mod._on_session_end(session_id="s1", turn_id="t9", turn_exit_reason=BLOCKED)
        vt.assert_called_once_with("rev-z9")
        dlg.assert_not_called()
        bf2.assert_not_called()
        cw.assert_not_called()

    def test_zero_hit_normal_end_no_receipt_no_veto(self, mod):
        """零命中未否决轮：不回执（零 used_ids 无可回执）、不发 veto、照常写对话。"""
        with (
            patch.object(mod, "_call_hook", return_value={"event_id": "rev-z8"}),
            patch.object(mod, "_call_checkpoint", return_value=None),
            patch.object(mod, "_call_backfill"),
        ):
            mod._on_pre_llm_call(user_message="我对什么过敏？", session_id="s2", turn_id="t8")
        with (
            patch.object(mod, "_call_dialogue") as dlg,
            patch.object(mod, "_call_checkpoint_write"),
            patch.object(mod, "_call_backfill") as bf2,
            patch.object(mod, "_call_veto") as vt,
        ):
            mod._on_session_end(session_id="s2", turn_id="t8")
        vt.assert_not_called()
        bf2.assert_not_called()
        dlg.assert_called_once()
        args, kwargs = dlg.call_args
        assert args[0] == "我对什么过敏？"

    def test_zero_hit_no_persona_no_empty_context_injection(self, mod):
        """修复的空注入守卫：event_id 帧无 context、无底本时返回 None 而非 `{"context": ""}`。"""
        with (
            patch.object(mod, "_call_hook", return_value={"event_id": "rev-z7"}),
            patch.object(mod, "_call_checkpoint", return_value=None),
        ):
            out = mod._on_pre_llm_call(user_message="我对什么过敏？", session_id="s3", turn_id="t7")
        assert out is None
