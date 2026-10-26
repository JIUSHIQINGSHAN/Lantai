"""staged 评测确定性环境（票 02 步骤②/③）：对表脚本、冒烟测试与正式跑测共用。

与 tests/test_staged_anchor_miss.py 的 staged_env fixture 同款范式（不 mock 冒烟
纪律）：真 SQLite（StaticPool 内存库）+ 真 FTS5 + 真内嵌 ChromaDB，真实直调
`run_staged_eval` 全六段；替身仅外部 embedding 面（统一 512 维确定性
embed，单一真源 deterministic_embed——与 harness 写入侧同维，避免混维）。

real_llm_chat=True（票 02 步骤③「提取段真 LLM」）：chat 面（extractor/
proposer/contradiction）走真实 OPENAI_BASE_URL/LLM_MODEL，不替；embed 面
仍为确定性 512——harness 写入侧固定 512 维（staged.py ④ 段），查询侧若放
真 embed（1024）即混维拒查，故真 LLM 轮的「真」限定在 chat 面，如实记录。

data_dir（票 02 步骤③「评测库独立 LANTAI_DATA_DIR 双闸」）：传入即把
settings.LANTAI_HOME / DATABASE_URL / CHROMADB_PATH 全部重指到该独立目录
（闸一），db_module.engine 同步换为该目录下的库（闸二）——调用方须在跑测
前断言两闸都落位。

同时挂日志采集 handler：捕获獬豸（lantai.security）与决策层的 warning 日志，
供对表标签的证据字段（獬豸拦截日志/sid）引用。
"""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

import lantai.storage.db as db_module
import lantai.storage.vector_store as vs_module
from lantai.core.settings import settings
from lantai.eval.deterministic_embed import embed_for_eval
from lantai.storage.fts import init_fts


class _LogCaptor(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.name}: {record.getMessage()}")


@contextmanager
def staged_deterministic_env(
    tmp_dir: Path | None = None,
    *,
    real_llm_chat: bool = False,
    data_dir: Path | None = None,
):
    """进入确定性评测环境；yield (log_captor)。

    tmp_dir 缺省用临时目录（Chroma 持久化路径，退出即弃）。
    real_llm_chat：True 则 chat_json 不替（真 LLM）；embed 面恒为确定性 512。
    data_dir：独立评测数据目录（双闸：settings 三项 + db.engine 全重指）。
    """
    tmp = Path(tmp_dir) if tmp_dir else Path(mkdtemp(prefix="lantai-staged-env-"))

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    init_fts(engine.raw_connection())

    old_intent = os.environ.get("LANTAI_INTENT_OFF")
    os.environ["LANTAI_INTENT_OFF"] = "1"

    # 闸一（settings 面）：独立数据目录（LANTAI_HOME/DATABASE_URL/CHROMADB_PATH）
    # 全重指到 data_dir——未给 data_dir 时退回 tmp 临时目录
    eff_dir = Path(data_dir) if data_dir is not None else tmp
    chroma_dir = eff_dir / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)

    patches: list = [
        patch.object(settings, "CHROMADB_PATH", str(chroma_dir)),
    ]
    if data_dir is not None:
        db_file = eff_dir / "eval.db"
        db_file.touch()
        patches.append(patch.object(settings, "LANTAI_HOME", str(eff_dir)))
        patches.append(patch.object(settings, "DATABASE_URL", f"sqlite:///{db_file}"))

    captor = _LogCaptor()
    root = logging.getLogger("lantai")

    # 外部 LLM 结构化输出替身（同 tests/conftest._fake_chat_json：返回空 dict，
    # 让调用方走各自的「无内容」分支）——不替则本地真连 API、CI 抛错降级，
    # 两个环境跑的不是同一条路（conftest 票 01 方案甲的实证结论）。staged
    # 路径涉及：proposer/promoter（提案）、decision/contradiction/scorer（闸门）、
    # intent（召回意图）。real_llm_chat=True 时全部放真。
    def _fake_chat_json(*args, **kwargs):
        return {}

    if not real_llm_chat:
        patches.extend(
            [
                patch("lantai.llm.client.chat_json", _fake_chat_json),
                patch("lantai.evolution.proposer.chat_json", _fake_chat_json),
                patch("lantai.gate.contradiction.chat_json", _fake_chat_json),
                patch("lantai.retrieval.intent.chat_json", _fake_chat_json),
            ]
        )

    patches.extend(
        [
            patch.object(db_module, "engine", engine),
            patch.object(vs_module, "_store", None),
            patch.object(settings, "FTS_BM25_GRAM_TOKENIZE", True),
            patch.object(settings, "VECTOR_STORE_TYPE", "chromadb"),
            # embed 替身挂全链引用点（检索/闸门/评分/促升——均为外部 embedding 面；
            # 真维 1024 与 harness 写入侧 512 混维即拒查，故任何轮次都统一 512）
            patch("lantai.retrieval.hybrid.embed", embed_for_eval),
            patch("lantai.gate.decision.embed", embed_for_eval),
            patch("lantai.gate.scorer.embed", embed_for_eval),
            patch("lantai.evolution.promoter.embed", embed_for_eval),
        ]
    )

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        root.addHandler(captor)
        try:
            yield captor
        finally:
            root.removeHandler(captor)
            if old_intent is None:
                os.environ.pop("LANTAI_INTENT_OFF", None)
            else:
                os.environ["LANTAI_INTENT_OFF"] = old_intent
