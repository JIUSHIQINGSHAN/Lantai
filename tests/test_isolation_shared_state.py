"""跨测试状态隔离回归（票 `.scratch/v0.66-next-window-2026-11/issues/01-test-isolation-fixture-leak.md`）。

四类泄漏各配一对「污染者 → 受害者」用例：pytest 在同一文件内按定义顺序执行，
配对顺序本身就是断言的一部分——护栏缺失时受害者用例必红，护栏到位时全绿。

1. 常驻注入面（CoreMemoryBlock / PersonaProfile / SessionCheckpoint / PromptTemplate /
   api_keys）写在会话级共享测试库上且无人复位：后续 `build_context("你好") == {}`
   一类断言读出脏数据。实证污染者：`test_console_api_contracts`（核心记忆+模板）、
   `test_diben_api_smoke`/`test_diben_engine_smoke`（会话检查点 41 行）、`test_shishi_api_smoke`。
2. `app.dependency_overrides` 是进程级生产全局：测试体内裸赋值不清，后续 API 用例
   以别人的身份打路由，行级 ACL 把目标行滤空 → 莫名 404 / 零召回。
   实证污染者：`test_xuanjing_topology_smoke.py:223`（连坐 zuozheng seam2、search force）。
3. `lantai.retrieval.hybrid` 模块级绑定：fixture 内自带的 `MonkeyPatch.context()` 与
   测试体内 `monkeypatch` 是两个独立撤销栈，叠加时撕卸顺序错位把陈旧替身回写，
   遗留给后续全部测试。实证污染者：`conftest.param_env` × `test_fence.py`（连坐 staged 家族 8 例）。
4. `scripts/shell_hook.py` 在 import 期无条件 `sys.path.insert(0, ...)`：模块被反复
   `exec_module` 时 sys.path 无界增长（v0.65 全量实测 +2574 条、收尾 2752 条重复项）。

口径：真实构造、真实 SQL、真实路由对象，不 mock 被测逻辑（AGENTS.md 测试纪律）。
"""

import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "scripts" / "shell_hook.py"

# 常驻注入面（SQLModel 实际表名）：这些行会被 shell_hook 的 `_build_resident_persona`
# 无条件置顶注入，也会把 DEV MODE 鉴权翻面——跨测试残留即顺序敏感失败。
_RESIDENT_TABLES = (
    "corememoryblock",
    "persona_profile",
    "session_checkpoint",
    "prompttemplate",
    "api_keys",
)


def _resident_counts():
    """直读会话级测试库统计常驻注入面行数（真 SQL，不走替身）。"""
    from sqlalchemy import text

    import lantai.storage.db as db_module

    out = {}
    with db_module.engine.connect() as conn:
        for table in _RESIDENT_TABLES:
            out[table] = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
    return out


def _root_entries(paths):
    return [p for p in paths if str(p).rstrip(os.sep) == str(REPO_ROOT)]


def _load_hook_copy(tag):
    """按宿主形态从文件加载一份 shell_hook 模块（与 `test_shell_hook._load_hook` 同构）。"""
    spec = importlib.util.spec_from_file_location(f"shell_hook_{tag}", str(HOOK_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. 常驻注入面复位 ──────────────────────────────────────────────


def test_1a_resident_writer_really_writes_into_shared_db():
    """污染者：用真实 service 把五张常驻注入面表都写脏。

    先自证「确实落库」——否则 1b 的空表断言可能只是因为写失败而假绿。
    """
    import lantai.storage.db as db_module
    from lantai.core.auth import create_api_key
    from lantai.models.tables import PromptTemplate
    from lantai.services.checkpoint_service import write_session_checkpoint
    from lantai.services.memory_service import put_core_memory
    from lantai.services.persona_service import ensure_default_persona

    ensure_default_persona()
    put_core_memory("policy", "隔离回归临时策略块")
    write_session_checkpoint(
        "sess-isolation-probe", {"cp_active_intent": "验证隔离", "cp_next_action": "复位"}
    )
    # create_api_key 是纯工厂（不落库），按既有测试的形态自己 add 进行话库
    _, api_key_obj = create_api_key("isolation-probe", ["general"])
    with db_module.get_session() as s:
        s.add(api_key_obj)
        s.add(
            PromptTemplate(id="prompt-isolation-probe", template="模板 {q}", description="隔离回归")
        )
        s.commit()

    counts = _resident_counts()
    missing = {name: n for name, n in counts.items() if not n}
    assert missing == {}, f"污染者未写满常驻注入面，受害者断言将失去意义：{counts}"


def test_1b_resident_channels_are_reset_before_next_test():
    """受害者：上一个测试写的常驻注入面必须已被复位。"""
    from lantai.core.auth import db_has_api_key

    dirty = {name: n for name, n in _resident_counts().items() if n}
    assert dirty == {}, f"跨测试残留常驻注入面行，后续 build_context 断言会读出脏数据：{dirty}"
    assert db_has_api_key() is False, "api_keys 残留会关掉 DEV MODE，后续鉴权走另一条路"


# ── 2. app.dependency_overrides（生产全局）─────────────────────────


def test_2a_app_dependency_override_applies_in_test_body():
    """污染者：复刻 `test_xuanjing_topology_smoke` 的裸赋值形态（无 teardown）。"""
    from lantai.api.app import app
    from lantai.core.auth import Principal, get_current_user

    app.dependency_overrides[get_current_user] = lambda: Principal(
        user_id="isolation-probe-user", tenant_id="t-isolation", role="user"
    )
    assert get_current_user in app.dependency_overrides


def test_2b_app_dependency_overrides_are_pristine_for_next_test():
    """受害者：鉴权覆盖表必须回到生产基线（空）。

    残留等于身份劫持：后续用例带着别人的 user_id/tenant_id 打路由，行级 ACL
    把数据滤空，表现为与业务无关的 404 与零召回。
    """
    from lantai.api.app import app

    left = sorted(getattr(k, "__name__", str(k)) for k in app.dependency_overrides)
    assert left == [], f"上一测试遗留 app.dependency_overrides：{left}"


# ── 3. hybrid 模块级绑定 ───────────────────────────────────────────


def test_3a_overlay_patch_on_param_env_target(param_env, monkeypatch):
    """污染者：param_env 已 patch 过 `hybrid.get_vector_store`，测试体内再叠一层。

    这正是 `test_fence.py` 的形态：两层 patch 属两个撤销栈，顺序错位时外层会把
    内层的陈旧替身当作「原值」写回。
    """
    import lantai.retrieval.hybrid as hybrid_module

    class _ProbeStore:
        def search(self, *args, **kwargs):
            return []

        def has(self, *args, **kwargs):
            return True

    monkeypatch.setattr(hybrid_module, "get_vector_store", lambda: _ProbeStore())
    assert callable(hybrid_module.get_vector_store)


def test_3b_hybrid_bindings_are_still_the_production_objects():
    """受害者：`hybrid.get_vector_store` 必须仍是 vector_store 模块里的生产函数。

    被替身顶掉后检索链静默走假向量库（恒空/恒真），下游召回断言与
    「索引失同步」锚点全部失真——且单跑永远复现不出来。
    """
    import lantai.retrieval.hybrid as hybrid_module
    import lantai.storage.vector_store as vs_module

    assert hybrid_module.get_vector_store is vs_module.get_vector_store, (
        "lantai.retrieval.hybrid.get_vector_store 被测试遗留：后续检索走的是替身"
    )


# ── 4. hook import 期写 sys.path（生产全局）────────────────────────


def test_4a_repeated_hook_import_does_not_grow_sys_path():
    """shell_hook 被重复 exec_module 时不得继续往 sys.path 塞同一个根目录。

    宿主 CLI 只 import 一次；测试/插件路径会反复加载同一文件，无界增长
    把解释器搜索路径变成噪声源，并拖慢之后的每一次 import。
    """
    before = list(sys.path)
    _load_hook_copy("probe1")
    _load_hook_copy("probe2")
    after = list(sys.path)

    assert len(after) == len(before), (
        f"加载 shell_hook 改写了进程级 sys.path：{len(before)} → {len(after)} 条"
    )
    assert len(_root_entries(after)) == len(_root_entries(before)), (
        "仓库根目录在 sys.path 中重复出现（import 期写入未做幂等）"
    )


def test_4b_hook_contract_survives_reimport():
    """冒烟：幂等修复后 hook 仍按契约工作（空查询且库干净 → 返回 {}）。"""
    mod = _load_hook_copy("probe3")
    assert mod.build_context("") == {}


# ── 5. 绑定残留：兜底复位不得被当成容忍（票 05 续8）────────────────


def test_binding_residue_policy_tolerates_only_the_declared_sentinel():
    """意外残留必须阻断会话——「护栏已复位」不等于「测试没问题」。

    `test_3a` 是**申报过的**污染哨兵（它故意造出两层撤销栈错位，由 `test_3b` 验证
    受害者不受损），所以 conftest 允许它留残留。问题是：全量每次都会打那一行
    「请修污染源，勿依赖兜底」，却没有任何机制区分「申报的哨兵」和「新出现的泄漏」——
    下一次真有人忘了还原 `hybrid.embed`，横幅会和今天的噪音混在一起，绿灯照拿。
    """
    import conftest

    sentinel = "tests/test_isolation_shared_state.py::test_3a_overlay_patch_on_param_env_target"
    assert sentinel in conftest.DECLARED_POLLUTION_SENTINELS, (
        "哨兵 nodeid 改名了就要同步改申报表，否则策略空转"
    )
    assert conftest.unexpected_binding_residue([(sentinel, ["脏"])]) == []

    accidental = "tests/test_something_else.py::test_forgets_to_restore"
    bad = conftest.unexpected_binding_residue([(accidental, ["脏"])])
    assert len(bad) == 1 and bad[0][0] == accidental, f"意外残留被容忍了：{bad}"

    mixed = conftest.unexpected_binding_residue([(sentinel, ["a"]), (accidental, ["b"])])
    assert [nodeid for nodeid, _ in mixed] == [accidental]
