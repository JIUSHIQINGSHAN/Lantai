"""拾遗补救扫描 lifecycle 过滤（v0.68 票 15）：superseded 行不再经拾遗回灌。

病灶（票 15；票 02 Answer「待立案」段入账）：`shiyi._remedy_recall` 基础 stmt
只认 `status == "active"`——更正设计里旧说**有意保留** status=active，仅标
`lifecycle_status="superseded"` + `superseded_by`（闸 docstring 自述），superseded
行可进零召回补救结果集。两面出口：hybrid 兜底面（MCP/shell_hook 由更正闸标记
臂兜住，票 02）与 `/shiyi/remedy` 直连面（无闸，旧值裸返）——契约必须在采集口
落刀（票 05 续32 同一理由）。口径对齐 context.py `5ae6caf3`：lifecycle 单条件
过滤，superseded_by 为空的异常行一并退出（拾遗无「已更正」标记机制，过滤是最小
正确语义，宁 miss 不脏写）。

红→绿：主臂/异常行臂在修复前必须红在断点形态（superseded 行在补救结果里）；
对照臂与 verbatim 现状锁现在就绿、修复后必须保持绿。不 mock 被测链路：
真内存库（param_env）+ 真 `apply_supersede_correction` 更正链 + 真
`ShiyiEngine.remedy_recall` 直调；无网络调用面（扫描是纯 SQL+打分）。
"""

import pytest

from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem

Q = "武汉光谷上班的地方是哪"


@pytest.fixture()
def remedy_env(param_env):
    """四行种子 + 真实更正链：superseded 正常行 / superseded 异常行 / active 对照行 / verbatim 行。

    落库态由用例内前提断言钉住（防种子写错让断言空转）。
    """
    from datetime import timedelta

    from lantai.cognition.late_correction import apply_supersede_correction
    from lantai.storage.fts import sync_fts

    session_factory, _engine = param_env
    now = utcnow()
    with session_factory() as s:
        s.add(
            MemoryItem(
                id="mem_old",
                memory_type="semantic",
                key="住址",
                content="用户的地址是武汉，在光谷上班",
                event_time=now - timedelta(days=200),
            )
        )
        s.add(
            MemoryItem(
                id="mem_new",
                memory_type="semantic",
                key="住址",
                content="用户的地址已改为荆州，现在住在荆州",
                event_time=now - timedelta(days=10),
            )
        )
        # 异常行：lifecycle 标了 superseded 但 superseded_by 为空（闸「只认显式链」
        # 会放过它；context 5ae6caf3 单条件过滤让它退出——本票同口径，防解释漂移）
        s.add(
            MemoryItem(
                id="mem_anom",
                memory_type="semantic",
                key="旧仓库",
                content="武汉光谷附近的老仓库记录",
                lifecycle_status="superseded",
                superseded_by=None,
                event_time=now - timedelta(days=100),
            )
        )
        # 对照行：普通 active 行不受过滤影响
        s.add(
            MemoryItem(
                id="mem_normal",
                memory_type="semantic",
                key="会议",
                content="用户在武汉光谷开产品会",
                event_time=now - timedelta(days=1),
            )
        )
        # verbatim 现状锁行（票 05 续32 契约：VERBATIM_IN_RECALL=false 排除）
        s.add(
            MemoryItem(
                id="mem_verbatim",
                memory_type="verbatim",
                key="原话",
                content="用户在武汉光谷上班的原话记录",
                event_time=now - timedelta(days=2),
            )
        )
        s.commit()
        for mid in ("mem_old", "mem_new", "mem_anom", "mem_normal", "mem_verbatim"):
            row = s.get(MemoryItem, mid)
            sync_fts(s, row.id, row.content)
        s.commit()
        res = apply_supersede_correction(
            s.get(MemoryItem, "mem_old"), s.get(MemoryItem, "mem_new"), session=s, reason="搬家"
        )
        s.commit()
    assert res.get("ok") is True, res
    with session_factory() as s:
        old = s.get(MemoryItem, "mem_old")
        assert old.status == "active" and old.lifecycle_status == "superseded"
        assert old.superseded_by == "mem_new"
    return param_env


def _remedy(session_factory):
    from lantai.cognition.shiyi import ShiyiEngine

    with session_factory() as s:
        eng = ShiyiEngine(session=s, principal=Principal(user_id="default"))
        return eng.remedy_recall(Q)


def _ids(ret) -> list[str]:
    return [it.get("id") for it in ret.get("remedied_items", [])]


class TestShiyiSupersedeFilter:
    def test_superseded_row_not_in_remedy_results(self, remedy_env):
        """主红点：superseded 旧值不进补救结果（当前态回灌面收口）。"""
        ret = _remedy(remedy_env[0])
        ids = _ids(ret)
        # 红点（修复前 mem_old 经拾遗回灌，裸正文）
        assert "mem_old" not in ids, f"被取代旧值仍经拾遗补救回灌：{ids}"

    def test_anomalous_superseded_row_also_excluded(self, remedy_env):
        """异常行臂：superseded 但 superseded_by 为空 → 单条件过滤一并退出（口径压实）。"""
        ret = _remedy(remedy_env[0])
        ids = _ids(ret)
        # 红点（修复前异常行同样回灌）
        assert "mem_anom" not in ids, f"superseded 异常行仍经拾遗补救回灌：{ids}"

    def test_normal_active_row_unaffected(self, remedy_env):
        """对照臂：普通 active 行照常进补救结果（前提断言在前防空转）。"""
        ret = _remedy(remedy_env[0])
        ids = _ids(ret)
        assert "mem_normal" in ids, f"前提塌了：对照行未被补救召回，LIKE 预筛或扫描面坏了：{ids}"

    def test_verbatim_still_excluded_when_switch_off(self, remedy_env, monkeypatch):
        """现状锁：VERBATIM_IN_RECALL=false 下 verbatim 仍被排除（票 05 续32 契约不回归）。"""
        from lantai.core.settings import settings

        monkeypatch.setattr(settings, "VERBATIM_IN_RECALL", False)
        ret = _remedy(remedy_env[0])
        ids = _ids(ret)
        assert "mem_verbatim" not in ids, f"verbatim 行混进补救结果：{ids}"
