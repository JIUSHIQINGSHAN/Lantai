"""票 85：wheel/容器形态下三个文档写面默认目录逃逸 LANTAI_HOME（TDD 红基线）。

病灶（巡检 2026-11-08 立案轮 10）：digest_worker/offload_service/wiki_service 的
默认输出目录由 `Path(__file__).resolve().parent.parent.parent`（安装前缀）推导，
无视 settings 已有的 LANTAI_HOME 数据目录推导——真 wheel 解包探针实测
`INSIDE_INSTALL_PREFIX=True`。发布 Docker 形态（wheel 进 site-packages + 非 root
appuser，Dockerfile:23/:39-40）下安装前缀 root 只读：默认开启的每日 digest job
（DIGEST_ENABLED=True）`write_digest_report → mkdir` 在只读前缀下 PermissionError，
`GET /digest/today` 每次现算 500；offload 落盘失败静默降级（零日志）；wiki
mem:sync 降级。

修复形态（票面拍板）：settings 补 `data_home` 属性单一真源（LANTAI_HOME 优先，
未设回 _REPO_ROOT——editable/仓库形态行为零变化），三写面默认目录改挂它；
显式 `*_OUTPUT_DIR` 优先级保持最高；offload 降级臂补 warning 日志。

测试形状（零 mock 被测链路）：
- 目录推导臂真调三个 dir 函数与 `data_home`（纯求值不写盘）；
- 写冒烟臂真文件系统落盘断言（AGENTS.md 冒烟纪律，tmp 当 LANTAI_HOME）；
- offload 降级臂替身的只是文件系统副作用（write_offload_file 抛
  PermissionError——AGENTS.md 豁免口径），被测的降级分支与日志行为全真。
"""

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from lantai.core.settings import Settings, settings

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "scripts" / "shell_hook.py"


@pytest.fixture()
def home_anchor(monkeypatch, tmp_path):
    """tmp_path 当 LANTAI_HOME + 三写面显式 OUTPUT_DIR 清空（回默认推导臂）。

    monkeypatch 自带 teardown 还原 session 隔离设好的字段。
    """
    home = tmp_path / "lantai-home"
    home.mkdir()
    monkeypatch.setattr(settings, "LANTAI_HOME", str(home))
    monkeypatch.setattr(settings, "DIGEST_OUTPUT_DIR", "")
    monkeypatch.setattr(settings, "OFFLOAD_OUTPUT_DIR", "")
    monkeypatch.setattr(settings, "WIKI_OUTPUT_DIR", "")
    return home


# ── data_home 单一真源 ────────────────────────────────────────────────────────


def test_data_home_property_follows_lantai_home(home_anchor, tmp_path):
    """红臂：settings.data_home 跟随 LANTAI_HOME。现状属性不存在。"""
    assert settings.data_home == tmp_path / "lantai-home"


def test_data_home_falls_back_to_repo_root(monkeypatch):
    """仓库形态零变化锁：LANTAI_HOME 未设时 data_home == _REPO_ROOT（与
    model_post_init 对 db/chroma 的既有推导同语义）。"""
    monkeypatch.setattr(settings, "LANTAI_HOME", "")
    assert settings.data_home == REPO_ROOT


def test_data_home_matches_db_derivation(tmp_path):
    """构造期一致性：data_home 与 model_post_init 给 DATABASE_URL 的推导同源；
    REMEMBRANCE_HOME 旧配置兼容位同样被 data_home 收编。"""
    home = tmp_path / "constructed"
    s = Settings(LANTAI_HOME=str(home))
    assert s.data_home == home
    assert s.DATABASE_URL == f"sqlite:///{home / 'remembrance.db'}"

    legacy = tmp_path / "legacy"
    s2 = Settings(LANTAI_HOME="", REMEMBRANCE_HOME=str(legacy))
    assert s2.data_home == legacy


# ── 三写面目录推导（显式优先 / 默认归数据根）─────────────────────────────────


def test_explicit_output_dirs_still_win(tmp_path, monkeypatch):
    """绿锁（现状即绿）：显式 *_OUTPUT_DIR 优先级最高，修复不得动它。"""
    from lantai.services.offload_service import offload_dir
    from lantai.services.wiki_service import wiki_dir
    from lantai.workers.digest_worker import _digest_dir

    monkeypatch.setattr(settings, "DIGEST_OUTPUT_DIR", str(tmp_path / "dg"))
    monkeypatch.setattr(settings, "OFFLOAD_OUTPUT_DIR", str(tmp_path / "of"))
    monkeypatch.setattr(settings, "WIKI_OUTPUT_DIR", str(tmp_path / "wk"))

    assert _digest_dir() == tmp_path / "dg"
    assert offload_dir() == tmp_path / "of"
    assert wiki_dir() == tmp_path / "wk"


def test_default_dirs_follow_lantai_home(home_anchor):
    """红臂主 asserting：三写面默认目录 = 数据根 docs/*（现状钉安装前缀）。

    wheel/容器形态下这就是「报告写进 root 只读的 site-packages」病灶本体。
    """
    from lantai.services.offload_service import offload_dir
    from lantai.services.wiki_service import wiki_dir
    from lantai.workers.digest_worker import _digest_dir

    home = home_anchor
    assert _digest_dir() == home / "docs" / "memory-digest", (
        f"digest 默认目录应落 LANTAI_HOME，实测 {_digest_dir()}"
    )
    assert offload_dir() == home / "docs" / "memory-offload", (
        f"offload 默认目录应落 LANTAI_HOME，实测 {offload_dir()}"
    )
    assert wiki_dir() == home / "docs" / "memory-wiki", (
        f"wiki 默认目录应落 LANTAI_HOME，实测 {wiki_dir()}"
    )


# ── 写冒烟（真文件系统，不 mock）─────────────────────────────────────────────


def test_write_digest_report_lands_in_lantai_home(home_anchor):
    """红臂：write_digest_report 真写，产物必须在 LANTAI_HOME 内
    （票面验收 1 的不 mock 冒烟形态；探针 INSIDE_LANTAI_HOME=True 同判据）。
    """
    from lantai.workers.digest_worker import write_digest_report

    stats = {
        "day": "2026-11-13-probe85",
        "memories": {"new": 0, "modified": 0, "total": 0},
        "pending": {"total": 0, "new_today": 0},
        "archived": {"ttl": 0, "created_today": 0},
        "retrieval": {"total": 0, "zero_result": 0, "noise": 0, "avg_latency_ms": 0},
        "reflection": None,
        "health": None,
    }
    path = write_digest_report(stats)
    expected = home_anchor / "docs" / "memory-digest" / "2026-11-13-probe85.md"
    assert path == expected, f"报告落点应随数据根，实测 {path}"
    assert path.exists()
    assert path.read_text(encoding="utf-8").startswith("# 记忆日报")


def test_write_offload_file_lands_in_lantai_home(home_anchor):
    """红臂：write_offload_file 真写，全文必须落 LANTAI_HOME/docs/memory-offload。"""
    from lantai.services.offload_service import write_offload_file

    path = write_offload_file("mem-85", "超长记忆正文")
    expected = home_anchor / "docs" / "memory-offload" / "mem-85.md"
    assert path == expected, f"卸载全文落点应随数据根，实测 {path}"
    assert path.read_text(encoding="utf-8") == "超长记忆正文"


@pytest.fixture()
def mem_db(monkeypatch):
    """内存 SQLite 真实建表（wiki 全链路；test_wiki.py 同款形态）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    init_fts(engine.raw_connection())

    import lantai.storage.db as dbm

    monkeypatch.setattr(dbm, "get_session", lambda: Session(engine))
    yield engine


def test_run_wiki_update_once_lands_in_lantai_home(home_anchor, mem_db):
    """红臂：run_wiki_update_once 真跑（内存库零数据也须落 index/overview），
    产物目录必须随 LANTAI_HOME（mem:sync wiki 面的容器形态病灶）。"""
    from lantai.services.wiki_service import run_wiki_update_once

    res = run_wiki_update_once(overview_llm=False)
    expected = home_anchor / "docs" / "memory-wiki"
    assert res["ok"] is True
    assert res["dir"] == str(expected), f"wiki 目录应随数据根，实测 {res['dir']}"
    assert (expected / "index.md").exists()
    assert (expected / "overview.md").exists()


# ── offload 降级可观测（票面 P3 无痕病灶）────────────────────────────────────


def _load_shell_hook():
    spec = importlib.util.spec_from_file_location("shell_hook_t85", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_offload_degrade_failure_logs_warning(caplog, monkeypatch):
    """红臂：落盘失败（容器只读前缀的真实形态）降级为截断注入时必须有
    warning 留痕——现状零日志，长记忆全文丢失无痕。

    替身的只是文件系统副作用（write_offload_file 抛 PermissionError，
    AGENTS.md 豁免口径）；降级分支、注入形态、日志行为全真。
    """
    mod = _load_shell_hook()

    def _boom(memory_id, content):
        raise PermissionError(f"[Errno 13] Permission denied: {memory_id}")

    monkeypatch.setattr(mod, "write_offload_file", _boom)
    item = SimpleNamespace(
        id="m85", content="很长" * 60, event_time=None, valid_from=None, created_at=None
    )

    with caplog.at_level(logging.WARNING, logger="lantai"):
        line, injected = mod._format_offload_entry(item, 0.9, 20)

    # 降级产物 = 截断注入形态（既有 fail-soft 语义保持）
    assert mod._RECALL_TRUNCATION_SUFFIX in line
    assert "全文:" not in line
    assert injected
    # 新增：降级必须可观测
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "offload 落盘失败降级未留 warning 日志（静默无痕病灶）"
    assert any("offload" in r.getMessage().lower() for r in warnings)
