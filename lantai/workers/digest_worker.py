"""盘点 worker——TTL 归档任务（Ticket 02）。

每日清理：超龄 pending_review 候选 → rejected（归档）。
Ticket 03 将在此扩展每日盘点报告（digest）生成。
"""
from lantai.core.scheduler import record_run
from lantai.services.candidate_service import run_candidate_ttl_once


def run_candidate_ttl() -> dict:
    """每日 TTL 归档入口（scheduler job）。"""
    result = run_candidate_ttl_once()
    record_run("candidate_ttl")
    return result
