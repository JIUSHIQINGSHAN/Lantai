"""票 25：发布门禁 schema 漂移探针的索引维度接线。

门禁盲区实况（复查轮亲核 `scripts/release_check.py` 的 `_SCHEMA_PROBE_CODE`）：
只比列存在 + NULL 约束，且只探**全新库**（临时 LANTAI_HOME 里 init_db）——
升级库（表在、索引缺）的漂移在门禁下天然不可见：全新库 create_all 天然带
全部模型索引，探针恒绿。本票给探针补三件：①存量库形态模拟（索引剥离 +
身份列缺失）→ 真 apply_migrations 全链 → 索引对账；②upgrade 库
user_version 对 TARGET 核对（迁移未应用拦截）；③二次 apply 幂等核对
（重复应用拦截）。纯函数接线用 payload 单测锁死——漂移 payload 必须变成
gate issue（主流程非零退出的直接输入），干净 payload 必须零 issue。
"""

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_release_check():
    """scripts/ 非包，按路径加载（main 有 __main__ 守卫，import 副作用为零）。"""
    name = "release_check_gate"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / "release_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_index_drift_payload_becomes_gate_issue():
    mod = _load_release_check()
    issues = mod.schema_drift_issues_from_payload(
        {"index_drift": ["memoryitem: 缺 ix_memoryitem_user_id"]}
    )
    assert any("索引漂移" in i for i in issues), (
        f"索引漂移 payload 未被门禁拦截（票 25 门禁盲区）：{issues}"
    )


def test_version_mismatch_becomes_gate_issue():
    mod = _load_release_check()
    issues = mod.schema_drift_issues_from_payload(
        {"upgrade_user_version": 37, "upgrade_target": 40}
    )
    assert any("迁移版本不一致" in i for i in issues), (
        f"迁移未推到 TARGET 未被门禁拦截（票 25）：{issues}"
    )


def test_non_idempotent_apply_becomes_gate_issue():
    mod = _load_release_check()
    issues = mod.schema_drift_issues_from_payload({"idempotent": False})
    assert any("重复应用" in i for i in issues), (
        f"迁移重复应用不幂等未被门禁拦截（票 25）：{issues}"
    )


def test_clean_payload_passes():
    mod = _load_release_check()
    issues = mod.schema_drift_issues_from_payload(
        {
            "migration_failures": [],
            "missing_tables": [],
            "drift": [],
            "index_drift": [],
            "idempotent": True,
            "upgrade_user_version": 40,
            "upgrade_target": 40,
        }
    )
    assert issues == [], f"干净 payload 不应产生 issue：{issues}"


def test_gate_probe_upgrade_path_converges():
    """端到端绿锁（现状锁性质，如实申报）：门禁探针真跑一次 init_db +
    升级库形态模拟，修复后 upgrade 库索引集必须收敛到模型声明集、版本到
    TARGET、二次 apply 幂等。修复前探针无这些维度（本测不红），红证据由
    tests/test_index_sync_migration.py 主臂与上面三条接线单测承担。"""
    mod = _load_release_check()
    payload = mod.schema_drift_probe(_REPO_ROOT)
    assert payload.get("index_drift") == [], (
        f"门禁探针 upgrade 库索引未收敛（票 25）：{payload.get('index_drift')}"
    )
    assert payload.get("idempotent") is True, "门禁探针二次 apply 不幂等（票 25）"
    assert payload.get("upgrade_user_version") == payload.get("upgrade_target"), (
        f"门禁探针 upgrade 库版本未到 TARGET：{payload.get('upgrade_user_version')}"
        f" != {payload.get('upgrade_target')}"
    )
