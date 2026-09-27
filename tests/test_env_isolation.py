"""测试环境对齐冒烟——`tests/conftest.py` 的两个 autouse fixture 直调验证（不 mock）。

背景：本地 `.env` 的 `LANTAI_HOME` 指向真实开发库，而 `lantai/storage/db.py` 的
`engine` 在 import 时就按 `settings.DATABASE_URL` 建好——测试因此绑定宿主真实
SQLite/ChromaDB。CI 是干净目录，本地不是，这个差异正是「本地绿 CI 红」的
根因家族（见 `.scratch/test-env-parity/`）。

本文件用真实构造最小输入直调被测行为：读测试进程内的 engine/settings 状态，
真实往隔离库写一行再查回，以及**真实调用一次产品代码的 LLM 入口**验证它拿到的是
替身而非真连 API。不 mock 任何内部逻辑——隔离/替身是否生效是**可观测的事实状态**，
mock 掉就什么都验证不了了。
"""

import lantai.storage.db as db_module
from lantai.core.settings import settings
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from tests.conftest import _FAKE_EMBED_DIM


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


# ── 外部 LLM 调用替身（票 01 方案甲）──────────────────────────
# ⚠ 判据不能用「is 某个函数对象」：tests/conftest.py 会被 import 两次（pytest 的
# rootdir 加载 + 测试内 `from tests.conftest import ...`），两个模块对象的同名
# 函数不是同一个。所以下面用**行为判据**：调用它，看返回是不是替身特征值。


def test_source_embed_returns_stub_vector():
    """源 embed 返回替身向量（1024 维、值 0.1）——真连 API 不可能瞬时返回这个。"""
    import lantai.llm.client as client_mod

    vecs = client_mod.embed(["探针文本"])
    assert vecs == [[0.1] * 1024], "源 embed 未被替身（或维度不对）"


def test_source_chat_json_returns_empty_dict():
    """源 chat_json 返回空 dict——替身的特征值。"""
    import lantai.llm.client as client_mod

    assert client_mod.chat_json("sys", "user") == {}, "源 chat_json 未被替身"


def test_module_level_bindings_return_stub():
    """全部模块级 import 绑定都走替身——方案甲的核心（from X import 各存一份）。

    逐个真实调用（不 mock），拿不到替身特征值即是逃逸——那种绑定在 CI 上会
    真连网（假 key → AuthenticationError → 走降级分支）。
    """
    import importlib

    from tests.conftest import _LLM_MODULE_BINDINGS

    escaped = []
    for mod_name in _LLM_MODULE_BINDINGS:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "embed"):
            try:
                if mod.embed(["探针"]) != [[0.1] * 1024]:
                    escaped.append(f"{mod_name}.embed")
            except Exception as e:  # noqa: BLE001
                escaped.append(f"{mod_name}.embed raised {type(e).__name__}")
        if hasattr(mod, "chat_json"):
            try:
                if mod.chat_json("sys", "user") != {}:
                    escaped.append(f"{mod_name}.chat_json")
            except Exception as e:  # noqa: BLE001
                escaped.append(f"{mod_name}.chat_json raised {type(e).__name__}")
    assert not escaped, f"这些绑定逃过替身（CI 上会真连网）: {escaped}"


def test_stub_dimension_matches_real_model():
    """替身维度 = 真实 EMBED_MODEL 维度（1024），否则调用点会被维度检查提前踢进 except。"""
    assert _FAKE_EMBED_DIM == 1024
