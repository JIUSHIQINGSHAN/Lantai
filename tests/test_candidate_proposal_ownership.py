"""候选/提案归属：票 .scratch/readside-gaps/02

`/candidates/*`（8 个 handler）与 `/proposals`、`/proposals/{id}/decide`
一个身份都不取。两条后果，第二条更严重：

1. **读侧**：`GET /candidates/pending` 与 `GET /proposals` 全表捞，
   `summary` / `reason` 就是待审正文——而 AGENTS.md 明写
   `GET /candidates/pending` 是「宁 miss 不脏写」的裁决入口。
2. **写侧**：A 能替 B 裁决。实测每条独立新建 B 的资源再打，全部 200：
   review(approve=false) 把 B 的候选打成 rejected、defer 改 B 的到期日、
   refine 改 B 的摘要、decide 替 B 裁提案、batch/reject、batch/defer。

**这是「宁 miss 不脏写」被击穿**：低置信度候选本该由属主裁决，A 一条
`approve:false` 就永久驳回，B 不会收到任何通知。票 ownership-gaps/05 修
`batch/organize` 时堵的是 memory 分支，candidate/proposal 分支同一个文件、
同一种漏法，没被覆盖。

测试策略同 ownership-gaps 系列：真实临时 SQLite + 真实产品路径，不 mock
内部计算；断言落在**行为**与**落库事实**上——403 必须伴随「库里那行
没被改」，否则只是把错误码提前了。
"""

from fastapi.testclient import TestClient

from lantai.core.auth import Principal, get_current_user
from lantai.core.time import utcnow
from lantai.models.tables import MemoryCandidate, MemoryProposal


def _principal(user_id: str, *, role: str = "user", tenant_id: str | None = None):
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        allowed_lanes=["general", "fact", "preference", "policy"],
        role=role,
    )


def _as(principal, fn):
    from lantai.api.app import app

    app.dependency_overrides[get_current_user] = lambda: principal
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _app():
    from lantai.api.app import app

    return app


def _seed_candidate(session_factory, cand_id: str, user_id: str, summary: str):
    now = utcnow()
    with session_factory() as s:
        s.add(
            MemoryCandidate(
                id=cand_id,
                tenant_id="tenant-1",
                user_id=user_id,
                agent_id=None,
                session_id=None,
                document_id="doc-1",
                summary=summary,
                status="pending_review",
                extractor_confidence=0.9,
                lane="general",
                created_at=now,
                updated_at=now,
                review_due_at=now,
            )
        )
        s.commit()


def _seed_proposal(session_factory, prop_id: str, user_id: str, reason: str):
    now = utcnow()
    with session_factory() as s:
        s.add(
            MemoryProposal(
                id=prop_id,
                tenant_id="tenant-1",
                user_id=user_id,
                candidate_id=None,
                proposal_type="add",
                status="pending",
                target_memory_id=None,
                reason=reason,
                proposed_patch={"content": "新内容"},
                confidence=0.9,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()


SECRET = "B 的机密：连接池 100，数据库密码 hunter2"


# ── Red 1（决定性）：A 不能替 B 裁决候选 ────────────────────────


class TestCandidateAdjudicationOwnership:
    def test_other_user_cannot_reject_candidate(self, param_env):
        """A 拒绝 B 的候选必须 403，且库里 B 的候选仍是 pending_review。

        现状：200 + 状态已被改成 rejected。这是本票最严重的一条——
        不是读不到，是**别人的校验判断被 A 做了**。
        """
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post(
                    "/candidates/cand-B/review", json={"approve": False, "reason": "我讨厌这条"}
                ),
            )

        assert resp.status_code == 403, f"A 替 B 驳回了候选：{resp.status_code} {resp.text[:200]}"
        with session_factory() as s:
            row = s.get(MemoryCandidate, "cand-B")
            assert row is not None
            assert row.status == "pending_review", (
                f"403 伴随了落库——B 的候选被改成了 {row.status!r}，错误码只是提前了"
            )

    def test_other_user_cannot_defer_candidate(self, param_env):
        """A 不能改 B 候选的到期日。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/candidates/cand-B/defer", json={"days": 3}),
            )

        assert resp.status_code == 403, (
            f"A 改了 B 候选的到期日：{resp.status_code} {resp.text[:200]}"
        )
        with session_factory() as s:
            row = s.get(MemoryCandidate, "cand-B")
            assert row.defer_count in (None, 0), f"defer_count 被改成 {row.defer_count}"

    def test_other_user_cannot_refine_candidate(self, param_env):
        """A 不能改写 B 候选的摘要（refine 会连 status 一起改）。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(_principal("user-A"), lambda: c.post("/candidates/cand-B/refine"))

        assert resp.status_code == 403, f"A 提纯了 B 的候选：{resp.status_code} {resp.text[:200]}"
        with session_factory() as s:
            row = s.get(MemoryCandidate, "cand-B")
            assert row.summary == SECRET, f"B 的摘要被 A 改成了 {row.summary!r}"

    def test_owner_can_still_reject_own_candidate(self, param_env):
        """B 自己照旧能拒自己的候选——不能把功能修废。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-B"),
                lambda: c.post(
                    "/candidates/cand-B/review", json={"approve": False, "reason": "我自己的判断"}
                ),
            )

        assert resp.status_code == 200, resp.text
        with session_factory() as s:
            assert s.get(MemoryCandidate, "cand-B").status == "rejected"

    def test_admin_can_still_reject_any_candidate(self, param_env):
        """admin 照旧全权——不能把运维流程修废。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(
                _principal("admin-1", role="admin"),
                lambda: c.post(
                    "/candidates/cand-B/review", json={"approve": False, "reason": "运维清理"}
                ),
            )

        assert resp.status_code == 200, resp.text
        with session_factory() as s:
            assert s.get(MemoryCandidate, "cand-B").status == "rejected"


# ── Red 2：A 不能替 B 裁决提案 ──────────────────────────────────


class TestProposalAdjudicationOwnership:
    def test_other_user_cannot_decide_proposal(self, param_env):
        """A 裁决 B 的提案必须 403，且提案仍 pending。"""
        session_factory, _ = param_env
        _seed_proposal(session_factory, "prop-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-A"),
                lambda: c.post("/proposals/prop-B/decide", json={"approve": False, "reason": "x"}),
            )

        assert resp.status_code == 403, f"A 裁了 B 的提案：{resp.status_code} {resp.text[:200]}"
        with session_factory() as s:
            row = s.get(MemoryProposal, "prop-B")
            assert row.status == "pending", f"B 的提案被改成 {row.status!r}"

    def test_owner_can_still_decide_own_proposal(self, param_env):
        """B 自己照旧能裁自己的提案。"""
        session_factory, _ = param_env
        _seed_proposal(session_factory, "prop-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(
                _principal("user-B"),
                lambda: c.post(
                    "/proposals/prop-B/decide", json={"approve": False, "reason": "我的判断"}
                ),
            )

        assert resp.status_code == 200, resp.text
        with session_factory() as s:
            assert s.get(MemoryProposal, "prop-B").status == "rejected"


# ── Red 3：批量入口同样不放宽（票 05 已确立的口径） ──────────────


class TestBatchAdjudicationOwnership:
    def test_batch_reject_of_other_user_fails_per_item(self, param_env):
        """batch/reject 含 B 的候选：B 的进 failed、ok=False，A 的照旧成功。

        部分失败的语义不能被「要么全拒要么全过」取代——票 05 同款口径。
        """
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-A", "user-A", "A 的候选")
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        from lantai.models.work_items import BatchItemRef, BatchRejectRequest
        from lantai.services.work_item_action_service import batch_reject

        result = batch_reject(
            BatchRejectRequest(
                reason="批量清理",
                items=[
                    BatchItemRef(kind="candidate", source_id="cand-A"),
                    BatchItemRef(kind="candidate", source_id="cand-B"),
                ],
            ),
            principal=_principal("user-A"),
        )
        assert result.ok is False
        assert [i["source_id"] for i in result.succeeded] == ["cand-A"]
        assert [i["source_id"] for i in result.failed] == ["cand-B"]
        with session_factory() as s:
            assert s.get(MemoryCandidate, "cand-A").status == "rejected"
            assert s.get(MemoryCandidate, "cand-B").status == "pending_review", (
                "批量把他人候选一起拒了——批量不放宽单条校验（票 05 口径）"
            )

    def test_batch_defer_of_other_user_fails_per_item(self, param_env):
        """batch/defer 同上。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        from lantai.models.work_items import BatchDeferRequest
        from lantai.services.work_item_action_service import batch_defer

        result = batch_defer(
            BatchDeferRequest(items=[{"candidate_id": "cand-B"}], days=3),
            principal=_principal("user-A"),
        )
        assert result.ok is False
        assert len(result.failed) == 1
        with session_factory() as s:
            row = s.get(MemoryCandidate, "cand-B")
            assert row.defer_count in (None, 0), (
                f"B 的候选被延期了（defer_count={row.defer_count}）"
            )


# ── Red 5：AI 批量预审——请求体 id 与建议列表无绑定 ─────────────


class TestAiTriageAdjudicationOwnership:
    def test_batch_apply_triage_rejects_other_users_candidate(self, param_env):
        """`/candidates/batch_apply_triage` 的 id 是调用方给的。

        这个入口最阴：预审返回的建议列表和这里提交的 `id` **没有任何绑定
        关系**——A 根本不需要预审到 B 的候选，直接 POST
        `{"actions": [{"id": "cand-B", "action": "reject"}]}` 就能驳回它。
        此前服务端对 id 照单全收。
        """
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            # 响应是什么无所谓：这个端点的失败是以 `applied["failed"]` 计数
            # 表达的，200 照样可能改库。判据只能是库里的状态。
            _as(
                _principal("user-A"),
                lambda: c.post(
                    "/candidates/batch_apply_triage",
                    json={
                        "actions": [{"id": "cand-B", "action": "reject", "reason": "我想拒就拒"}]
                    },
                ),
            )

        with session_factory() as s:
            row = s.get(MemoryCandidate, "cand-B")
            assert row.status == "pending_review", (
                f"A 借批量预审驳回了 B 的候选（status={row.status!r}）"
            )

    def test_ai_triage_scan_excludes_other_users(self, param_env):
        """预审把候选正文送进 LLM——A 的扫描不该捞到 B 的候选。

        断言只能落在 `total` 与建议 id 上：响应里**不含**候选正文
        （正文是送进 LLM 的输入，不是输出），所以「SECRET 不在响应里」
        这条判据抓不住漏——变异验证实测 M14b/M14c 全放过。
        """
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-A", "user-A", "A 的候选")
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(_principal("user-A"), lambda: c.post("/candidates/ai_triage"))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1, f"A 的预审扫描捞到了 {body['total']} 条（含 B 的）"
        assert [r["id"] for r in body["recommendations"]] == ["cand-A"], (
            f"A 的预审建议里出现了 B 的候选：{[r['id'] for r in body['recommendations']]}"
        )


# ── Red 4：读侧不再全表捞 ───────────────────────────────────────


class TestCandidateProposalReadOwnership:
    def test_pending_list_excludes_other_users(self, param_env):
        """A 列待审候选不该看见 B 的候选正文。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-A", "user-A", "A 的候选")
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/candidates/pending"))

        assert resp.status_code == 200, resp.text
        ids = [x["id"] for x in resp.json()["candidates"]]
        assert "cand-A" in ids, "A 自己的候选该被看见（不能修废）"
        assert "cand-B" not in ids, f"A 看见了 B 的候选：{ids}"
        assert SECRET not in resp.text

    def test_proposals_list_excludes_other_users(self, param_env):
        """A 列提案不该看见 B 的提案。"""
        session_factory, _ = param_env
        _seed_proposal(session_factory, "prop-A", "user-A", "A 的提案")
        _seed_proposal(session_factory, "prop-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(_principal("user-A"), lambda: c.get("/proposals"))

        assert resp.status_code == 200, resp.text
        ids = [x["id"] for x in resp.json()["proposals"]]
        assert "prop-A" in ids, "A 自己的提案该被看见（不能修废）"
        assert "prop-B" not in ids, f"A 看见了 B 的提案：{ids}"
        assert SECRET not in resp.text

    def test_admin_sees_all_pending(self, param_env):
        """admin 照见全部。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-A", "user-A", "A 的候选")
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        with TestClient(_app()) as c:
            resp = _as(_principal("admin-1", role="admin"), lambda: c.get("/candidates/pending"))

        assert resp.status_code == 200, resp.text
        ids = {x["id"] for x in resp.json()["candidates"]}
        assert ids == {"cand-A", "cand-B"}, f"admin 看不到全部：{ids}"

    def test_internal_call_converges_to_default(self, param_env):
        """principal=None（内部调用）按 "default" 收敛，不漏别人的。"""
        session_factory, _ = param_env
        _seed_candidate(session_factory, "cand-def", "default", "未记录归属的候选")
        _seed_candidate(session_factory, "cand-B", "user-B", SECRET)

        from lantai.services.candidate_service import list_pending_candidates

        result = list_pending_candidates()
        ids = {x["id"] for x in result["candidates"]}
        assert "cand-def" in ids, "default 归属的候选该被内部调用看见"
        assert "cand-B" not in ids, f"内部调用漏了别人的候选：{ids}"
