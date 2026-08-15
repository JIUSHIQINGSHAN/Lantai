"""Hermes lantai-hook 插件测试（v0.5 对话自动写入）。

插件源码在仓库 hermes-plugin/lantai-hook/（部署脚本同步到 Hermes home）。
测试不依赖 Hermes 进程——register(ctx) 用假 ctx；serve 子进程交互全部 mock。
"""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_PATH = Path(__file__).parent.parent / "hermes-plugin" / "lantai-hook" / "__init__.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("remembrance_plugin", PLUGIN_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._session_buffers = {}
    m._proc = None
    m._proc_ready = False
    m._checkpoint_injected = set()
    return m


class TestSessionBuffer:
    """会话缓冲：pre_llm_call 累积 user_message，on_session_end flush"""

    def test_buffer_accumulates_and_flushes(self, mod):
        mod._buffer_turn("sess_1", "记住：明天下午3点开会")
        mod._buffer_turn("sess_1", "我最近喜欢用 Rust 写 CLI")
        assert mod._session_buffers["sess_1"] == [
            "记住：明天下午3点开会", "我最近喜欢用 Rust 写 CLI"]
        with patch.object(mod, "_call_dialogue") as m:
            mod._flush_session("sess_1")
        assert m.call_count == 2
        assert "sess_1" not in mod._session_buffers

    def test_buffer_ignores_empty(self, mod):
        mod._buffer_turn("sess_1", "   ")
        mod._buffer_turn("", "内容")
        assert mod._session_buffers == {}

    def test_buffer_cap_trims_oldest(self, mod):
        mod._SESSION_BUFFER_MAX_MSGS = 3
        for i in range(5):
            mod._buffer_turn("sess_1", f"消息{i}")
        assert len(mod._session_buffers["sess_1"]) == 3
        assert mod._session_buffers["sess_1"][0] == "消息2"

    def test_flush_missing_session_noop(self, mod):
        with patch.object(mod, "_call_dialogue") as m:
            mod._flush_session("sess_nope")
        m.assert_not_called()


class TestCallbacks:
    """pre_llm_call 缓冲 + on_session_end flush"""

    def test_pre_llm_call_buffers_user_message(self, mod):
        with patch.object(mod, "_call_hook", return_value=None), \
             patch.object(mod, "_call_checkpoint", return_value=None):
            mod._on_pre_llm_call(user_message="我最近在学知识图谱",
                                 session_id="sess_1")
        assert mod._session_buffers["sess_1"] == ["我最近在学知识图谱"]

    def test_on_session_end_flushes(self, mod):
        mod._buffer_turn("sess_1", "记住：明天开会")
        with patch.object(mod, "_call_dialogue") as m, \
             patch.object(mod, "_call_checkpoint_write") as cw:
            mod._on_session_end(session_id="sess_1", completed=True)
        m.assert_called_once_with("记住：明天开会")
        # ADR-0022：落底本块（在做=末条消息）
        cw.assert_called_once()
        blocks = cw.call_args[0][1]
        assert blocks["cp_active_intent"] == "记住：明天开会"
        assert "sess_1" not in mod._session_buffers

    def test_register_hooks(self, mod):
        hooks = {}

        class _FakeCtx:
            def register_hook(self, name, cb):
                hooks[name] = cb

        mod.register(_FakeCtx())
        assert "pre_llm_call" in hooks
        assert "on_session_end" in hooks
        assert callable(hooks["on_session_end"])

    def test_plugin_yaml_declares_hooks(self):
        yaml_path = PLUGIN_PATH.parent / "plugin.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        assert "pre_llm_call" in text
        assert "on_session_end" in text


class TestCheckpointInjection:
    """底本闭环（ADR-0022）：首轮注入一次 + on_session_end 落五段块。"""

    def test_build_session_blocks_pure(self, mod):
        """纯函数（不 mock）：在做=末条；下一步/决策/待办命中才填；工作区恒空。"""
        blocks = mod.build_session_blocks(["闲聊A", "接下来把接口文档写完"])
        assert blocks["cp_active_intent"] == "接下来把接口文档写完"
        assert blocks["cp_next_action"] == "接下来把接口文档写完"
        assert "cp_key_decisions" not in blocks
        assert "cp_current_work" not in blocks

        blocks2 = mod.build_session_blocks(["决定了就用 Rust 重写"])
        assert blocks2["cp_key_decisions"] == "决定了就用 Rust 重写"

        blocks3 = mod.build_session_blocks(["别忘了周五交周报"])
        assert blocks3["cp_open_notes"] == "别忘了周五交周报"

        assert mod.build_session_blocks([]) == {}
        assert mod.build_session_blocks(["  "]) == {}

    def test_first_turn_injects_checkpoint_once(self, mod):
        """首轮注入底本并合并检索；同会话第二轮不再注入。"""
        with patch.object(mod, "_call_checkpoint", return_value="[Checkpoint · 上次会话]\n在做: X") as ck, \
             patch.object(mod, "_call_hook", return_value="检索上下文") as hk:
            r1 = mod._on_pre_llm_call(user_message="继续上次的工作",
                                      session_id="sess_ck")
            r2 = mod._on_pre_llm_call(user_message="继续上次的工作",
                                      session_id="sess_ck")
        assert r1["context"].startswith("[Checkpoint · 上次会话]")
        assert "检索上下文" in r1["context"]
        assert ck.call_count == 1
        assert hk.call_count == 2

    def test_first_turn_short_query_still_injects_checkpoint(self, mod):
        """首轮短句无触发词：检索跳过，但底本仍注入（会话续接语义）。"""
        with patch.object(mod, "_call_checkpoint", return_value="[Checkpoint · 上次会话]") as ck, \
             patch.object(mod, "_call_hook", return_value=None) as hk:
            r = mod._on_pre_llm_call(user_message="好", session_id="sess_short")
        assert r == {"context": "[Checkpoint · 上次会话]"}
        ck.assert_called_once()
        hk.assert_not_called()

    def test_on_session_end_writes_blocks(self, mod):
        """会话结束：五段块（含命中句式）落 checkpoint_write。"""
        mod._buffer_turn("sess_e", "接下来把部署脚本补全")
        with patch.object(mod, "_call_checkpoint_write") as cw, \
             patch.object(mod, "_call_dialogue"):
            mod._on_session_end(session_id="sess_e", completed=True)
        cw.assert_called_once()
        sid, blocks = cw.call_args[0]
        assert sid == "sess_e"
        assert blocks["cp_active_intent"] == "接下来把部署脚本补全"
        assert blocks["cp_next_action"] == "接下来把部署脚本补全"


class TestInstallScriptBackup:
    """安装脚本：备份必须落在插件扫描目录之外，避免被 Hermes 加载器当作同名插件扫描到。

    回归背景：v1.0.0 备份留在 plugins/lantai-hook.bak-YYYYMMDD 且 plugin.yaml 同名，
    加载器按 key=name 去重、后扫描者覆盖，导致重启后旧版遮蔽新版被加载。
    """

    @staticmethod
    def _load_script():
        spec = importlib.util.spec_from_file_location(
            "install_hermes_plugin",
            Path(__file__).parent.parent / "scripts" / "install_hermes_plugin.py",
        )
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_deploy_backs_up_outside_scan_dir(self, tmp_path):
        inst = self._load_script()
        plugins = tmp_path / "hermes" / "plugins"
        plugins.mkdir(parents=True)
        old = plugins / "lantai-hook"
        old.mkdir()
        (old / "plugin.yaml").write_text(
            "name: lantai-hook\nversion: 1.0.0\n", encoding="utf-8")
        (old / "__init__.py").write_text("# old\n", encoding="utf-8")

        src = tmp_path / "src"
        src.mkdir()
        (src / "plugin.yaml").write_text(
            "name: lantai-hook\nversion: 1.1.0\n", encoding="utf-8")
        (src / "__init__.py").write_text("# new\n", encoding="utf-8")

        inst.deploy(src, plugins, tmp_path / "hermes" / "plugins-backup")

        backups = list((tmp_path / "hermes" / "plugins-backup").iterdir())
        assert len(backups) == 1
        backup = backups[0]
        assert backup.name.startswith("lantai-hook-")
        # 备份内 manifest 失效化，内容保留
        assert not (backup / "plugin.yaml").exists()
        assert (backup / "plugin.yaml.disabled").read_text(encoding="utf-8") == (
            "name: lantai-hook\nversion: 1.0.0\n")
        assert (backup / "__init__.py").read_text(encoding="utf-8") == "# old\n"
        # 目标目录是新版
        assert (plugins / "lantai-hook" / "plugin.yaml").read_text(
            encoding="utf-8") == "name: lantai-hook\nversion: 1.1.0\n"
        # 自检通过：plugins/ 下同名候选唯一
        assert inst.validate_no_duplicate(plugins)

    def test_loader_winner_is_deployed_dir(self, tmp_path):
        """模拟加载器 winners 去重（top-level key=name、sorted 后扫描者覆盖）：胜者为新版。"""
        import re
        inst = self._load_script()
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        src = tmp_path / "src"
        src.mkdir()
        (src / "plugin.yaml").write_text(
            "name: lantai-hook\nversion: 1.1.0\n", encoding="utf-8")
        (src / "__init__.py").write_text("# new\n", encoding="utf-8")

        inst.deploy(src, plugins, tmp_path / "plugins-backup")

        winners = {}
        for child in sorted(plugins.iterdir()):
            if not child.is_dir():
                continue
            mf = child / "plugin.yaml"
            if mf.exists():
                name = re.search(
                    r"(?m)^name\s*:\s*(\S+)", mf.read_text(encoding="utf-8")).group(1)
                winners[name] = child
        assert winners["lantai-hook"] == plugins / "lantai-hook"

    def test_backup_inside_scan_dir_shadows_new(self, tmp_path):
        """回归：旧方案的备份留在 plugins/ 内且同名 → 自检必须拦截。"""
        inst = self._load_script()
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        (plugins / "lantai-hook").mkdir()
        (plugins / "lantai-hook" / "plugin.yaml").write_text(
            "name: lantai-hook\nversion: 1.1.0\n", encoding="utf-8")
        (plugins / "lantai-hook.bak-20260810").mkdir()
        (plugins / "lantai-hook.bak-20260810" / "plugin.yaml").write_text(
            "name: lantai-hook\nversion: 1.0.0\n", encoding="utf-8")
        assert not inst.validate_no_duplicate(plugins)
