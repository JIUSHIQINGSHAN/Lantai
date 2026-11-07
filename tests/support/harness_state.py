"""测试装配期的全局状态唯一写入器：登记原值 + 会话结束复原（票 05 续14，验收底线 G2）。

为什么需要它（`docs/research/gate-finding-v0660.md` 判 G2 **违反** 的四处裸写）：

* `tests/conftest.py` 在 import 期裸写 `os.environ.setdefault("OPENAI_API_KEY"/
  "OPENAI_BASE_URL", ...)`，全程不恢复。`setdefault` 在宿主已配真 key 时**不覆盖**——
  测试进程于是带着真实凭据指向 `https://api.openai.com/v1`，而该 host 正躺在
  `settings.ALLOWED_API_HOSTS` 白名单里，连生产校验 `validate_api_url()` 都不会拦它。
  `_stub_external_llm` 的替身网只要漏一个绑定就是**真连网**：真扣费、真把记忆全文外传。
* 同一文件把生产配置单例 `settings.FEATURE_*` 原地翻成 True 且没有 teardown：全量跑绿
  并不代表**出厂默认配置**下的行为。
* `tests/test_release_check.py` 永久改写 `os.environ["PATH"]`。
* `tests/test_recall_chain.py` 的 `__exit__` 无条件 `pop("LANTAI_INTENT_OFF")`，
  把用户原本配过的值一起吞掉。

纪律：测试侧要动全局环境/配置，只允许经过本模块。每次写入登记「首次接触前的原值」，
会话结束由 `tests/conftest.py::pytest_unconfigure` 调 `restore()` 无条件复原。
第三类（票 .scratch/v0.68-next-window-2026-11/04）：`scrub_foreign_sys_path()` 对
sys.path 只删不增、无残留面（进程退出即终），故不复原，但删除清单入
`registry_view()["syspath_removals"]` 台账可审计——AST 扫描器对本文件的
SYSPATH_WRITE 豁免仅限此形态。
`tests/test_harness_global_state_isolation.py` 用 AST 静态扫描锁死这条边界（`tests/`
内除本模块以外的裸写一律判红），并用**真实子会话**证明退出时复原确实发生。

隐私边界：审计快照里名字含 KEY/TOKEN/SECRET/PASSWORD 的环境变量与配置字段一律只记
指纹与长度——门禁证据文件不得携带真实凭据。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path

# ── 装配策略（唯一声明处；conftest 只负责应用，测试据此核对）────────────
# 黑洞端点：环回地址 + discard(9) 端口，任何进程都无从经它出网。刻意用 http://——
# `validate_api_url()` 要求 https + host 白名单，于是即便某个绑定逃过替身，真实
# 客户端在发请求**之前**就会被生产校验本身拒掉（失败即断网，不是失败即外传）。
HARNESS_LLM_BLACKHOLE_URL = "http://127.0.0.1:9/v1"
HARNESS_LLM_FAKE_KEY = "test-key"
AUDIT_ENV_KEY = "LANTAI_TEST_HARNESS_AUDIT"

HARNESS_ENV_POLICY = {
    "OPENAI_API_KEY": HARNESS_LLM_FAKE_KEY,
    "OPENAI_BASE_URL": HARNESS_LLM_BLACKHOLE_URL,
}

# 测试需要 Obsidian/维基/案牍/视觉/终端五条特性线；出厂默认里 OBSIDIAN 与 VISION 是
# False，所以这几项属于**装配期改写**，必须登记并复原——否则「全量绿」悄悄替默认配置
# 背了书（G2）。
HARNESS_FEATURE_FLAG_POLICY = {
    "FEATURE_OBSIDIAN": True,
    "FEATURE_WIKI": True,
    "FEATURE_WORK_ITEMS": True,
    "FEATURE_VISION": True,
    "FEATURE_TERMINAL": True,
}

_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
_ABSENT = object()

_ENV_AT_IMPORT = dict(os.environ)
_ENV_WRITES: dict[str, object] = {}
_SETTINGS_WRITES: dict[tuple[int, str], dict] = {}
_LAST_REPORT: dict | None = None


def _is_secret(name: str) -> bool:
    upper = name.upper()
    return any(hint in upper for hint in _SECRET_HINTS)


def mask(name: str, value: object) -> dict:
    """把「变量名 + 值」变成可安全写盘的证据条目（敏感项只留指纹）。"""
    if value is _ABSENT or value is None:
        return {"present": False, "value": None, "fingerprint": None}
    text = str(value)
    if _is_secret(name):
        return {
            "present": True,
            "value": None,
            "fingerprint": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "length": len(text),
        }
    return {"present": True, "value": text, "fingerprint": None}


def set_env(name: str, value: str) -> str:
    """登记式写环境变量：只记首次接触前的原值，写入本身照常生效。"""
    if name not in _ENV_WRITES:
        _ENV_WRITES[name] = os.environ.get(name, _ABSENT)
    os.environ[name] = value
    return value


def unset_env(name: str) -> None:
    """登记式删除环境变量：复原时会把原值写回来。"""
    if name not in _ENV_WRITES:
        _ENV_WRITES[name] = os.environ.get(name, _ABSENT)
    os.environ.pop(name, None)


@contextmanager
def env_context(name: str, value: str):
    """临时环境变量：离开 with 时**精确复原**（原本没有才删除，原本有则写回原值）。

    与 `monkeypatch.setenv` 同语义，但不依赖 fixture——给 `with` 形态的测试夹具
    （如 `test_recall_chain.ChainHarness`）用，避免它们手写 `os.environ[...]`。
    """
    previous = os.environ.get(name, _ABSENT)
    os.environ[name] = value
    try:
        yield value
    finally:
        if previous is _ABSENT:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def set_settings_fields(target: object, **fields: object) -> None:
    """登记式改配置对象字段（会话结束复原）。装配期与隔离期对单例的改写都走这里。"""
    for name, value in fields.items():
        key = (id(target), name)
        if key not in _SETTINGS_WRITES:
            _SETTINGS_WRITES[key] = {"target": target, "before": getattr(target, name, _ABSENT)}
        setattr(target, name, value)


def apply_env_policy() -> None:
    for name, value in HARNESS_ENV_POLICY.items():
        set_env(name, value)


def apply_feature_flag_policy(settings_obj: object) -> None:
    set_settings_fields(settings_obj, **HARNESS_FEATURE_FLAG_POLICY)


_SYSPATH_REMOVALS: list[str] = []


def scrub_foreign_sys_path() -> list[str]:
    """清出会遮蔽本仓依赖的外来 sys.path 条目（票 .scratch/v0.68-next-window-2026-11/04）。

    PYTHONPATH 条目在 sys.path 上先于活动 venv 的 site-packages——宿主环境的
    第三方包（实测：hermes venv 的 pydantic，为宿主解释器编译）会抢解析并
    二进制不兼容崩。只清「既不在本仓根下、也不在活动解释器 prefix 下」的
    条目：套件按声明 venv 的依赖运行，不吃宿主环境。会话级一次性装配，
    只删不增、无残留面（进程退出即终），故不做复原；删了什么全在
    `registry_view()["syspath_removals"]` 里可审计。调用时机必须早于
    conftest 的任何 lantai./sqlmodel 级 import——晚了就已被遮蔽。
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    prefixes = [Path(sys.prefix).resolve()]
    base_prefix = getattr(sys, "base_prefix", None)
    if base_prefix:
        prefixes.append(Path(base_prefix).resolve())
    kept = [os.path.normcase(str(p)) for p in [repo_root, *prefixes]]
    removed: list[str] = []
    for entry in list(sys.path):
        if not entry:
            continue
        norm = os.path.normcase(os.path.realpath(entry))
        if any(norm == k or norm.startswith(k + os.sep) for k in kept):
            continue
        sys.path.remove(entry)
        removed.append(entry)
    _SYSPATH_REMOVALS.extend(removed)
    if removed:
        print(f"[harness] 已清外来 sys.path 条目（票 04 遮蔽防御）：{removed}", file=sys.stderr)
    return removed


def registry_view() -> dict:
    """当前登记面（供测试核对「写了什么、原值是什么」，敏感项只露指纹）。"""
    return {
        "env_writes": {name: mask(name, before) for name, before in _ENV_WRITES.items()},
        "settings_writes": {
            name: mask(name, rec["before"]) for (_, name), rec in _SETTINGS_WRITES.items()
        },
        "syspath_removals": list(_SYSPATH_REMOVALS),
    }


def restore() -> dict:
    """复原全部登记过的全局写入，并返回可审计报告（幂等，可重复调用）。"""
    env_entries = {}
    for name, before in _ENV_WRITES.items():
        at_teardown = os.environ.get(name, _ABSENT)
        if before is _ABSENT:
            os.environ.pop(name, None)
        else:
            os.environ[name] = before  # type: ignore[assignment]
        after = os.environ.get(name, _ABSENT)
        env_entries[name] = {
            "before": mask(name, before),
            "at_teardown": mask(name, at_teardown),
            "after": mask(name, after),
            "exact": after == before,
        }

    settings_entries = {}
    for (_target_id, name), rec in _SETTINGS_WRITES.items():
        target = rec["target"]
        before = rec["before"]
        at_teardown = getattr(target, name, _ABSENT)
        if before is _ABSENT:
            with contextlib.suppress(AttributeError):
                delattr(target, name)
        else:
            setattr(target, name, before)
        after = getattr(target, name, _ABSENT)
        settings_entries[name] = {
            "before": mask(name, before),
            "at_teardown": mask(name, at_teardown),
            "after": mask(name, after),
            "exact": after == before,
        }

    residue = sorted(name for name, e in env_entries.items() if not e["exact"])
    settings_residue = sorted(name for name, e in settings_entries.items() if not e["exact"])
    report = {
        "env_writes_registered": len(env_entries),
        "settings_writes_registered": len(settings_entries),
        "env": env_entries,
        "settings": settings_entries,
        "env_residue": residue,
        "settings_residue": settings_residue,
        "env_reverted_exact": not residue,
        "settings_reverted_exact": not settings_residue,
        "keys_added_since_import": sorted(set(os.environ) - set(_ENV_AT_IMPORT)),
        "ambient_openai_key_restored": os.environ.get("OPENAI_API_KEY")
        == _ENV_AT_IMPORT.get("OPENAI_API_KEY"),
        "ambient_openai_base_url_restored": os.environ.get("OPENAI_BASE_URL")
        == _ENV_AT_IMPORT.get("OPENAI_BASE_URL"),
        "blackhole_url_gone_after_restore": all(
            v != HARNESS_LLM_BLACKHOLE_URL for v in os.environ.values()
        ),
    }
    global _LAST_REPORT
    _LAST_REPORT = report
    _write_audit(report)
    return report


def last_report() -> dict | None:
    return _LAST_REPORT


def _write_audit(report: dict) -> None:
    """仅当调用方显式给出审计路径时落盘（不给则零副作用）。"""
    path = os.environ.get(AUDIT_ENV_KEY, "")
    if not path:
        return
    import json
    from pathlib import Path

    Path(path).write_text(
        json.dumps({"report": report, "env_at_import_names": sorted(_ENV_AT_IMPORT)}, indent=2),
        encoding="utf-8",
    )
