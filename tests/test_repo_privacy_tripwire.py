r"""X-05 隐私绊线：本机身份与记忆产物目录不得进入版本库（票 05-v0660-release-gate.md）。

两条绊线都直读 git 的真实输出，不 mock、不降级为 warning：

1. **记忆产物目录**：`docs/memory-offload/`（卸载全文落盘）与 `docs/memory-wiki/`
   （Wiki 渲染落盘）写的是**用户记忆正文**。它们曾被 track（`2b41b47b` 加入 /
   `bbbc4496` 删除）。只要 ignore 规则被改掉、或同类新目录忘了登记，用户记忆原文
   就会进公开仓库——本绊线同时验「未被 track」与「被 .gitignore 真的忽略」。

2. **本机身份字面量**：保护性代码（评测双闸的生产路径黑名单）里写死了
   `C:\Users\<本机用户名>\AppData\Local\remembrance-data`，把开发者真实姓名连同
   公开仓库一起交出去。双闸的保护不依赖字面量：运行时推导即可，且推导值在本机与
   原字面量逐字相同（实测见票面续23 的红→绿证据）。

红线：不得改成「命中就 skip」。git 不可用时判定无法成立必须失败（宁红不静默）；
也不得把扫描范围缩到某个子目录来凑绿。主机身份在**模块导入期**冻结——pytest 的
fixture 可能临时改写 HOME，现取 `Path.home()` 会退化成「扫一个临时目录」，测试
静默变绿正是本票禁止的假绿形态（`test_frozen_identity_is_a_real_host_home` 把守）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BS = chr(92)  # 反斜杠：显式取字符，避免源码里写转义字面量（本文件自身的纪律）

# 记忆产物目录：任何一条被 track、或被 ignore 漏掉，都是隐私外泄面
MEMORY_OUTPUT_DIRS = ("docs/memory-offload", "docs/memory-wiki")

# 冻结时刻 = 模块导入（collection 阶段），早于任何 fixture 改写环境
_HOST_HOME = str(Path.home())
_HOST_USER = _HOST_HOME.replace(BS, "/").rstrip("/").split("/")[-1]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True, check=False)


def _dec(b: bytes) -> str:
    return b.decode("utf-8", "replace")


def _tracked_files() -> list[str]:
    r = _git("ls-files", "-z")
    if r.returncode != 0:
        pytest.fail("git ls-files 失败，隐私绊线无法成立（不得静默放行）: " + _dec(r.stderr))
    return [f for f in _dec(r.stdout).split("\0") if f]


def _identity_needles() -> list[bytes]:
    """本机身份的多种写法（Windows / POSIX / 分隔符变体 / UTF-16）。"""
    cands = {
        _HOST_HOME,
        _HOST_HOME.replace(BS, "/"),
        f"/home/{_HOST_USER}",
        f"/Users/{_HOST_USER}",
        f"C:{BS}Users{BS}{_HOST_USER}",
    }
    out: list[bytes] = []
    for c in sorted(x for x in cands if len(x) > 2):
        out.append(c.encode("utf-8"))
        out.append(c.encode("utf-16-le"))
    return out


class TestMemoryOutputDirsStayOutOfVcs:
    def test_nothing_tracked_under_memory_output_dirs(self):
        offenders: list[str] = []
        for d in MEMORY_OUTPUT_DIRS:
            files = [f for f in _dec(_git("ls-files", "-z", d).stdout).split("\0") if f]
            offenders.extend(files)
        assert not offenders, f"记忆产物目录被 git track（用户记忆正文会进公开仓库）: {offenders}"

    @pytest.mark.parametrize("d", MEMORY_OUTPUT_DIRS, ids=list(MEMORY_OUTPUT_DIRS))
    def test_memory_output_dir_is_ignored(self, d):
        r = _git("check-ignore", "-q", f"{d}/probe.md")
        assert r.returncode == 0, f"{d}/ 不在 .gitignore 覆盖内：记忆落盘目录随时可能被提交"


class TestNoHostIdentityLiteralsInTrackedFiles:
    def test_frozen_identity_is_a_real_host_home(self):
        """冻结值必须是真实主机目录，否则绊线没有判定对象——宁红不静默。"""
        low = _HOST_HOME.replace(BS, "/").lower() + "/"
        ephemeral = [
            s for s in ("/tmp/", "/temp/", "local/temp/", "/private/var/folders/") if s in low
        ]
        assert not ephemeral, (
            f"主机 home 落在临时目录 {_HOST_HOME!r}（命中 {ephemeral}），绊线不可信"
        )
        assert _HOST_USER and len(_HOST_USER) > 1, f"未能取得可用主机用户名：{_HOST_HOME!r}"

    def test_tracked_files_do_not_carry_host_identity(self):
        needles = _identity_needles()
        hits: list[str] = []
        for rel in _tracked_files():
            p = REPO / rel
            if not p.is_file():
                continue
            try:
                blob = p.read_bytes()
            except OSError:
                continue
            for needle in needles:
                at = blob.find(needle)
                if at >= 0:
                    line = blob[:at].count(b"\n") + 1
                    hits.append(f"{rel}:{line} 命中 {_dec(needle)!r}")
                    break
        assert not hits, (
            "已跟踪文件里出现本机身份字面量（真实用户名进入公开仓库）；"
            "保护性黑名单应由运行时推导，不要写死:\n  " + "\n  ".join(hits)
        )
