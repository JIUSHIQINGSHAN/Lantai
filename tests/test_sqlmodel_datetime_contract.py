"""SQLModel datetime 契约守护（v0.68 票 33）：锁定 0.0.42 的真实行为 + 注释如实化。

三臂：
1. 版本反转哨兵——sqlmodel ≥0.0.47 进入环境（UTCDateTime 契约反转：naive
   写库直接 raise + 读回值变 aware）时本测试先红，把「悄悄升级」从事故
   变成红灯；
2. 落盘形态锁——真内存库 + 真 SQLModel：aware-UTC 值落盘为无偏移 UTC
   墙上时钟文本（bind 不换算、SQLite 按分量格式化），aware 带偏移值若不
   先归一 UTC 会按原墙上时钟落盘（票 32 F2 形态），升级后行为漂移此锁先红；
3. 注释一致性——core/time.py 不得再以现在时表述指向不存在的库级防护。

真契约（0.0.42 实况）：datetime 字段映射裸 DateTime，无库级时区防护；
正确性 = 写侧恒 aware UTC（core/time.py 单点）+ 读侧 _ensure_utc 归一。
依据：uv.lock 锁 0.0.42，pyproject 钉 >=0.0.42,<0.0.43。
"""

import importlib
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import DateTime as SADateTime
from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]


class _DatetimeContractProbe(SQLModel, table=True):
    """最小 datetime 列探针表：专测字段映射与落盘形态，不依赖业务表。"""

    __tablename__ = "dt_contract_probe"

    id: str | None = Field(default=None, primary_key=True)
    created_at: datetime


def test_no_utcdatetime_in_locked_sqlmodel():
    """臂 1：锁定环境无 UTCDateTime，datetime 字段映射裸 DateTime。"""
    try:
        sqltypes = importlib.import_module("sqlmodel.sql.sqltypes")
    except ImportError:
        sqltypes = None
    assert sqltypes is not None and not hasattr(sqltypes, "UTCDateTime"), (
        "sqlmodel.sql.sqltypes 缺失或已含 UTCDateTime：pyproject 上界 <0.0.43 "
        "被突破——datetime 契约即将反转（naive 写库拒收 + 读回值变 aware），"
        "须先清 naive 写入面与 naive 读取假设再升级"
    )
    col_type = _DatetimeContractProbe.__table__.columns["created_at"].type
    assert type(col_type) is SADateTime, (
        f"datetime 字段映射已非裸 DateTime：{type(col_type).__name__}"
    )


def test_aware_utc_value_persists_as_naive_utc_text():
    """臂 2：aware-UTC 值落盘 = 无偏移 UTC 墙上时钟文本（bind 不换算）。

    两个方向都钉死：①aware-UTC 落盘为 UTC 墙上时钟（正确路径的现状锁）；
    ②aware 带偏移值**不换算**、按原墙上时钟落盘——写侧不先归一 UTC 的
    毒值（票 32 F2 形态：aware +08:00）会静默差 8 小时入库，升级后若
    bind 行为变化此锁先红。
    """
    for offset_h, label in ((0, "aware-UTC"), (8, "aware+08:00（bind 不换算毒值形态）")):
        engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                _DatetimeContractProbe(
                    id="probe-1",
                    created_at=datetime(
                        2026, 8, 2, 22, 41, 19, 234999, tzinfo=timezone(timedelta(hours=offset_h))
                    ),
                )
            )
            session.commit()
        with engine.connect() as conn:
            raw = conn.execute(text("SELECT created_at FROM dt_contract_probe")).scalar_one()
        assert raw == "2026-08-02 22:41:19.234999", (
            f"{label} 落盘文本形态漂移（期望无偏移原墙上时钟）：{raw!r}"
        )


def test_core_time_docstring_states_real_contract():
    """臂 3：core/time.py 的写库防护注记必须如实（本臂驱动注释改写）。

    旧注记以「sqlmodel ≥0.0.47 的 UTCDateTime 拒 naive」为现在时依据，
    该防护在锁定 0.0.42 从未生效——正确性论据必须指向真实契约：
    0.0.42 无库级防护、naive 静默落库、纪律靠约定。
    """
    src = (ROOT / "lantai" / "core" / "time.py").read_text(encoding="utf-8")
    assert "0.0.42" in src, "core/time.py 未写明版本基线 0.0.42"
    assert "无库级" in src, "core/time.py 未如实声明「无库级防护」"
    assert not re.search(r"的\s*`?UTCDateTime`?\s*(对\s*naive|拒绝|拒写|硬要求)", src), (
        "core/time.py 仍有以现在时表述指向不存在的 UTCDateTime 防护"
    )
