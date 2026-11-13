"""票 85：三个文档写面（digest/offload/wiki）默认目录必须归一 LANTAI_HOME 数据目录推导。

病灶：三面默认目录都按 ``Path(__file__)`` 安装前缀推导（``lantai/workers/digest_worker.py:26``、
``lantai/services/offload_service.py:13``、``lantai/services/wiki_service.py:26``），无视 settings
已有的 LANTAI_HOME 数据目录推导（db/chroma 都落它，``lantai/core/settings.py:528``）。wheel/容器
形态下安装前缀 root 只读、进程非 root（Dockerfile builder→pip install→USER appuser）：每日
digest job PermissionError 必炸、GET /digest/today 500、offload 落盘失败静默降级（零日志）。

不 mock 被测推导：直调真实目录助手与真实写函数（tmp_path 当 LANTAI_HOME，真实文件副作用）；
仓库形态回归臂锁「LANTAI_HOME 未设 = 仓库根 docs/memory-*」零变化；offload 降级臂用
「输出目录指向已存在的文件」制造真 FileExistsError，不用 mock。
"""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import lantai.storage.db as db_module
from lantai.core.settings import _REPO_ROOT, settings

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    """LANTAI_HOME 指向临时数据目录 + 三个 OUTPUT_DIR 开关清空（走默认推导）。"""
    monkeypatch.setattr(settings, "LANTAI_HOME", str(tmp_path))
    monkeypatch.setattr(settings, "DIGEST_OUTPUT_DIR", "")
    monkeypatch.setattr(settings, "OFFLOAD_OUTPUT_DIR", "")
    monkeypatch.setattr(settings, "WIKI_OUTPUT_DIR", "")
    return tmp_path


@pytest.fixture()
def isolated_db(monkeypatch):
    """内存 SQLite 真实建表 + patch db.get_session（真实 SQL 链，不 mock 被测逻辑）。"""
    import lantai.models.tables  # noqa: F401  注册全部表

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    monkeypatch.setattr(db_module, "get_session", session_factory)
    return session_factory


class TestThreeWriteFacesFollowDataHome:
    """三写面目录推导：LANTAI_HOME 设了就必须落在数据目录内（wheel/容器形态不逃逸安装前缀）。"""

    def test_digest_dir_follows_lantai_home(self, data_home):
        from lantai.workers.digest_worker import _digest_dir

        d = _digest_dir()
        assert d.is_relative_to(data_home), f"digest 目录 {d} 逃逸 LANTAI_HOME {data_home}"

    def test_offload_dir_follows_lantai_home(self, data_home):
        from lantai.services.offload_service import offload_dir

        d = offload_dir()
        assert d.is_relative_to(data_home), f"offload 目录 {d} 逃逸 LANTAI_HOME {data_home}"

    def test_wiki_dir_follows_lantai_home(self, data_home):
        from lantai.services.wiki_service import wiki_dir

        d = wiki_dir()
        assert d.is_relative_to(data_home), f"wiki 目录 {d} 逃逸 LANTAI_HOME {data_home}"


class TestRepoFormUnchanged:
    """仓库形态回归锁：LANTAI_HOME 未设时 = 仓库根 docs/memory-*（editable 形态行为零变化）。"""

    @pytest.mark.parametrize(
        ("getter", "sub"),
        [
            ("lantai.workers.digest_worker:_digest_dir", "memory-digest"),
            ("lantai.services.offload_service:offload_dir", "memory-offload"),
            ("lantai.services.wiki_service:wiki_dir", "memory-wiki"),
        ],
    )
    def test_default_dir_still_repo_docs_when_lantai_home_unset(self, monkeypatch, getter, sub):
        monkeypatch.setattr(settings, "LANTAI_HOME", "")
        mod_name, attr = getter.split(":")
        mod = __import__(mod_name, fromlist=[attr])
        assert getattr(mod, attr)() == _REPO_ROOT / "docs" / sub


class TestExplicitOutputDirStillWins:
    """显式 OUTPUT_DIR 优先于 data_home（既有契约，顺手锁住）。"""

    def test_explicit_digest_output_dir_beats_data_home(self, data_home, tmp_path, monkeypatch):
        from lantai.workers.digest_worker import _digest_dir

        explicit = tmp_path / "elsewhere"
        monkeypatch.setattr(settings, "DIGEST_OUTPUT_DIR", str(explicit))
        assert _digest_dir() == explicit


class TestRealWriteSmoke:
    """真写冒烟：LANTAI_HOME 设临时目录，真实写函数产物落在数据目录内。"""

    def test_write_offload_file_lands_in_data_home(self, data_home):
        from lantai.services.offload_service import write_offload_file

        path = write_offload_file("mem_ticket85", "长记忆全文 " * 50)
        assert path.is_relative_to(data_home), f"offload 全文写到了 {path}"
        assert path.read_text(encoding="utf-8").startswith("长记忆全文")

    def test_run_digest_once_writes_report_into_data_home(self, data_home, isolated_db):
        from lantai.workers.digest_worker import _digest_dir, run_digest_once

        res = run_digest_once()
        path = _digest_dir() / f"{res['day']}.md"
        assert path.is_relative_to(data_home), f"digest 报告写到了 {path}"
        assert path.exists() and res["path"] == path.name


class TestOffloadDegradeObservable:
    """offload 落盘失败必须留痕（warning），不许静默降级——容器内全文丢失零痕迹就是本病灶放大器。"""

    def test_degrade_logs_warning(self, tmp_path, monkeypatch, caplog):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "shell_hook_ticket85", REPO_ROOT / "scripts" / "shell_hook.py"
        )
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)

        blocker = tmp_path / "blocker.md"
        blocker.write_text("占位", encoding="utf-8")  # 输出目录指向已存在的文件 → mkdir 真炸
        monkeypatch.setattr(settings, "OFFLOAD_OUTPUT_DIR", str(blocker))

        item = SimpleNamespace(
            id="mem_x", content="长记忆全文 " * 50, event_time=None, valid_from=None
        )
        with caplog.at_level(logging.WARNING, logger="lantai"):
            line, summary = hook._format_offload_entry(item, 0.9, 60)

        assert "（已卸载全文）" not in summary  # 降级形态：截断注入，不带卸载后缀
        assert any(
            "offload" in r.message and r.levelno == logging.WARNING for r in caplog.records
        ), "offload 落盘失败被静默吞掉——降级必须留 warning 痕迹"


class TestDocsTruthLock:
    """文档如实锁：daily-digest 的输出口径必须写清两种安装形态（票 85 验收 4）。"""

    def test_daily_digest_doc_states_data_home_semantics(self):
        text = (REPO_ROOT / "docs" / "daily-digest.md").read_text(encoding="utf-8")
        assert "LANTAI_HOME" in text, "daily-digest.md 输出口径未写数据目录语义（wheel 形态失真）"
        assert "数据目录" in text, "daily-digest.md 输出口径未写「数据目录」一词（两形态语义回退）"
        assert "DIGEST_OUTPUT_DIR" in text, "daily-digest.md 丢失显式覆盖开关说明（优先级契约回退）"
        assert "宿主本地日" in text and "调度器使用 UTC" in text, (
            "daily-digest.md 必须区分宿主日界与 UTC 调度口径（票 84）"
        )
        assert "夏令时" in text and "尾部" in text, (
            "daily-digest.md 必须说明固定 UTC 调度的夏令时与触发后尾部限制（票 84）"
        )
