"""run_ingest 真冒烟（v0.68 测试诚实性审计补强）。

审计结论：`POST /ingest/run` 与其后端 `source_service.run_ingest` 此前
**零测试覆盖**——`/ingest/run` 在 tests/ 里只以注释形态出现，摄取总入口
从未真实执行过。本文件直调 `run_ingest` 并打一次真实 HTTP 路由。

空库语义：无待摄取来源 → 循环空转 → 仍返回 ok（窗口不满足时参数建议
同样空转），不需要任何外部网络。
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select


def test_run_ingest_no_sources_smoke(param_env):
    """直调 run_ingest：空库空转主路径真实执行，返回 ok。"""
    from lantai.services.source_service import run_ingest

    result = run_ingest()
    assert result == {"ok": True}


def test_ingest_run_route_smoke(monkeypatch):
    """HTTP 接缝:POST /ingest/run 真实路由到 run_ingest(控制台「立即摄取」按钮的合同)。

    v0.68 票 19 起该路由挂 `require_admin`(admin 角色仅来自环境 API_KEY),
    本测试按 admin 面真实调用——不带 X-API-Key 在配了 API_KEY 的部署(含 CI)
    下恒 403,DEV MODE 才放行,那不是该路由的合同。
    """
    from lantai.api.app import app
    from lantai.core.settings import settings
    from lantai.models.tables import IngestJob, Source
    from lantai.storage import db

    admin_key = "ingest-run-smoke-admin"
    monkeypatch.setattr(settings, "API_KEY", admin_key)

    # 会话级共享库上可能有前序 API 测试留下的来源；本测试只验证空转语义，
    # 且绝不能带着别人的真实 URL 出网——先清空摄取面。
    with db.get_session() as s:
        for row in s.exec(select(IngestJob)).all():
            s.delete(row)
        for row in s.exec(select(Source)).all():
            s.delete(row)
        s.commit()

    with TestClient(app) as client:
        resp = client.post("/ingest/run", headers={"X-API-Key": admin_key})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}
