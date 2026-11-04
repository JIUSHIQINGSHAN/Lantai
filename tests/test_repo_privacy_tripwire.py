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


# 不承载个人身份的系统账户（巡检 2026-11-04）：以 root 跑测试时（Docker、devcontainer、
# sudo pytest）home 是 `/root`，用户名是英文常用词，裸 needle `/root` 会命中
# `e1e2-lang-drift-2026-10/root-cause-exp.jsonl`、`bucket/root 摘要` 这类正文，
# 绊线在干净仓库上判红（实测 5 个文件假红）。对这类账户只认「带尾分隔符的 home
# 路径」：真写进仓库的 root home 子路径（如 home 下的 .lantai）照样咬住，正文里的 root
# 单词不再误伤。不是 skip：判定对象仍然存在，只是换成不会撞上普通英文的写法。
# 本文件自身也在扫描范围内：不要在这里写出「斜杠 root 斜杠」的字面量，样本一律拼接。
_NON_PERSONAL_ACCOUNTS = frozenset({"root"})


def _scan_blob(blob: bytes, needles: list[bytes]) -> tuple[int, bytes] | None:
    """返回首个命中的 (行号, needle)；没命中返回 None。"""
    for needle in needles:
        at = blob.find(needle)
        if at >= 0:
            return blob[:at].count(b"\n") + 1, needle
    return None


def _scan_tracked_files(needles: list[bytes]) -> list[str]:
    hits: list[str] = []
    for rel in _tracked_files():
        p = REPO / rel
        if not p.is_file():
            continue
        try:
            blob = p.read_bytes()
        except OSError:
            continue
        hit = _scan_blob(blob, needles)
        if hit:
            hits.append(f"{rel}:{hit[0]} 命中 {_dec(hit[1])!r}")
    return hits


def _identity_needles(host_home: str | None = None) -> list[bytes]:
    """本机身份的多种写法（Windows / POSIX / 分隔符变体 / UTF-16）。

    `host_home` 只给回归测试用；绊线本身永远用导入期冻结的 `_HOST_HOME`。
    """
    home = _HOST_HOME if host_home is None else host_home
    user = home.replace(BS, "/").rstrip("/").split("/")[-1]
    if user.lower() in _NON_PERSONAL_ACCOUNTS:
        posix_home = home.replace(BS, "/").rstrip("/")
        cands = {
            posix_home + "/",
            f"/home/{user}/",
            f"/Users/{user}/",
            f"C:{BS}Users{BS}{user}{BS}",
        }
    else:
        cands = {
            home,
            home.replace(BS, "/"),
            f"/home/{user}",
            f"/Users/{user}",
            f"C:{BS}Users{BS}{user}",
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
        hits = _scan_tracked_files(_identity_needles())
        assert not hits, (
            "已跟踪文件里出现本机身份字面量（真实用户名进入公开仓库）；"
            "保护性黑名单应由运行时推导，不要写死:\n  " + "\n  ".join(hits)
        )


class TestNonPersonalAccountNeedles:
    """巡检 2026-11-04：以 root 身份跑全量时绊线在干净仓库上假红（5 个文件命中裸 `/root`）。"""

    ROOT_HOME = "/" + "root"
    ROOT_DIR = ROOT_HOME + "/"

    def test_root_needles_require_trailing_separator(self):
        needles = _identity_needles(self.ROOT_HOME)
        assert self.ROOT_DIR.encode("utf-8") in needles
        assert self.ROOT_HOME.encode("utf-8") not in needles
        assert all(not n.endswith(b"root") for n in needles)

    def test_root_needles_skip_english_prose(self):
        needles = _identity_needles(self.ROOT_HOME)
        prose = (
            "docs/benchmarks/e1e2-lang-drift-2026-10" + self.ROOT_HOME + "-cause-exp.jsonl\n"
            "dataset 级 bucket" + self.ROOT_HOME + " 摘要\n"
        ).encode("utf-8")
        assert _scan_blob(prose, needles) is None

    def test_root_needles_still_catch_real_home_paths(self):
        needles = _identity_needles(self.ROOT_HOME)
        leak = ('LANTAI_HOME = "' + self.ROOT_DIR + '.lantai"\n').encode("utf-8")
        assert _scan_blob(b"line1\n" + leak, needles) == (2, self.ROOT_DIR.encode("utf-8"))
        leak16 = (self.ROOT_DIR + ".lantai").encode("utf-16-le")
        assert _scan_blob(leak16, needles) is not None

    def test_personal_account_needles_unchanged(self):
        needles = _identity_needles("/home/alice")
        for bare in ("/home/alice", "/Users/alice", f"C:{BS}Users{BS}alice"):
            assert bare.encode("utf-8") in needles
            assert bare.encode("utf-16-le") in needles
        win = _identity_needles(f"C:{BS}Users{BS}alice")
        assert f"C:{BS}Users{BS}alice".encode() in win
        assert b"C:/Users/alice" in win

    def test_real_repo_is_clean_under_root_identity(self):
        """真实仓库按 root 身份扫一遍必须是 0 命中（修复前是 5 个文件假红）。"""
        hits = _scan_tracked_files(_identity_needles(self.ROOT_HOME))
        assert not hits, "root 身份下仍有命中:\n  " + "\n  ".join(hits)
