"""测试环境对齐冒烟——`tests/conftest.py` 的隔离 fixture 直调验证（不 mock）。

背景：本地 `.env` 的 `LANTAI_HOME` 指向真实开发库，而 `lantai/storage/db.py` 的
`engine` 在 import 时就按 `settings.DATABASE_URL` 建好——测试因此绑定宿主真实
SQLite/ChromaDB。CI 是干净目录，本地不是，这个差异正是「本地绿 CI 红」的
根因家族（见 `.scratch/test-env-parity/`）。

本文件用真实构造最小输入直调被测行为：读测试进程内的 engine/settings 状态，
以及真实往隔离库写一行再查回。不 mock 任何内部逻辑——隔离是否生效是**可观测的
事实状态**，mock 掉就什么都验证不了了。
"""

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.models.tables import MemoryItem
from lantai.core.time import utcnow


def _is_isolated(path: str) -> bool:
    """落在 pytest 临时目录即视为隔离（pytest 的 tmp 根目录名固定为 pytest-of-*）。"""
    return "pytest-of-" in path and "remembrance-data" not in path


def test_engine_points_to_isolated_dir():
    """db.engine 不指向宿主真实库。"""
    url = str(db_module.engine.url)
    assert "remembrance-data" not in url, f"engine 仍绑真实库：{url}"
    assert _is_isolated(url), f"engine 未落 pytest 临时目录：{url}"


def test_settings_paths_are_isolated():
    """settings 的三个路径项（DATABASE_URL / CHROMADB_PATH / LANTAI_HOME）全部隔离。"""
    for name in ("DATABASE_URL", "CHROMADB_PATH", "LANTAI_HOME"):
        value = str(getattr(settings, name))
        assert "remembrance-data" not in value, f"{name} 仍指真实库：{value}"
        assert _is_isolated(value), f"{name} 未落 pytest 临时目录：{value}"


def test_isolated_db_is_writable_and_queryable():
    """隔离库真能写真能查（证明不是指向了个打不开/空的路径）。"""
    from sqlmodel import Session, select

    with Session(db_module.engine) as s:
        mem = MemoryItem(
            id="test_isolation_probe",
            memory_type="general",
            key="隔离探针",
            content="隔离库可写可查探针",
            lane="general",
            status="active",
            importance=0.5,
            use_count=0,
            decay_score=1.0,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        s.add(mem)
        s.commit()
        got = s.exec(select(MemoryItem).where(MemoryItem.id == "test_isolation_probe")).first()
        assert got is not None
        assert got.content == "隔离库可写可查探针"
        # 清理：探针行不留（隔离库虽随 tmp 消失，但不留脏行是好习惯）
        s.delete(got)
        s.commit()


def test_get_session_follows_isolated_engine():
    """get_session() 走的是隔离后的 engine（它引用模块级 engine 名字，须跟随）。"""
    from sqlmodel import Session

    with db_module.get_session() as s:
        assert s.bind is db_module.engine, "get_session 未跟随隔离 engine"
