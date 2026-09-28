"""反思（reflect）归属：A 触发一次，B 的记忆正文不再送进外部 LLM 提示词。

票 `.scratch/readside-gaps/14-reflect-leaks-cross-user.md`

**先说影响**：前十三票治的都是「A 从本系统读到 B 的数据」；这一票是
**A 能把 B 的数据送到系统外的 LLM**。`health_scan` 三处全表扫描一个身份
都不取，`_curate()` 把候选拼进 user prompt 调 `chat_json`——B 的记忆正文
原样出现在发给外部 LLM 的提示词里（第九轮 spy 直证）。

`wrap_as_data` 的 `<memory_data>` 围栏只防提示词注入，**不防归属**：
它假设「拼进提示词的内容本来就是有权看的」。所以围栏照旧，归属另修。

**三处扫描必须同批修**：候选集收窄后，related 与 theme 两处若漏，
B 的正文仍会经 related_texts 或 theme 触发进提示词。
"""

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

import lantai.storage.db as db_module
from lantai.core.acl import Principal
from lantai.core.time import utcnow
from lantai.models.tables import MemoryEdge, MemoryItem

# 独特机密串：断言它不出现在任何一次 LLM 调用的 prompt 里
SECRET = "B 的银行密码是 9527"


def _principal(user_id: str | None, *, role: str = "user"):
    return Principal(
        user_id=user_id,
        tenant_id=None,
        allowed_lanes=["general", "fact"],
        role=role,
    )


@pytest.fixture()
def rf_env():
    import lantai.models.tables  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    from lantai.storage.fts import init_fts

    with engine.connect() as conn:
        init_fts(conn.connection.driver_connection)

    def session_factory() -> Session:
        return Session(engine)

    with (
        patch.object(db_module, "get_session", session_factory),
        patch("lantai.storage.vector_store.ChromaVectorStore"),
        patch("lantai.llm.client.embed", return_value=[[0.1] * 8]),
    ):
        yield session_factory


def _add(sf, mem_id: str, user_id: str | None, content: str, **kw) -> None:
    fields = dict(
        title=mem_id,
        content=content,
        lane="fact",
        status="active",
        importance=0.9,
        decay_score=0.9,
        tier="working",
        use_count=0,
        helpful_count=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    fields.update(kw)
    with sf() as s:
        s.add(MemoryItem(id=mem_id, user_id=user_id, **fields))
        s.commit()


def _supersede(sf, old_id: str, new_id: str) -> None:
    """new supersedes old → old 命中 health_scan 的 R1「superseded_active」规则。"""
    with sf() as s:
        s.add(
            MemoryEdge(
                id=f"edge_{old_id}",
                source_memory_id=new_id,
                target_memory_id=old_id,
                relation="supersedes",
                weight=1.0,
                created_at=utcnow(),
            )
        )
        s.commit()


class PromptSpy:
    """包住 `chat_json`，记录每次调用的 user prompt 供断言。

    返回空 proposals——本票只关心「什么被送进了提示词」，
    不关心 curator 产出什么（那是别的测试的事）。
    """

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, sys_p, user_p):
        self.prompts.append(user_p)
        return {"proposals": []}

    def leaked(self, needle: str = SECRET) -> bool:
        return any(needle in p for p in self.prompts)


# ── 决定性：直证提示词出口 ──────────────────────────────────────


class TestReflectPromptLeak:
    def test_other_users_content_not_in_prompt(self, rf_env):
        """**Red 1（决定性）**：A 触发反思，B 的记忆正文不进 LLM 提示词。

        断言落在**提示词文本**上，不是 candidates 列表——候选集只是中间量，
        提示词才是内容真正离开本机的出口。
        """
        sf = rf_env
        # A 自己也有一条候选：否则收窄正确时 A 的候选集为空、反思直接 idle，
        # 一次 LLM 都不调——那样"没泄漏"是因为什么都没跑，证明不了归属生效。
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            res = run_reflect_once(source="manual", principal=_principal("user-A"))

        assert spy.prompts, "反思一次 LLM 都没调——测试没跑到出口，是空转的"
        assert res.get("skipped") is not True, f"反思被判为 idle，LLM 出口根本没走到：{res}"
        assert not spy.leaked(), (
            f"A 触发反思把 B 的记忆正文送进了外部 LLM 提示词（{len(spy.prompts)} 次调用）"
        )

    def test_own_superseded_memory_still_in_prompt(self, rf_env):
        """Red 2：A 自己的 superseded 记忆照常进提示词（收窄不是把功能修废）。"""
        sf = rf_env
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")

        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            run_reflect_once(source="manual", principal=_principal("user-A"))

        assert spy.prompts, "反思一次 LLM 都没调"
        assert any("A 的旧正文" in p for p in spy.prompts), "A 自己的记忆没进提示词（被误收窄？）"

    def test_null_owner_memory_still_in_prompt(self, rf_env):
        """Red 3：NULL 属主老记忆照常进提示词（单人部署下反思不能空转）。"""
        sf = rf_env
        _add(sf, "mem-null", None, "null 属主的旧正文")
        _add(sf, "mem-null-new", None, "null 属主的更正正文")
        _supersede(sf, "mem-null", "mem-null-new")

        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            run_reflect_once(source="manual", principal=_principal("user-A"))

        assert spy.prompts, "反思一次 LLM 都没调"
        assert any("null 属主的旧正文" in p for p in spy.prompts), "NULL 属主老记忆被漏掉了"

    def test_admin_sees_everything(self, rf_env):
        """Red 4：admin → 全表（与改动前逐字一致）。"""
        sf = rf_env
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            run_reflect_once(source="manual", principal=_principal(None, role="admin"))

        assert spy.prompts, "反思一次 LLM 都没调"
        assert spy.leaked(), "admin 下 B 的记忆没进提示词（被误收窄？）"

    def test_internal_call_unfiltered(self, rf_env):
        """Red 5：`principal=None`（scheduler 定时任务）→ 全表，行为不变。"""
        sf = rf_env
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            run_reflect_once(source="scheduled")

        assert spy.prompts, "反思一次 LLM 都没调"
        assert spy.leaked(), "定时任务下 B 的记忆没进提示词（被误收窄？）"


# ── 三条扫描路径逐条埋断言 ──────────────────────────────────────


class TestThreeScanPaths:
    def test_waterline_ignores_other_users_importance(self, rf_env):
        """**Red 6c（水位路径）**：B 的高价值新记忆不能把 A 的水位拱到触发线。

        水位只由自己的记忆累加——否则 A 库里明明没新增什么，却因为 B 写了一
        堆重要记忆而被触发一轮 theme 蒸馏（主题内容来自全表）。
        """
        sf = rf_env
        # B 写满高价值新记忆，把全局水位顶到默认阈值之上
        for i in range(8):
            _add(sf, f"wl-B-{i}", "user-B", f"B 的新记忆 {i}", importance=0.9)
        # A 自己只有一条候选，不构成水位
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")

        from lantai.core.settings import settings
        from lantai.evolution.reflector import _importance_waterline

        with rf_env() as s:
            own = _importance_waterline(s, principal=_principal("user-A"))
        assert own < settings.REFLECT_IMPORTANCE_POOL, (
            f"A 的水位被 B 的记忆拱到了 {own}（阈值 {settings.REFLECT_IMPORTANCE_POOL}）"
        )

        # 顺带：A 视角跑一轮，不泄漏也不被触发成 theme
        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            res = run_reflect_once(source="manual", principal=_principal("user-A"))
        assert spy.prompts, "反思一次 LLM 都没调"
        assert res.get("waterline") == round(own, 2), (
            f"返回的 waterline 与 A 视角不一致：{res.get('waterline')} vs {round(own, 2)}"
        )
        assert not spy.leaked(), "B 的新记忆经 theme 触发进了提示词（水位路径漏修）"

    def test_related_texts_also_scoped(self, rf_env):
        """**Red 6a（related 路径）**：B 的记忆纵然不进候选，也不能经
        related_texts 进提示词。

        related 取的是全表 active 记忆前 20 条拼成文本——这是**独立于候选集**
        的第二次泄漏面，候选集收窄了它照样漏。
        """
        sf = rf_env
        # B 的记忆不带 superseded 边 → 不进候选集，但会被 related 捞到
        _add(sf, "rel-B", "user-B", SECRET)
        # A 自己造一个候选，确保反思真的跑到 _curate
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")

        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            run_reflect_once(source="manual", principal=_principal("user-A"))

        assert spy.prompts, "反思一次 LLM 都没调"
        assert not spy.leaked(), "B 的记忆经 related_texts 进了提示词（第二条扫描路径漏修）"

    def test_theme_trigger_also_scoped(self, rf_env):
        """**Red 6b（theme 触发路径）**：B 的新记忆纵然不进候选、不进 related，
        也不能经 theme 触发（水位达标时全表扫新记忆）进提示词。
        """
        sf = rf_env
        # 关掉候选集：让 B 只有 theme 这一条路可走
        _add(sf, "theme-B", "user-B", SECRET)
        # A 自己造一个候选让反思真的跑起来
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")

        from lantai.core.settings import settings
        from lantai.evolution.reflector import run_reflect_once

        # 水位阈值压到 0，确保 theme 触发路径一定走到。
        # 注意 spy 必须是**独立变量**：`patch(...) as spy` 绑的是 MagicMock，
        # 那时 `spy.prompts` 也是 MagicMock，`SECRET in <MagicMock>` 恒为 False
        # ——断言全空转还不报错（票 13 的 403 空转同类坑，第二次踩）。
        spy = PromptSpy()
        with (
            patch.object(settings, "REFLECT_IMPORTANCE_POOL", 0.0),
            patch("lantai.evolution.reflector.chat_json", side_effect=spy),
        ):
            res = run_reflect_once(source="manual", principal=_principal("user-A"))

        assert spy.prompts, "反思一次 LLM 都没调"
        assert res.get("skipped") is not True, f"反思被判为 idle，theme 路径根本没走到：{res}"
        assert not any(SECRET in p for p in spy.prompts), (
            "B 的新记忆经 theme 触发进了提示词（第三条扫描路径漏修）"
        )


# ── 第四条扫描路径：rejecter 的 evidence_ids ────────────────────


class TestRejecterEvidenceScope:
    def test_foreign_evidence_id_not_in_rejecter_prompt(self, rf_env):
        """**Red 8（第四条扫描路径）**：curator 回一个**别人的记忆 id** 当
        evidence，该正文不得进 rejecter 提示词。

        前三处扫描（候选集 / related / theme）收窄后，这条是漏的：
        `prop.evidence_ids` 来自 LLM 输出，是**攻击者可控字段**——
        curator 只要回 `evidence_ids: ["别人的 id"]`，那条正文就被
        `s.get(MemoryItem, eid)` 按主键直读出来拼进提示词。
        第九轮探针实测（第 2 次 LLM 调用 = rejecter）抓到这条。
        """
        sf = rf_env
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")
        _add(sf, "mem-B", "user-B", SECRET)

        from lantai.evolution.reflector import run_reflect_once

        # curator 回一条指向 B 的记忆的 update 提案（模拟被诱导/幻觉编造）
        def curator(sys_p, user_p):
            return {
                "proposals": [
                    {
                        "proposal_type": "update",
                        "target_memory_id": "mem-A",
                        "new_content": "提案正文",
                        "reason": "probe",
                        "confidence": 0.95,
                        "evidence_ids": ["mem-B"],
                    }
                ]
            }

        spy = PromptSpy()
        # curator 回一条指向 B 的记忆的 update 提案（模拟被诱导/幻觉编造）。
        # 收窄生效时 rejecter 会因「无证据」直接判 high 不再调 LLM——
        # 所以断言不要求第 2 次调用一定发生，只要求 B 的正文一次都不出现。
        with patch("lantai.evolution.reflector.chat_json") as m:
            m.side_effect = [curator({}, {}), {"accept": True, "risk": "low"}]
            res = run_reflect_once(source="manual", principal=_principal("user-A"))
            for call in m.call_args_list:
                spy.prompts.append(call[0][1])

        assert spy.prompts, "反思一次 LLM 都没调——测试没跑到出口，是空转的"
        assert res.get("skipped") is not True, f"反思被判为 idle：{res}"
        assert not spy.leaked(), (
            f"B 的记忆经 curator 编造的 evidence_ids 进了 rejecter 提示词"
            f"（共 {len(spy.prompts)} 次调用）"
        )

    def test_own_evidence_id_still_in_rejecter_prompt(self, rf_env):
        """A 自己的证据照常进 rejecter 提示词（收窄不是把功能修废）。"""
        sf = rf_env
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")
        _add(sf, "ev-A", "user-A", "A 的证据正文")

        from lantai.evolution.reflector import run_reflect_once

        def curator(sys_p, user_p):
            return {
                "proposals": [
                    {
                        "proposal_type": "update",
                        "target_memory_id": "mem-A",
                        "new_content": "提案正文",
                        "reason": "probe",
                        "confidence": 0.95,
                        "evidence_ids": ["ev-A"],
                    }
                ]
            }

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json") as m:
            m.side_effect = [curator({}, {}), {"accept": True, "risk": "low"}]
            run_reflect_once(source="manual", principal=_principal("user-A"))
            for call in m.call_args_list:
                spy.prompts.append(call[0][1])

        # A 自己的证据必须让 rejecter 真的跑起来（第 2 次调用）——
        # 否则"没泄漏"又是因为什么都没跑，证明不了 scope 只挡了别人。
        assert len(spy.prompts) >= 2, f"rejecter 没被调用（自己的证据也没取到）：{len(spy.prompts)} 次"
        assert any("A 的证据正文" in p for p in spy.prompts), "A 自己的证据没进 rejecter 提示词（被误收窄？）"


# ── MCP 层 ─────────────────────────────────────────────────────


class TestReflectMcpIdentity:
    def test_mcp_handler_scopes_by_user_id(self, rf_env):
        """**Red 7（MCP 层）**：宿主透传 user_id 时，MCP reflect_run 同样不泄漏。"""
        sf = rf_env
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.cli.mcp import handle_reflect_run

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            res = handle_reflect_run({"source": "manual", "user_id": "user-A"})

        assert spy.prompts, "反思一次 LLM 都没调"
        assert res.get("skipped") is not True, f"反思被判为 idle，LLM 出口根本没走到：{res}"
        assert not spy.leaked(), "经 MCP 触发反思把 B 的记忆正文送进了提示词"

    def test_mcp_handler_without_user_id_unfiltered(self, rf_env):
        """MCP 不透传 user_id → 不过滤（不猜身份，票 10 口径）。"""
        sf = rf_env
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.cli.mcp import handle_reflect_run

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            handle_reflect_run({"source": "manual"})

        assert spy.prompts, "反思一次 LLM 都没调"
        assert spy.leaked(), "不透传身份时被误收窄了"


# ── 前后快照口径一致 ───────────────────────────────────────────


class TestBeforeAfterSnapshot:
    def test_scan_after_uses_same_scope(self, rf_env):
        """`scan_after` 与 `scan_before` 必须同口径，否则健康快照自证失效。

        这不是泄漏（snapshot 只有计数），但口径不一致会让 `health_before` 与
        `health_after` 不可比——A 看到"处理了 5 条"而库里其实还有 3 条别人的
        没动，digest 反思统计随之失真。
        """
        sf = rf_env
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")
        # B 的 superseded 记忆：正确 scope 下两边都不数它
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.evolution.reflector import run_reflect_once

        spy = PromptSpy()
        with patch("lantai.evolution.reflector.chat_json", side_effect=spy):
            res = run_reflect_once(source="manual", principal=_principal("user-A"))

        before, after = res.get("health_before"), res.get("health_after")
        assert before is not None and after is not None, f"快照缺失：{res}"
        assert before.get("superseded_active") == 1, f"A 视角的 superseded 计数不对：{before}"
        assert after.get("superseded_active") == 1, (
            f"scan_after 用了不同口径，把别人的记忆数进来了：{after}"
        )


# ── health_scan 单独验（它是纯读函数，可直接断言候选集）────────


class TestHealthScanScope:
    def test_health_scan_excludes_other_user(self, rf_env):
        """候选集本身也要收窄（不只靠提示词断言兜底）。"""
        sf = rf_env
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.evolution.reflector import health_scan

        with sf() as s:
            scan = health_scan(s, principal=_principal("user-A"))
        ids = [c["memory_id"] for c in scan["candidates"]]
        assert "mem-B" not in ids, f"B 的记忆进了 A 的反思候选集：{ids}"

    def test_health_scan_keeps_own_and_null(self, rf_env):
        """A 自己的 + NULL 属主的都还在（收窄不是把功能修废）。"""
        sf = rf_env
        _add(sf, "mem-A", "user-A", "A 的旧正文")
        _add(sf, "mem-A-new", "user-A", "A 的更正正文")
        _supersede(sf, "mem-A", "mem-A-new")
        _add(sf, "mem-null", None, "null 属主的旧正文")
        _add(sf, "mem-null-new", None, "null 属主的更正正文")
        _supersede(sf, "mem-null", "mem-null-new")

        from lantai.evolution.reflector import health_scan

        with sf() as s:
            scan = health_scan(s, principal=_principal("user-A"))
        ids = [c["memory_id"] for c in scan["candidates"]]
        assert "mem-A" in ids, f"A 自己的记忆被漏掉了：{ids}"
        assert "mem-null" in ids, f"NULL 属主老记忆被漏掉了：{ids}"

    def test_health_scan_internal_call_unfiltered(self, rf_env):
        """`principal=None` → 全表（scheduler 口径）。"""
        sf = rf_env
        _add(sf, "mem-B", "user-B", SECRET)
        _add(sf, "mem-B-new", "user-B", "B 的更正正文")
        _supersede(sf, "mem-B", "mem-B-new")

        from lantai.evolution.reflector import health_scan

        with sf() as s:
            scan = health_scan(s)
        ids = [c["memory_id"] for c in scan["candidates"]]
        assert "mem-B" in ids, f"内部调用被收窄了：{ids}"
