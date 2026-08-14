"""autodream 蒸馏 worker——7 天周期后台蒸馏（Fog 项：autodream 7 天周期记忆蒸馏）。

后台合成 → 待审提案（decided_by="autodream"，人工闸门裁决，宁 miss 不脏写）。
record_run("autodream") 供 /stats 与启动补跑体系观测。
"""
from lantai.core.scheduler import record_run
from lantai.core.settings import settings
from lantai.evolution.autodream import run_autodream_once


def run_autodream_scheduled() -> dict:
    """周期入口（scheduler job）：一轮蒸馏落 pending 提案 + 记录运行时间。"""
    result = run_autodream_once(namespace="default", dry_run=False)
    record_run("autodream")
    return result


def autodream_cron_days() -> int:
    """周期天数（settings 默认 7 = 每周一次；零硬编码）。"""
    return settings.AUTODREAM_CRON_DAYS
