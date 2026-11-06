"""X-08 部署文档与真实 SQLite 行为的合同锁（票 05 续24，评审清单 §4 X-08）。

现场复现（2026-11-04）：README.md:107 写「SQLite WAL + 内嵌 ChromaDB」、
白皮书 :219/:447 写「SQLite (WAL)」「WAL mode」，但 `lantai/storage/db.py:25`
的 `create_engine(..., connect_args={"timeout": 30})` 从不设置 journal_mode，
实测 PRAGMA 为 `delete`；busy_timeout=30000 倒是真的。文档承诺的并发语义
（读写互不阻塞）在生产里**并不存在**——运维按 WAL 理解去多进程部署就会撞上
文档自己声明的单进程约束。评审时零测试锁（14 处 "WAL" 测试命中全是记忆内容
字符串，无一断言）。

本锁是**双向合同**，不是把某一边钉死：
- 部署文档（README/白皮书）对 journal_mode 的声称 == 生产 engine 构造实测；
- 对 busy_timeout 的声称 == 实测 PRAGMA busy_timeout；
- 将来谁真的启用 WAL（连同备份/恢复对 -wal/-shm 伴生文件的处置），必须同步
  改文档，否则这里红；文档单方面吹回 WAL 而代码没开，同样红。

不 mock 口径：用 `spec_from_file_location` 重新执行 `lantai/storage/db.py` 的
模块体（与生产同一行 create_engine 构造，指向 monkeypatch 后的 tmp 库），
真实 connect + 真实 PRAGMA。别名模块名不占用 `lantai.storage.db`，不碰
conftest 的 engine 身份绊线；连接用完即 dispose，文件全落 tmp_path。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, select

from lantai.core.settings import settings

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_SRC = REPO_ROOT / "lantai" / "storage" / "db.py"
DEPLOY_DOCS = [REPO_ROOT / "README.md", REPO_ROOT / "docs" / "Lantai-v0.21-Whitepaper.md"]

# 声称识别：WAL（含 "SQLite … WAL" 架构图行、"WAL mode" 规格行）vs delete
_WAL_CLAIM = re.compile(r"SQLite[^|\n]{0,24}WAL|WAL\s*mode", re.IGNORECASE)
_DELETE_CLAIM = re.compile(
    r"journal_mode\s*=?\s*['\"]?delete|SQLite[^|\n]{0,24}[（(]\s*delete|rollback\s+journal|DELETE\s*模式",
    re.IGNORECASE,
)
_BUSY_CLAIM = re.compile(r"busy_timeout\s*=?\s*(\d+)\s*s", re.IGNORECASE)


@pytest.fixture()
def production_engine(tmp_path, monkeypatch):
    """按生产构造新建一个 engine（真实执行 db.py 模块体），指向 tmp 库。"""
    db_file = tmp_path / "remembrance.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{db_file}")
    # 别名加载：不触碰 sys.modules 里的 lantai.storage.db（conftest 身份绊线在盯它）
    spec = importlib.util.spec_from_file_location("db_prod_construction_probe", DB_SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod.engine
    mod.engine.dispose()


def _pragma(eng, name: str):
    conn = eng.raw_connection()
    try:
        return conn.cursor().execute(f"PRAGMA {name}").fetchone()[0]
    finally:
        conn.close()


def test_measured_journal_mode_is_delete_by_default(production_engine):
    """生产构造的实测基线：未设 journal_mode ⇒ SQLite 文件库默认 delete。

    这条同时是合同的「事实半边」：如果哪天真启用了 WAL，这里变红不是回归，
    是提醒你连同部署文档、备份/恢复（-wal/-shm 伴生文件）一起改——见合同。
    """
    assert _pragma(production_engine, "journal_mode") == "delete"


def test_measured_busy_timeout_is_30s(production_engine):
    assert _pragma(production_engine, "busy_timeout") == 30000


def test_deployment_docs_journal_mode_claim_matches_reality(production_engine):
    """双向合同：文档对模式的声称必须与实测一致，且不许沉默。"""
    measured = _pragma(production_engine, "journal_mode")
    docs_text = "\n".join(p.read_text(encoding="utf-8") for p in DEPLOY_DOCS)
    claims_wal = bool(_WAL_CLAIM.search(docs_text))
    claims_delete = bool(_DELETE_CLAIM.search(docs_text))
    if claims_wal and claims_delete:
        pytest.fail("部署文档同时声称 WAL 与 delete 两种模式，自相矛盾")
    assert claims_wal != claims_delete, (
        "部署文档不再声称 SQLite 日志模式——单进程部署约束的根依据消失了；"
        "README 部署约束段或白皮书存储行必须保留可核对的模式描述"
    )
    claimed = "wal" if claims_wal else "delete"
    assert claimed == measured, (
        f"部署文档声称 journal_mode={claimed!r}，生产构造实测 {measured!r}——"
        "X-08 的原始发散形态。要么代码真开 WAL（并处置备份/恢复的伴生文件），"
        "要么把文档改成实测值，二者必须一致"
    )


def test_deployment_docs_busy_timeout_claim_matches_reality(production_engine):
    measured_ms = _pragma(production_engine, "busy_timeout")
    docs_text = "\n".join(p.read_text(encoding="utf-8") for p in DEPLOY_DOCS)
    claim = _BUSY_CLAIM.search(docs_text)
    assert claim, "部署文档的 busy_timeout 声称被删掉了（30s 是并发写保护的根依据）"
    assert int(claim.group(1)) * 1000 == measured_ms, (
        f"文档声称 busy_timeout={claim.group(1)}s，实测 {measured_ms}ms"
    )


def test_production_delete_fact_atomic_actually_cascades_projection(production_engine):
    """端到端不 mock 冒烟（票 05 续28）：真调 `delete_fact_atomic`，投影必须随删清空。

    `delete_fact_atomic` 的 docstring 白纸黑字承诺「触发 FactEntity 级联删除」——
    这条锁验证承诺在生产构造的 engine 上**真的兑现**，而不是只在手动开 pragma 的
    测试库里兑现。全仓唯一的外键就是这个父子关系，删除不级联即孤儿投影行残留，
    实体反查索引会查到已不存在的事实。
    """
    import lantai.models.tables as tables  # noqa: F401  注册全部表
    from lantai.services.record_ops_service import delete_fact_atomic

    SQLModel.metadata.create_all(production_engine)
    fid = "fact-fk-lock-1"
    with Session(production_engine) as s:
        s.add(tables.FactAtomic(id=fid, statement="甲乙是同事", predicate="同事", confidence=0.9))
        s.add(tables.FactEntity(fact_id=fid, entity_name="甲", role="subject"))
        s.add(tables.FactEntity(fact_id=fid, entity_name="乙", role="object"))
        s.commit()

    with Session(production_engine) as s:
        res = delete_fact_atomic(fid, reason="回归锁", session=s)
    assert res["ok"] is True, f"删除本身必须成功（失败说明级联改出了新错）：{res}"

    with Session(production_engine) as s:
        assert s.get(tables.FactAtomic, fid) is None, "父行应已物理删除"
        left = s.exec(select(tables.FactEntity).where(tables.FactEntity.fact_id == fid)).all()
        assert left == [], (
            f"父行删除后实体投影残留 {len(left)} 行：docstring 承诺的级联没兑现——"
            "正是续28 定位的约束漂移病灶（FK pragma 未开，ondelete=CASCADE 落空）"
        )
