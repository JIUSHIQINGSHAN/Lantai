"""票 70：UI 出口防护三病——CDN 无 SRI 供应链面、全站零安全头、API Key 明文持久。

`.scratch/v0.68-next-window-2026-11/issues/70-ui-outbound-defense-family.md`

**先说影响**（三病灶同属「浏览器出口防护缺失」，同族合票先例：票 48/68）：

| 病灶 | 修复前 |
|---|---|
| `ui/index.html:691` d3 走 jsdelivr CDN 无 `integrity` | CDN 投毒/DNS 劫持 → 控制台页同源任意 JS → 读 localStorage API Key；每次开页出站请求违背本地第一 |
| 全站无 CSP/X-Frame-Options/nosniff（app.py 无安全头中间件） | 任意第三方页可 iframe 控制台点击劫持；XSS 得手后无纵深 |
| recall 页（routes_ui.py）填过 Key 就无条件 `localStorage.setItem` | 与主控制台 api.js 的「记住此设备」开关语义不齐（api.js 已有开关，recall 页没跟上） |

**修法**：d3 去远端化（本地资产 `d3.v7.min.js` 已在仓，删 CDN 行）；app.py 统一
安全头中间件（CSP 按内联用量白名单化——routes_ui 内联页 11 个 script、
index.html 40 处内联样式，故 script/style-src 需 'unsafe-inline'，仍拦外部
脚本注入这一真实威胁）；recall 页补「记住此设备」开关默认 sessionStorage。

**测试边界声明**：Key 落盘臂是纯前端 JS，pytest 不覆盖（票面 TDD 3 明示服务端
可测面为 CDN/安全头两臂，api.js/recall 页改动以代码评审 + Answer 登记手工
验证步骤验收）；d3 资产存在臂是票面 TDD 4 的防假绿回归锁（删 CDN 忘落本地
文件 → 星图/悬镜全瘫）。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from lantai.api.routes_ui import _UI_ASSET_DIR

_SCRIPT_SRC_RE = re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>")


class TestNoUnverifiedRemoteScript:
    """修复目标 1（TDD 要求 1）：index.html 不得引用无 integrity 的远端脚本。"""

    def test_index_html_has_no_bare_remote_script(self):
        html = (_UI_ASSET_DIR / "index.html").read_text(encoding="utf-8")
        matches = list(_SCRIPT_SRC_RE.finditer(html))
        remote = [
            m.group(1) for m in matches if m.group(1).startswith(("http://", "https://", "//"))
        ]
        assert remote == [], f"index.html 引用了远端脚本（无本地化）：{remote}"
        # 本地化后 d3 与 app.js 必须以本地路径加载
        local_srcs = [m.group(1) for m in matches]
        assert "/ui/assets/d3.v7.min.js" in local_srcs, f"d3 未本地加载：{local_srcs}"


class TestSecurityHeaders:
    """修复目标 2（TDD 要求 2）：统一安全头中间件——/ui 与静态资产三头在场。"""

    def test_ui_and_assets_carry_security_headers(self):
        from lantai.api.app import app

        client = TestClient(app)
        for path in ("/ui", "/ui/assets/app.js", "/ui/assets/d3.v7.min.js"):
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"
            assert resp.headers.get("X-Content-Type-Options") == "nosniff", f"{path} 缺 nosniff"
            assert resp.headers.get("X-Frame-Options") == "DENY", f"{path} 缺 X-Frame-Options"
            csp = resp.headers.get("Content-Security-Policy", "")
            assert "default-src 'self'" in csp, f"{path} CSP 缺 default-src 'self'：{csp}"
            assert "frame-ancestors 'none'" in csp, f"{path} CSP 缺 frame-ancestors：{csp}"

    def test_business_api_unbroken_by_middleware(self):
        """回归：安全头中间件不得破坏业务 API 响应体与状态码。"""
        from lantai.api.app import app

        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200, f"/health -> {resp.status_code}"
        assert resp.json(), "/health 响应体为空"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"


class TestD3LocalAssetPresent:
    """修复目标 1 的防假绿回归锁（TDD 要求 4）：删了 CDN 忘落本地文件 → 星图/悬镜全瘫。"""

    def test_local_d3_asset_exists_and_serves(self):
        asset = _UI_ASSET_DIR / "d3.v7.min.js"
        assert asset.is_file(), f"本地 d3 资产缺失：{asset}"
        head = asset.read_text(encoding="utf-8", errors="ignore")[:80]
        assert "d3js.org" in head, f"文件不像 d3 发行版：{head!r}"

        from lantai.api.app import app

        client = TestClient(app)
        resp = client.get("/ui/assets/d3.v7.min.js")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("text/javascript")
        assert len(resp.content) > 100_000, "d3 资产疑似截断/占位文件"
