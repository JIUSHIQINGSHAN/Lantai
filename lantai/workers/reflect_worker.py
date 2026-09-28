"""反思 worker（spec: docs/plans/reflection-module-spec.md）

- run_reflect_once：健康扫描 → 蒸馏 → 提案化 → 自动应用/待审 → 健康快照自证
  核心逻辑在 lantai/evolution/reflector.py；record_run 由核心函数内部记录。

归属（票 `.scratch/mcp-identity-gaps/06`）：定时反思要的是**全表**，
但不能再靠 `principal=None` 表达——那个值在 MCP 入口面的含义是
「宿主没透传身份」。两种含义混用一个值，无身份的 MCP 调用就继承了
worker 的全表权限（票 04/05/06 连续三票同一根因）。
故显式传系统身份 `acl.SYSTEM_VIEWER`。
"""

from lantai.core.acl import SYSTEM_VIEWER, Principal
from lantai.evolution.reflector import run_reflect_once as _run_reflect


def run_reflect_once() -> dict:
    res = _run_reflect(
        source="scheduled",
        principal=Principal(
            tenant_id=None,
            user_id=SYSTEM_VIEWER,
            agent_id=None,
            session_id=None,
            role="system",
            allowed_lanes=None,
        ),
    )
    try:
        from lantai.cognition.reflection import ReflectionEngine
        from lantai.storage import db

        with db.get_session() as s:
            engine = ReflectionEngine(s)
            report = engine.run_reflection()
            res["cognitive_reflection"] = {
                "new_patterns": report.new_patterns,
                "belief_candidates": report.belief_candidates,
                "rule_candidates": report.rule_candidates,
                "rules_weakened": report.rules_weakened,
                "failures": report.failures,
                # 票 cognitive-write-gaps/01：`FailureRecord` 无归属列，
                # 这个数跨用户——如实标注，别让读 worker 输出的人误判。
                "failures_scoped": report.failures_scoped,
                "summary": report.summary,
            }
    except Exception:
        pass
    return res
