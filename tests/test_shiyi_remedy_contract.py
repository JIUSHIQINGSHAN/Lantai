"""拾遗零召回兜底的契约守卫锁（评审 N-01 修法的后半句，票 05 续32）。

病灶（实测复现，探针 `.scratch/gate/r41_probe2.py` 三轴一致）：
`hybrid_search` 三个兜底站点（`_hybrid_search_impl` 与 `_keyword_fallback` ×2）在
零召回时唤醒 `ShiyiEngine.remedy_search_pipeline(query, domain, limit)`——**只传
query/domain/limit**。主流水线在同样的调用里施加的契约，兜底一条都不带：

- **verbatim**：主路按 `VERBATIM_IN_RECALL=False` 默认排除逐字原文
  （`hybrid.py:658`），引擎扫 `status=="active"` 全表不看 `memory_type` ⇒ 默认档
  下逐字原文经兜底重新进入召回面。实测：主路 `[]` vs 兜底 `['vb-1']`。
- **as_of / time_from / time_to / as_of_recorded**：主路做历史视图过滤，兜底无视
  ⇒ 「as_of 之后才生效/该在视图里看不见」的记忆被塞回。实测 as_of=昨天：
  主路 `[]` vs 兜底 `['t-1']`。
- **lanes / shared_scope / memory_types 显式限定**：调用方点名了作用域，兜底的
  子串匹配根本不看 ⇒ 跨 lane 召回。实测 lanes=["meeting"]：主路 `[]` vs 兜底
  返回 lane=coding 的行。

续24 已落地的只是 N-01 修法前半句（三个入口签名默认 `enable_shiyi=False`，seam5
钉住）；后半句「至少在传了 as_of/…/lane、或 principal is None 时跳过拾遗」从未写
过。租户/属主面**实测不泄漏**（引擎 `_check_access` 在岗，探针 C3/D1/D2），本轮
不虚修，只补守卫 + verbatim 排除。

判据防假绿（每锁带对照）：
- 每条泄漏锁同时断言**前提**（主路确实零召回——环境变了就炸给人看，不当空转）
  与**对照**（同样的种子/查询在契约参数缺席时兜底照常召回——排除「兜底一律空」
  这种白捡的绿）。
- verbatim 排除做在**引擎扫描侧**（SQL where），不是把整个兜底关掉——
  `/shiyi/remedy` 直连入口同样受同一默认契约约束，语义一致。
- 守卫只在兜底面生效：显式 `enable_shiyi=True` 仍可用（seam1/3/5 行为保持），
  「默认关 + 用时带契约」两条腿都不许被锯。

AGENTS.md 测试纪律：全部真实建库（autouse 隔离 + LLM 替身，零网络），直调生产
`hybrid_search`，不 mock 检索/补救逻辑本身。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from lantai.core.auth import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryItem
from lantai.retrieval.hybrid import hybrid_search
from lantai.storage import db

P = Principal(user_id="user-r41", tenant_id="tenant-r41")
# seam5 同款触发式：主路（向量/BM25/FTS）零召回、引擎字符重合命中的缩略词查询
K8S_CONTENT = "Kubernetes生产集群中核心微服务容器重启策略统一配置为Always"
K8S_QUERY = "K8S微服务重启总是生效"


def _seed(
    session, *, id: str, content: str, memory_type: str = "text", lane: str = "general", **kw
) -> None:
    t = utcnow()
    session.add(
        MemoryItem(
            id=id,
            content=content,
            user_id=P.user_id,
            tenant_id=P.tenant_id,
            domain="user",
            lane=lane,
            confidence=0.9,
            status="active",
            memory_type=memory_type,
            created_at=t,
            updated_at=t,
            **kw,
        )
    )
    session.commit()


def _ids(res: list[dict]) -> list[str]:
    return [r["memory"]["id"] for r in res]


def test_remedy_never_breaches_verbatim_default_contract():
    """verbatim「默认不召回」是全局契约，兜底不得击穿：默认档下逐字原文不进召回面。

    三段：前提（纯 verbatim 种子 ⇒ 主路零召回 ⇒ 修复前兜底把它捞回=红）；
    泄漏断言（结果里绝不出现 verbatim 行）；对照（同库同查询换 text 种子兜底照常
    召回——证明红/绿差在 verbatim 排除本身，不是兜底死掉）。
    """
    with db.get_session() as session:
        _seed(
            session,
            id="r41-vb-only",
            content="我的银行卡密码是 864213 的逐字原文记录备存",
            memory_type="verbatim",
        )
        # 前提：主路对 verbatim 默认排除（否则本锁测不到兜底面）
        assert (
            hybrid_search(
                query="银行卡密码 864213 逐字原文备存", top_k=5, principal=P, session=session
            )
            == []
        ), "前提失效：主路召回了 verbatim，本文件其余 verbatim 判据失去意义"
        # 泄漏断言：开了兜底也不许把 verbatim 捞回来
        res = hybrid_search(
            query="银行卡密码 864213 逐字原文备存",
            top_k=5,
            principal=P,
            session=session,
            enable_shiyi=True,
        )
        assert "r41-vb-only" not in _ids(res), f"拾遗兜底击穿 verbatim 默认不召回契约：{_ids(res)}"

        # 对照：text 种子同路径兜底必须照常召回
        _seed(session, id="r41-vb-control", content=K8S_CONTENT)
        on = hybrid_search(
            query=K8S_QUERY, top_k=5, principal=P, session=session, enable_shiyi=True
        )
        assert "r41-vb-control" in _ids(on), (
            f"对照组失效（兜底整体死了，上一条绿是假的）：{_ids(on)}"
        )


def test_remedy_skipped_under_as_of_view():
    """传了 as_of 历史视图 ⇒ 兜底子串匹配没有视图过滤能力 ⇒ 必须跳过（N-01 修法后半句）。"""
    with db.get_session() as session:
        _seed(session, id="r41-asof", content=K8S_CONTENT)
        # 前提 + 对照：不带 as_of 时兜底照常召回
        base = hybrid_search(
            query=K8S_QUERY, top_k=5, principal=P, session=session, enable_shiyi=True
        )
        assert "r41-asof" in _ids(base), f"前提失效：兜底对照组不召回：{_ids(base)}"
        # 断言：带 as_of ⇒ 兜底不出现（宁 miss 不脏召）
        asof = utcnow() - timedelta(days=1)
        assert (
            hybrid_search(
                query=K8S_QUERY,
                top_k=5,
                principal=P,
                session=session,
                enable_shiyi=True,
                as_of=asof,
            )
            == []
        ), "as_of 历史视图下拾遗兜底仍在召回——补救行没经过时效视图过滤，契约被击穿"
        # time_from/time_to/as_of_recorded 同口径
        for kw in (
            {"time_from": utcnow() - timedelta(days=2)},
            {"time_to": utcnow() + timedelta(days=2)},
            {"as_of_recorded": asof},
        ):
            assert (
                hybrid_search(
                    query=K8S_QUERY, top_k=5, principal=P, session=session, enable_shiyi=True, **kw
                )
                == []
            ), f"时间参数 {kw} 在场时兜底未跳过"


def test_remedy_skipped_under_lane_scope():
    """lanes 限定作用域 ⇒ 兜底不看 lane，跨道召回即越界：必须跳过。"""
    with db.get_session() as session:
        _seed(session, id="r41-lane", content=K8S_CONTENT, lane="coding")
        base = hybrid_search(
            query=K8S_QUERY, top_k=5, principal=P, session=session, enable_shiyi=True
        )
        assert "r41-lane" in _ids(base), "前提失效：兜底对照组不召回"
        res = hybrid_search(
            query=K8S_QUERY,
            top_k=5,
            principal=P,
            session=session,
            enable_shiyi=True,
            lanes=["meeting"],
        )
        assert res == [], (
            f"lanes=['meeting'] 下兜底召回了 lane=coding 的行：{_ids(res)}（调用方点名作用域被无视）"
        )


def test_remedy_skipped_under_explicit_types_or_scope_or_anonymous():
    """其余兜底无法兑现的调用方契约，一律不补救（评审点名 + 纵深防御）：
    memory_types 显式限定、shared_scope 显式限定、principal=None 匿名（引擎
    ACL 在 None 时放行一切行——修复前的探针在这条上受门控干扰读数不稳，
    按最保守口径一并跳过，判据只锁修复后的行为）。"""
    with db.get_session() as session:
        _seed(session, id="r41-types", content=K8S_CONTENT)
        base = hybrid_search(
            query=K8S_QUERY, top_k=5, principal=P, session=session, enable_shiyi=True
        )
        assert "r41-types" in _ids(base), "前提失效：兜底对照组不召回"
        assert (
            hybrid_search(
                query=K8S_QUERY,
                top_k=5,
                principal=P,
                session=session,
                enable_shiyi=True,
                memory_types=["text"],
            )
            == []
        ), "memory_types 显式限定下兜底未跳过（引擎扫描不分类型，兑现不了该限定）"
        assert (
            hybrid_search(
                query=K8S_QUERY,
                top_k=5,
                principal=P,
                session=session,
                enable_shiyi=True,
                shared_scope="swarm_shared",
            )
            == []
        ), "shared_scope 显式限定下兜底未跳过"
        assert (
            hybrid_search(
                query=K8S_QUERY, top_k=5, principal=None, session=session, enable_shiyi=True
            )
            == []
        ), (
            "principal=None 匿名请求下兜底未跳过（None 时引擎 ACL 放行一切行——匿名面不该拿到全量模糊召回）"
        )


def test_remedy_still_available_when_no_contract_params():
    """反向保险：守卫只挡「兜底兑现不了的契约」，不得把无契约的显式兜底一起杀掉
    （seam1 同型的裸调用必须照常召回 + 带围栏投影）。"""
    with db.get_session() as session:
        _seed(session, id="r41-bare", content=K8S_CONTENT)
        res = hybrid_search(
            query=K8S_QUERY, top_k=5, principal=P, session=session, enable_shiyi=True
        )
        assert _ids(res) and res[0]["is_remedied"] is True, f"裸调用兜底被错杀：{res!r}"
        assert "<memory_data" in res[0]["fenced_content"], (
            "补救行必须仍经樊篱围栏（X-04 契约不回归）"
        )
