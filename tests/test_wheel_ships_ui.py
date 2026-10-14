"""wheel 必须带上 Web UI 静态文件(api/ui/*)。

不 mock:真实调用 `uv build --wheel` 产出 wheel,再读 zip 内容。
缺失时 /ui/ 路由返回 500(FileNotFoundError: api/ui/index.html),
线上 0.23.2 容器即是这种状态。
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI_DIR = REPO / "lantai" / "api" / "ui"


@pytest.mark.skipif(shutil.which("uv") is None, reason="需要 uv 构建 wheel")
def test_built_wheel_contains_ui_static_files(tmp_path):
    out = tmp_path / "dist"
    r = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    assert r.returncode == 0, f"uv build 失败:{r.stderr[-800:]}"

    wheels = list(out.glob("lantai-*.whl"))
    assert len(wheels) == 1, f"应产出且仅产出一个 wheel,实际:{wheels}"

    with zipfile.ZipFile(wheels[0]) as z:
        names = set(z.namelist())

    shipped = [n for n in names if n.startswith("lantai/api/ui/")]
    assert "lantai/api/ui/index.html" in names, "wheel 缺少 index.html,/ui/ 会 500"

    source_files = sorted(p.name for p in UI_DIR.iterdir() if p.is_file())
    missing = [f for f in source_files if f"lantai/api/ui/{f}" not in names]
    assert not missing, f"wheel 漏打 UI 文件:{missing}"
    assert len(shipped) == len(source_files)
