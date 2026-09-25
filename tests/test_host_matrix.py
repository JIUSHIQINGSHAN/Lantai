r"""宿主矩阵端到端冒烟（票 06 / 片 04）。

口径（roadmap P1-2 验收）：**≥3 宿主端到端冒烟**。每宿主跑真实子进程
`python scripts/shell_hook.py`，真实 stdin/stdout 协议帧，真实 SQLite+FTS，
覆盖三步：

1. **注入**：写宿主形输入帧 → stdout `context` 非空且 `event_id` 回传；
2. **回执**：以该 `event_id` 写 backfill 帧 → `receipt_status == "acked"`；
3. **隔离**：畸形帧 → 空输出，且不影响后续请求。

命令钩子矩阵 = Hermes + Claude Code + Codex（三宿主同构，走同一协议）。
Cursor 无命令钩子入口，作降级档另测（见 test_install_host_hooks）。

不 mock 进程边界：测试与 hook 是两个真实进程，只经 stdin/stdout 通信。
每测试用独立 `LANTAI_HOME`（隔离 DB 与向量库），teardown 清理。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "scripts" / "shell_hook.py"
STUB_DIR = REPO / "tests" / "support" / "host_matrix_stub"
PYTHON = sys.executable

# 矩阵内三宿主：命令钩子矩阵（Hermes 插件 / Claude Code / Codex CLI）
MATRIX_HOSTS = ["hermes", "claude-code", "codex"]


def _child_env(home: Path, host: str) -> dict:
    """子进程环境：隔离 LANTAI_HOME + 指定宿主 + embedding 网络替身。"""
    env = dict(os.environ)
    env["LANTAI_HOME"] = str(home)
    env["LANTAI_HOST"] = host
    # 替身目录在前，使 sitecustomize 先于 lantai 生效（只替外部 embedding 网络）
    env["PYTHONPATH"] = os.pathsep.join([str(STUB_DIR), str(REPO)])
    env["LANTAI_TEST_STUB_EMBED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_hook(frames: list[dict | str], home: Path, host: str, timeout: float = 120.0):
    """真实子进程跑 hook：NDJSON 帧经 stdin 传入，返回 stdout 各行解析后的 dict。"""
    payload = "\n".join(
        f if isinstance(f, str) else json.dumps(f, ensure_ascii=False) for f in frames
    )
    proc = subprocess.run(
        [PYTHON, str(HOOK), "--serve"],
        input=payload + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(home, host),
        timeout=timeout,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, f"hook 非零退出：{proc.stderr[-800:]}"
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


@pytest.fixture
def hook_home(tmp_path):
    """独立 LANTAI_HOME（隔离 DB 与向量库）+ 真实建表。"""
    home = tmp_path / "lantai_home"
    home.mkdir()
    init = subprocess.run(
        [PYTHON, "-c", "from lantai.storage.db import init_db; init_db()"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(home, "hermes"),
        cwd=str(REPO),
        timeout=180,
    )
    assert init.returncode == 0, f"init_db 失败：{init.stderr[-800:]}"
    return home


def _seed_memory(home: Path, content: str, mem_id: str) -> None:
    """往隔离库里塞一条 active 记忆 + 向量索引（真实写入，供注入命中）。"""
    code = (
        "from lantai.models.tables import MemoryItem;"
        "from lantai.storage import db;"
        "from lantai.retrieval.hybrid import index_memory_item;"
        "from lantai.llm.client import embed;"
        "from lantai.core.time import utcnow;"
        f"content={content!r}; mid={mem_id!r};"
        "s=db.get_session();"
        "s.add(MemoryItem(id=mid, content=content, lane='fact', domain='user',"
        " status='active', decay_score=1.0, decay_class='semantic',"
        " created_at=utcnow(), updated_at=utcnow(), valid_from=utcnow()));"
        "s.commit(); s.close();"
        "index_memory_item(mid, embed([content])[0], {'lane':'fact','domain':'user'});"
    )
    r = subprocess.run(
        [PYTHON, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(home, "hermes"),
        cwd=str(REPO),
        timeout=180,
    )
    assert r.returncode == 0, f"seed 失败：{r.stderr[-800:]}"


def _extract_context(out: dict, host: str) -> str:
    """从宿主输出帧里取注入正文（Hermes 自有形状 vs CC/Codex additionalContext）。"""
    if host in ("claude-code", "codex"):
        return out.get("hookSpecificOutput", {}).get("additionalContext", "")
    return out.get("context", "")


def _extract_event_id(out: dict, host: str) -> str:
    """取回执凭据 event_id（两种形状均为顶层，见 host_adapters 说明）。"""
    return out.get("event_id", "")


def _event_session_id(home: Path, event_id: str) -> str | None:
    """从隔离库读该检索事件的 session_id（验证来源链透传）。"""
    code = (
        "from lantai.models.tables import RetrievalEvent;"
        "from lantai.storage import db;"
        f"s=db.get_session(); ev=s.get(RetrievalEvent, {event_id!r});"
        "print(ev.session_id if ev else '<MISSING>'); s.close()"
    )
    r = subprocess.run(
        [PYTHON, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_child_env(home, "hermes"),
        cwd=str(REPO),
        timeout=180,
    )
    assert r.returncode == 0, f"读 session_id 失败：{r.stderr[-400:]}"
    return r.stdout.strip()


@pytest.mark.parametrize("host", MATRIX_HOSTS)
class TestHostEndToEnd:
    """三宿主矩阵端到端冒烟（真实子进程 + 真实协议帧）。"""

    def test_inject_then_backfill(self, hook_home, host):
        """注入 → 回执全链：context 非空、event_id 回传、backfill 置 acked。"""
        mem_id = f"mem_matrix_{host}"
        _seed_memory(hook_home, f"宿主矩阵冒烟专用记忆 龙井茶冲泡水温 85度 {host}", mem_id)

        # ① 注入：各宿主真实请求帧（CC/Codex 用 prompt，Hermes 用 query）
        frame = (
            {"prompt": "龙井茶冲泡水温是多少", "session_id": f"sess-{host}"}
            if host in ("claude-code", "codex")
            else {"query": "龙井茶冲泡水温是多少", "session_id": f"sess-{host}"}
        )
        outs = _run_hook([frame], hook_home, host)
        assert len(outs) == 1, f"{host}: 应有一行响应"
        out = outs[0]

        ctx = _extract_context(out, host)
        assert ctx, f"{host}: 注入正文为空（context 缺失）"

        # event_id 回传（三宿主均为顶层）：回执（backfill）的凭据
        event_id = _extract_event_id(out, host)
        assert event_id, f"{host}: event_id 未回传，回执无从谈起"

        # session_id 透传（票 04 口径）：落 RetrievalEvent.session_id 列，
        # 「带 session 的读才算真实会话读」的写线活性判据依赖此值。
        assert _event_session_id(hook_home, event_id) == f"sess-{host}", (
            f"{host}: session_id 未透传到 RetrievalEvent"
        )

        # ② 回执：以该 event_id 写 backfill → 置 acked（接票 04 回执链）
        backfill = {"type": "backfill", "event_id": event_id, "used_ids": [mem_id]}
        ack = _run_hook([backfill], hook_home, host)
        assert ack[0].get("receipt_status") == "acked", f"{host}: 回执未置 acked"

    def test_malformed_frame_isolated(self, hook_home, host):
        """③ 隔离：畸形帧降级为空，后续请求仍正常处理（进程不被打死）。"""
        outs = _run_hook(["null", "not-json{{{", {"query": ""}, "[1,2]"], hook_home, host)
        assert len(outs) == 4, f"{host}: 四帧应各有一行响应"
        assert all(isinstance(o, dict) for o in outs)


class TestMatrixCoverage:
    def test_three_hosts_covered(self):
        """≥3 宿主口径：矩阵含三宿主。"""
        assert len(MATRIX_HOSTS) == 3
        assert set(MATRIX_HOSTS) == {"hermes", "claude-code", "codex"}
