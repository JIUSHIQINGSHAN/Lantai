"""票 77 红测：pre_compress 死接线删除案——模块/测试下线 + 引用面清理锁定。

病灶（工单锚点，HEAD=ce8758d9 现场复核）：
1. lantai/integrations/pre_compress.py:38-61 flush_before_compress 全仓零生产
   调用（scripts/shell_hook.py 只 import host_adapters/host_protocol；MCP/REST/
   调度器四装配面均无触发点）——模块 docstring「压缩前抢救」与 db.py:23-24
   busy_timeout 注释理由（「pre_compress daemon 线程并发写库」）都假设它活着，
   两处文档名实不符误导维护者（busy_timeout 参数本体对其余并发写路径仍有
   价值，删理由不删参数）。
2. 拍板：删除案——接线需宿主协议新增动作帧（跨仓协调成本，P3 不撑）；模块
   自引入零生产调用无用户依赖；死代码 + 误导文档是纯负债。
3. 活契约保全：tests/test_pre_compress.py 里 TestMetadataBounds（AddMemoryReq.
   metadata 有界校验，security review 防线）与 metadata→RawDocument.meta 落库
   测试是活契约覆盖，迁移到本文件不陪葬。

接缝（票面关闭条件「删除案」）：
- arm1 死代码臂 @ import + 全仓源码扫：模块/旧测试不存在 + lantai/tests/scripts
  三目录零残留（test_dead_code_removal.py 模块级锁形态，票 72 arm2 同构）。
- arm2 注释清理臂 @ db.py 源码：busy_timeout 注释理由不再引用 pre_compress，
  参数本体保留（删理由不删参数）。
- arm3 活契约保全臂 @ 直调真 pydantic 校验：metadata 有界校验五防线不因死
  模块删除而失守。
- arm4 消费面注释臂 @ schemas.py 源码：AddMemoryReq.metadata 注释里的
  source=pre_compress 示例（唯一以它为示例的消费面注释）随模块删除失去所指，
  改中性示例；metadata 字段与 _check_metadata 校验器本体仍在（防误伤）。
- arm5 metadata 落库臂（自 test_metadata_persisted_to_raw_document 迁移）：
  真实内存库 + LLM 提取外部边界替身。
- 回归臂：mcp/ingest_worker/distill_service 三个活 AddMemoryReq 构造面随
  家族回归覆盖。

纪律：测试库走夹具隔离（红线：零连接触碰 LANTAI_HOME 真实开发库）；
活契约臂直调真 pydantic 校验零 mock。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

ROOT = Path(__file__).resolve().parents[1]


# ── arm1：死代码臂（现状红：模块仍在）────────────────────────────────────────


def test_arm1_pre_compress_module_removed():
    """pre_compress 模块必须删净：import 失败 + 源文件不存在 + 全仓活代码面
    零残留（只扫 lantai/ tests/ scripts/；docs 历史巡检记录与 .scratch/build
    基线快照是历史档案非活代码，豁免）。"""
    import importlib.util

    spec = importlib.util.find_spec("lantai.integrations.pre_compress")
    assert spec is None, "lantai.integrations.pre_compress 模块仍可被找到（删除案未执行）"
    assert not (ROOT / "lantai" / "integrations" / "pre_compress.py").exists(), (
        "pre_compress.py 源文件仍在"
    )
    assert not (ROOT / "tests" / "test_pre_compress.py").exists(), (
        "tests/test_pre_compress.py 死模块测试仍在（应随模块一并下线，活契约已迁移）"
    )

    residue: list[str] = []
    for base in ("lantai", "tests", "scripts"):
        for f in (ROOT / base).rglob("*.py"):
            rel = str(f.relative_to(ROOT)).replace("\\", "/")
            if rel == "tests/test_ticket77_precompress_dead_wiring_removal.py":
                continue  # 本文件自证行（docstring 引用病灶原文）豁免，票 72 arm3 同款
            try:
                text = f.read_text(encoding="utf-8")
            except OSError:
                continue
            if "pre_compress" in text:
                residue.append(rel)
    assert not residue, f"活代码面 pre_compress 残留（删除案）：{residue}"


# ── arm2：注释清理臂（现状红：db.py busy_timeout 理由引用死模块）──────────────


def test_arm2_db_busy_timeout_comment_no_pre_compress():
    """db.py 的 busy_timeout 注释理由改写后不得再引用 pre_compress（参数本体
    保留——对其余并发写路径仍有价值，删理由不删参数）。"""
    db_src = (ROOT / "lantai" / "storage" / "db.py").read_text(encoding="utf-8")
    assert "pre_compress" not in db_src, "db.py 源码仍引用 pre_compress（注释理由未改写）"
    # 参数本体仍在（防误删）：connect_args timeout=30 是真实并发写保护面
    assert '"timeout": 30' in db_src, "busy_timeout=30 参数被误删（工单口径：删理由不删参数）"


# ── arm3：活契约保全臂（AddMemoryReq.metadata 有界校验五防线）─────────────────


class TestMetadataBounds:
    """自 tests/test_pre_compress.py::TestMetadataBounds 迁移（死模块删除不
    陪葬活契约）：metadata 有界校验是 security review 防线，直调真 pydantic
    校验零 mock。"""

    def test_flat_scalar_ok(self):
        from lantai.models.schemas import AddMemoryReq

        req = AddMemoryReq(
            title="t", content="这是一段足够长的内容", metadata={"source": "host_tool"}
        )
        assert req.metadata == {"source": "host_tool"}

    def test_too_many_keys_rejected(self):
        from lantai.models.schemas import AddMemoryReq

        with pytest.raises(Exception):
            AddMemoryReq(
                title="t", content="这是一段足够长的内容", metadata={f"k{i}": i for i in range(11)}
            )

    def test_nested_value_rejected(self):
        from lantai.models.schemas import AddMemoryReq

        with pytest.raises(Exception):
            AddMemoryReq(title="t", content="这是一段足够长的内容", metadata={"a": {"b": 1}})

    def test_long_string_value_rejected(self):
        from lantai.models.schemas import AddMemoryReq

        with pytest.raises(Exception):
            AddMemoryReq(title="t", content="这是一段足够长的内容", metadata={"a": "x" * 501})

    def test_long_key_rejected(self):
        from lantai.models.schemas import AddMemoryReq

        with pytest.raises(Exception):
            AddMemoryReq(title="t", content="这是一段足够长的内容", metadata={"k" * 65: 1})


# ── arm4：消费面注释臂（现状红：schemas.py:40 注释仍拿死模块当示例）──────────


def test_arm4_schemas_metadata_comment_neutral_example():
    """AddMemoryReq.metadata 字段注释（唯一拿 source=pre_compress 当示例的消费
    面注释）随模块删除失去所指——改中性示例后不得再出现该字样。"""
    schemas_src = (ROOT / "lantai" / "models" / "schemas.py").read_text(encoding="utf-8")
    assert "pre_compress" not in schemas_src, (
        "schemas.py 注释仍以 source=pre_compress 为示例（所指已随模块删除）"
    )
    # metadata 字段与校验器本体仍在（防误伤活契约）
    assert "metadata: dict" in schemas_src
    assert "_check_metadata" in schemas_src


# ── arm5：metadata 落库活契约（自 test_metadata_persisted_to_raw_document 迁移）


def test_arm5_metadata_persisted_to_raw_document(monkeypatch):
    """AddMemoryReq.metadata → RawDocument.meta 落库（真实内存库；LLM 提取与
    向量库为外部边界替身——mock 允许面）。"""
    from lantai.models.schemas import AddMemoryReq
    from lantai.models.tables import RawDocument

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    import lantai.services.memory_service as ms
    import lantai.storage.db as db_module

    def get_test_session():
        return Session(engine)

    req = AddMemoryReq(
        title="快照",
        content="这是一段足够长用于测试的对话快照内容",
        lane="chat",
        metadata={"source": "host_tool"},
    )
    with (
        patch.object(db_module, "get_session", get_test_session),
        patch.object(
            ms,
            "extract_candidate",
            return_value={
                "topic": [],
                "summary": "s",
                "claims": [],
                "methods": [],
                "constraints": [],
                "actions": [],
                "extractor_confidence": 0.9,
            },
        ),
        patch.object(ms, "get_vector_store", return_value=Mock()),
        patch("lantai.services.memory_service.fastpath_check", return_value=None),
    ):
        ms.add_memory(req)
    with Session(engine) as s:
        doc = s.exec(select(RawDocument)).first()
        assert doc is not None
        assert doc.meta == {"source": "host_tool"}
