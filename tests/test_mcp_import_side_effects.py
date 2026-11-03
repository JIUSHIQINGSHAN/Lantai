"""生产模块 import 期不得改写进程全局（票 05 续15，G2 的生产侧延伸 + MCP 副作用取证）。

`lantai/cli/mcp.py` 过去在 **import 期**做两件事：

1. `sys.path.insert(0, <仓库根>/lantai)` —— 注释声称「让子进程能 import lantai」，
   实际插进去的是包目录本身（`import lantai` 需要的是仓库根，不是 `lantai/`），
   而它真正的效果是把 `core / models / gate / api / eval / memory / services …`
   这些平铺名字变成**可解析**并排在 site-packages 之前。仓库里 `from core|models|…`
   这种平铺 import 的命中数是 **0**，也就是没有任何消费者，只有影子解析风险；
   且每次 import 都插一次（历史上同类写法在全量跑里放大成 +2574 条重复项）。
2. `sys.stdin/stdout.reconfigure("utf-8")` —— 这是 stdio 服务进程的需求（Hermes 按
   UTF-8 写 JSON，Windows 默认 GBK 会把中文 query 变成乱码），却落在**任何 importer**
   身上：测试进程、门禁的 MCP 探针、以及任何把 `lantai.cli.mcp` 当库用的调用方，
   都会在不自知的情况下被换掉标准流编码。

两者都不可恢复、不可审计（发生在 import 期，harness 无从登记），因此按 G2 同一口径
判红。修复方式是把它们收进**服务入口** `main()`，import 只定义、不改写进程。

第 5 条判据是反向保险：中文 stdio 契约必须继续成立——不能为了「不污染全局」把
UTF-8 这件事一起删掉（那会把真缺陷换成另一个真缺陷）。
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LANTAI_PKG = REPO_ROOT / "lantai"

# import lantai/ 目录进 sys.path 后会被影子解析的名字（生产包目录名）
SHADOW_NAMES = (
    "core",
    "models",
    "services",
    "retrieval",
    "gate",
    "storage",
    "llm",
    "ingestion",
    "memory",
    "parameters",
    "observability",
    "api",
    "eval",
    "evolution",
    "parsing",
    "cli",
)

# 探针先载入 pkg_resources（巡检 2026-11-03）：jieba._compat 在 import 时 try 导入
# pkg_resources，而 setuptools<81 的 pkg_resources 会把自己的 setuptools/_vendor 追加进
# sys.path。CI 的 toolcache Python 3.11 自带这类 setuptools，于是本用例在 CI 稳定失败，
# 本地 uv 环境（uv.lock 不含 setuptools）却稳定通过。这是 setuptools 自己的行为，
# 不是兰台生产代码改 sys.path；先载入只排除这一个来源，兰台模块和其余三方库
# 在 import 期对 sys.path 的任何改动仍然会被报出来。
_PROBE_IMPORT = """
import importlib.util, json, sys
if importlib.util.find_spec("pkg_resources") is not None:
    import pkg_resources  # noqa: F401  setuptools 自己的 sys.path 追加，见上方注释
before = list(sys.path)
out_before = sys.stdout.encoding
in_before = sys.stdin.encoding
import lantai.cli.mcp  # noqa: F401
added = [p for p in sys.path if p not in before]
shadow = [n for n in SHADOW_NAMES_LIST if importlib.util.find_spec(n) is not None]
print(json.dumps({
    "added": added,
    "shadow": shadow,
    "stdout_before": out_before,
    "stdout_after": sys.stdout.encoding,
    "stdin_before": in_before,
    "stdin_after": sys.stdin.encoding,
}))
"""


def _run_probe(script: str, tmp_path: Path, extra_env: dict | None = None) -> dict:
    probe = tmp_path / "probe.py"
    probe.write_text(script, encoding="utf-8", newline="\n")
    env = dict(__import__("os").environ)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env["LANTAI_HOME"] = str(home)
    env["REMEMBRANCE_HOME"] = str(home)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "gbk"  # 强制非 UTF-8，让「被改写」可观测
    env["PYTHONUTF8"] = "0"
    env.update(extra_env or {})
    proc = subprocess.run(
        [sys.executable, "-X", "utf8=0", str(probe)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"探针进程退出码 {proc.returncode}：\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    )
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")][-1]
    return json.loads(line)


def _module_scope_global_writes(root: Path) -> list[str]:
    """lantai/** 里**模块级**（不进函数/类体）的进程全局改写。"""
    skip = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    def walk_module_scope(stmt):
        stack = [stmt]
        while stack:
            node = stack.pop()
            if isinstance(node, skip):
                continue
            yield node
            stack.extend(ast.iter_child_nodes(node))

    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for stmt in tree.body:
            for sub in walk_module_scope(stmt):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    val = sub.func.value
                    if (
                        isinstance(val, ast.Attribute)
                        and isinstance(val.value, ast.Name)
                        and val.value.id == "sys"
                    ):
                        if val.attr == "path" and sub.func.attr in (
                            "insert",
                            "append",
                            "extend",
                            "remove",
                        ):
                            hits.append(f"{rel}:{sub.lineno} sys.path.{sub.func.attr}(...)")
                        elif (
                            val.attr in ("stdout", "stdin", "stderr")
                            and sub.func.attr == "reconfigure"
                        ):
                            hits.append(f"{rel}:{sub.lineno} sys.{val.attr}.reconfigure(...)")
                if isinstance(sub, (ast.Assign, ast.AugAssign, ast.Delete)):
                    if isinstance(sub, ast.Assign):
                        targets = sub.targets
                    elif isinstance(sub, ast.AugAssign):
                        targets = [sub.target]
                    else:
                        targets = sub.targets
                    for tgt in targets:
                        if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Attribute):
                            base = tgt.value.value
                            if (
                                isinstance(base, ast.Name)
                                and base.id == "os"
                                and tgt.value.attr == "environ"
                            ):
                                hits.append(f"{rel}:{sub.lineno} os.environ 模块级写")
    return hits


def test_importing_mcp_does_not_mutate_sys_path(tmp_path):
    """import 生产模块不得往 sys.path 里塞目录。"""
    report = _run_probe(
        _PROBE_IMPORT.replace("SHADOW_NAMES_LIST", repr(list(SHADOW_NAMES))), tmp_path
    )
    assert report["added"] == [], f"import lantai.cli.mcp 改写了 sys.path：{report['added']}"


def test_importing_mcp_does_not_create_shadow_imports(tmp_path):
    """塞进去的包目录会让 core/models/api/eval… 变成可平铺解析的影子名。"""
    report = _run_probe(
        _PROBE_IMPORT.replace("SHADOW_NAMES_LIST", repr(list(SHADOW_NAMES))), tmp_path
    )
    assert report["shadow"] == [], (
        f"import 后这些名字被影子暴露（排在 site-packages 之前）：{report['shadow']}"
    )


def test_importing_mcp_does_not_reconfigure_std_streams(tmp_path):
    """stdio 编码属于服务进程，不属于 import 路径。"""
    report = _run_probe(
        _PROBE_IMPORT.replace("SHADOW_NAMES_LIST", repr(list(SHADOW_NAMES))), tmp_path
    )
    assert report["stdout_before"] == report["stdout_after"], (
        f"import 把 sys.stdout 从 {report['stdout_before']} 改成 {report['stdout_after']}"
    )
    assert report["stdin_before"] == report["stdin_after"], (
        f"import 把 sys.stdin 从 {report['stdin_before']} 改成 {report['stdin_after']}"
    )


def test_lantai_has_no_module_scope_process_global_writes():
    """生产侧长期锁：lantai/** 的模块级进程全局改写必须为 0（续15 前实测 3 处）。"""
    hits = _module_scope_global_writes(LANTAI_PKG)
    assert hits == [], "生产模块在 import 期改写进程全局：\n" + "\n".join(hits)


def test_installed_mcp_entrypoint_speaks_utf8_from_foreign_cwd(tmp_path):
    """发布产物的真入口（console script `lantai-mcp`）必须在任意 cwd 下讲 UTF-8。

    这条判据同时兜住两件事：① 当年 `sys.path.insert` 声称要解决的「cwd 不可控」
    场景，靠的是安装态而不是 import 期改 sys.path；② stdio UTF-8 修复确实挂在
    服务入口上（把 `_configure_stdio_utf8()` 删掉的话，这里严格解码就会炸）。
    """
    import os

    bindir = Path(sys.executable).parent
    exe = next((p for p in (bindir / "lantai-mcp.exe", bindir / "lantai-mcp") if p.is_file()), None)
    assert exe is not None, (
        f"发布入口脚本缺失（{bindir} 下没有 lantai-mcp）：安装态没注册 console script"
    )

    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        {
            "LANTAI_HOME": str(home),
            "REMEMBRANCE_HOME": str(home),
            "PYTHONIOENCODING": "gbk",
            "PYTHONUTF8": "0",
        }
    )
    env.pop("PYTHONPATH", None)  # 不靠 PYTHONPATH 兜底：走真实安装态
    env.pop("LANTAI_MCP_PROFILE", None)
    env.pop("MCP_TOOL_PROFILE", None)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    proc = subprocess.run(
        [str(exe)],
        cwd=str(tmp_path),  # 故意与仓库无关的 cwd
        env=env,
        input=(json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
        capture_output=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    text_out = proc.stdout.decode("utf-8")
    payload = [json.loads(ln) for ln in text_out.splitlines() if ln.strip()]
    assert payload and "tools" in payload[0]["result"], text_out[:500]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in json.dumps(payload, ensure_ascii=False)), (
        "真入口的响应里没有中文工具描述"
    )
    assert "\ufffd" not in text_out, "出现替换符：UTF-8 链路断了"


def test_mcp_stdio_server_still_speaks_utf8_cjk(tmp_path):
    """反向保险：服务入口的 UTF-8 stdio 契约必须继续成立（中文描述不得变乱码）。

    子进程显式 `PYTHONIOENCODING=gbk`：若服务入口不再把 stdio 修成 UTF-8，
    `ensure_ascii=False` 写出的中文就是 GBK 字节，严格 UTF-8 解码当场失败。
    """
    home = tmp_path / "home"
    home.mkdir()
    env = dict(__import__("os").environ)
    env.update(
        {
            "LANTAI_HOME": str(home),
            "REMEMBRANCE_HOME": str(home),
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONIOENCODING": "gbk",
            "PYTHONUTF8": "0",
        }
    )
    env.pop("LANTAI_MCP_PROFILE", None)
    env.pop("MCP_TOOL_PROFILE", None)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    proc = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-m", "lantai.cli.mcp"],
        cwd=str(REPO_ROOT),
        env=env,
        input=(json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
        capture_output=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    text = proc.stdout.decode("utf-8")  # 严格解码：GBK 字节会在这里炸
    payload = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    assert payload and "tools" in payload[0]["result"], text[:500]
    joined = json.dumps(payload, ensure_ascii=False)
    cjk = [ch for ch in joined if "\u4e00" <= ch <= "\u9fff"]
    assert cjk, "tools/list 响应里没有中文——描述被吞或编码链路坏了"
    assert "\ufffd" not in text and "鍏" not in text and "浣犲ソ" not in text, (
        "出现乱码替换符/GBK 误读痕迹"
    )
