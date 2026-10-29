"""直书（Zhishu）REST 路由 (ADR-0064)。

提供确定性规则匹配分析与分轨诊断端点。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from lantai.models.schemas import ZhishuMatchReq
from lantai.services import zhishu_service

router = APIRouter(tags=["parsing"])


@router.post("/parsing/zhishu/match")
def match_zhishu_route(req: ZhishuMatchReq) -> dict[str, Any]:
    """直书规则匹配诊断接口。

    对输入文本执行确定性规则匹配分析，返回分轨意图与提取结构。
    """
    return zhishu_service.match_zhishu_service(req.text)
