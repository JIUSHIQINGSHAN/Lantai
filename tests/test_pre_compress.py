"""压缩前抢救测试：_render 纯函数 + flush_before_compress + metadata 落库"""

import threading
from unittest.mock import Mock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from lantai.integrations.pre_compress import _render, flush_before_compress
from lantai.models.schemas import AddMemoryReq


class TestMetadataBounds:
    """AddMemoryReq.metadata 有界校验（security review 修复）"""

    def test_flat_scalar_ok(self):
        req = AddMemoryReq(
            title="t", content="这是一段足够长的内容", metadata={"source": "pre_compress"}
        )
        assert req.metadata == {"source": "pre_compress"}

    def test_too_many_keys_rejected(self):
        with pytest.raises(Exception):
            AddMemoryReq(
                title="t", content="这是一段足够长的内容", metadata={f"k{i}": i for i in range(11)}
            )

    def test_nested_value_rejected(self):
        with pytest.raises(Exception):
            AddMemoryReq(title="t", content="这是一段足够长的内容", metadata={"a": {"b": 1}})

    def test_long_string_value_rejected(self):
        with pytest.raises(Exception):
            AddMemoryReq(title="t", content="这是一段足够长的内容", metadata={"a": "x" * 501})

    def test_long_key_rejected(self):
        with pytest.raises(Exception):
            AddMemoryReq(title="t", content="这是一段足够长的内容", metadata={"k" * 65: 1})


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestRender:
    def test_extracts_user_and_assistant(self):
        text = _render([_msg("user", "你好"), _msg("assistant", "你好，有什么可以帮你")])
        assert text.startswith("User: 你好")
        assert "Assistant: 你好，有什么可以帮你" in text

    def test_skips_tool_and_system(self):
        text = _render(
            [
                _msg("system", "系统提示不可见"),
                {"role": "tool", "content": "工具输出"},
                _msg("user", "真正的用户提问"),
            ]
        )
        assert "系统提示" not in text
        assert "工具输出" not in text
        assert "真正的用户提问" in text

    def test_skips_non_string_content(self):
        text = _render(
            [
                {"role": "user", "content": ["数组", "内容"]},
                {"role": "assistant", "content": None},
                {"role": "user", "content": ""},
            ]
        )
        assert text is None

    def test_all_tool_returns_none(self):
        assert _render([{"role": "tool", "content": "x"}]) is None

    def test_empty_input_none(self):
        assert _render([]) is None
        assert _render(None) is None

    def test_only_last_n(self):
        msgs = [_msg("user", f"消息{i}") for i in range(20)]
        text = _render(msgs, n=3)
        assert "消息0" not in text
        assert "消息17" in text and "消息19" in text

    def test_truncates_long_content(self):
        long = "长" * 2000
        text = _render([_msg("user", long)])
        assert len(text) <= 600 + len("User: ")


class TestFlush:
    def test_no_plain_text(self):
        assert flush_before_compress([{"role": "tool", "content": "x"}]) == {
            "flushed": False,
            "reason": "no_plain_text",
        }

    def test_too_short(self):
        assert flush_before_compress([_msg("user", "好")]) == {
            "flushed": False,
            "reason": "too_short",
        }

    def test_flush_passes_correct_params(self):
        """冒烟：mock add_memory（内部依赖 LLM 提取），验证参数与 metadata 传递。

        r57（票 05 续41）：`flush_before_compress` 是 fire-and-forget daemon 线程
        （`pre_compress.py:60` 不 join）。旧写法在 `with patch` 退出后才断言
        `m.assert_called_once()`——主线程赢则绿、worker 输则红（门禁全量套件实测
        `1 failed, 2908 passed`；`.scratch/gate/r57_starve.py` 压 GIL 切换间隔
        两档各 2/5 红、默认间隔 5/5 绿）。patch 先退还有个更脏的出口：worker 随后打到
        **真实** `add_memory`（LLM/DB 面）。修法=在 patch 作用域内等 worker 真进门
        ——把「终结」变成被断言的事实而非许愿（续26 `_drain_task` 同纪律），
        零 skip、零删断言、不改生产语义。
        """
        called = threading.Event()

        def _record(req):
            called.set()
            return {"document_id": "doc1", "candidate_id": "c1"}

        with patch("lantai.integrations.pre_compress.add_memory", side_effect=_record) as m:
            res = flush_before_compress([_msg("user", "这是一段足够长的对话内容")])
            assert res["flushed"] is True
            assert res["chars"] > 10
            assert called.wait(timeout=15), (
                "worker 线程 15s 内未调用被 patch 的 add_memory（孤儿竞态）"
            )
        m.assert_called_once()
        req = m.call_args.args[0]
        assert req.lane == "chat"
        assert req.metadata == {"source": "pre_compress"}
        assert req.title == "压缩前会话快照"

    def test_flush_worker_failure_swallowed(self):
        """add_memory 抛异常 → flush_before_compress 仍返回 flushed，线程内吞掉。

        同样必须等 worker 进入被 patch 的 add_memory 再拆 patch，否则竞态下
        worker 打到真实函数（异常被 _worker 吞掉，测试假绿、真调用已发生）。
        """
        entered = threading.Event()

        def _boom(req):
            entered.set()
            raise RuntimeError("llm down")

        with patch("lantai.integrations.pre_compress.add_memory", side_effect=_boom) as m:
            res = flush_before_compress([_msg("user", "足够长的内容避免 too_short")])
            assert res["flushed"] is True  # 返回值不受线程内异常影响
            assert entered.wait(timeout=15), "worker 线程 15s 内未进入被 patch 的 add_memory"
        assert m.call_count == 1  # 异常路径同样只调一次，且被吞掉不重抛


def test_metadata_persisted_to_raw_document():
    """AddMemoryReq.metadata → RawDocument.meta 落库（真实内存库，mock LLM 提取）。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)

    import lantai.services.memory_service as ms
    import lantai.storage.db as db_module
    from lantai.models.tables import RawDocument

    def get_test_session():
        return Session(engine)

    req = __import__("lantai.models.schemas", fromlist=["AddMemoryReq"]).AddMemoryReq(
        title="快照",
        content="这是一段足够长用于测试的对话快照内容",
        lane="chat",
        metadata={"source": "pre_compress"},
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
        assert doc.meta == {"source": "pre_compress"}
