# -*- coding: utf-8 -*-
"""v0.14 双主题换肤：routes_ui.py 全局注入吉金/漏窗主题（幂等，带计数断言）。

运行：python apply_theme.py
"""
import io
import re
import sys

PATH = r"C:\Users\Asus\Desktop\记忆\lantai\api\routes_ui.py"
MARK = "v0.14 双主题"

OVERRIDE_CSS = """  /* ===== v0.14 双主题：吉金（默认，青铜铭文）/ 漏窗（园林借景） ===== */
  :root { --bg:#1c2430; --card:#232c38; --card2:#2a3442; --ink:#e6dcc3; --muted:#8f9a9a;
          --line:#3a4655; --accent:#b08a3e; --ok:#3e7a6b; --warn:#b08a3e; --bad:#a33b2e;
          --soft:#2a3442; --btn-ink:#14181a;
          --lane-fact:#e6dcc3; --lane-rule:#7a9aa8; --lane-exp:#3e7a6b; --lane-pref:#a33b2e;
          --lane-chat:#b08a3e; --lane-gen:#8a7a9a;
          --e-supports:#3e7a6b; --e-refines:#7a9aa8; --e-contradicts:#a33b2e; --e-supersedes:#b08a3e;
          --title-font:"STKaiti","KaiTi",serif; --body-font:"Songti SC","STSong","SimSun",serif;
          --strip-h:22px; --sig-a:#b08a3e; --sig-b:#4e8d7c; }
  [data-theme="louchuang"] { --bg:#e9dfc6; --card:#f4ecda; --card2:#efe6cf; --ink:#2f4f4f; --muted:#7d8a72;
          --line:#c9bc96; --accent:#4e8d7c; --ok:#6f9e8a; --warn:#b08a3e; --bad:#a05a4a;
          --soft:#efe6cf; --btn-ink:#fff;
          --lane-fact:#2f4f4f; --lane-rule:#4e8d7c; --lane-exp:#6f9e8a; --lane-pref:#a05a4a;
          --lane-chat:#8a6a3a; --lane-gen:#6a5a7a;
          --e-supports:#6f9e8a; --e-refines:#4e8d7c; --e-contradicts:#a05a4a; --e-supersedes:#8a6a2a;
          --title-font:"STXingkai","STKaiti","KaiTi",serif; --body-font:"Songti SC","STSong","SimSun",serif;
          --strip-h:26px; --sig-a:#4e8d7c; --sig-b:#8a6a3a; }
  body { font-family:var(--body-font); }
  main h1, header h1 { font-family:var(--title-font); letter-spacing:4px; }
  header { border-bottom:2px solid var(--accent); }
  .bar, .tag, .track, .bar-row .track { background:var(--soft); }
  .bar i, .track i, .bar-row .track i { background:linear-gradient(90deg,var(--ok),var(--accent)); }
  .result pre, .row .body { color:var(--ink); }
  input[type=text], input[type=number], input[type=password], select { background:var(--card); color:var(--ink); border-color:var(--line); }
  button { color:var(--btn-ink); }
  button.ghost { background:var(--soft); color:var(--ink); }
  .card { background:var(--card); }
  a.card { background:var(--card); border-color:var(--line); color:var(--ink); }
  a.card b { color:var(--accent); } a.card span { color:var(--muted); } a.card:hover { border-color:var(--accent); }
  p { color:var(--muted); }
  svg { background:var(--card); }
  .node circle { stroke:var(--card); } .node text { fill:var(--ink); }
  /* 签名元素：吉金=云雷纹饰带；漏窗=回纹画框 */
  .sig { height:var(--strip-h); margin:10px 24px 0; opacity:.5; }
  .sig svg { width:100%; height:100%; display:block; background:transparent; border:0; }
  .sig-jijin { display:block; } .sig-louchuang { display:none; }
  [data-theme="louchuang"] .sig-jijin { display:none; }
  [data-theme="louchuang"] .sig-louchuang { display:block; }
  /* 主题切换钮 */
  .theme-ctl { float:right; margin-left:18px; }
  .theme-ctl button { padding:4px 12px; font-size:12px; background:transparent; color:var(--accent);
                      border:1px solid var(--accent); border-radius:999px; letter-spacing:2px; cursor:pointer; }
  .theme-ctl button:hover { background:var(--accent); color:var(--bg); }
  /* 漏窗：月洞门/画框卡片 */
  [data-theme="louchuang"] .panel, [data-theme="louchuang"] .card, [data-theme="louchuang"] a.card {
      position:relative; border-radius:16px 16px 6px 6px; }
  [data-theme="louchuang"] .card::before, [data-theme="louchuang"] a.card::before {
      content:""; position:absolute; left:50%; top:-9px; transform:translateX(-50%);
      width:52px; height:18px; border:2px solid var(--line); border-bottom:none;
      border-radius:26px 26px 0 0; background:var(--card); }
"""

SIG_STRIPS = """  <div class="sig sig-jijin" aria-hidden="true"><svg viewBox="0 0 1016 22" preserveAspectRatio="none"><defs><pattern id="lw" width="28" height="22" patternUnits="userSpaceOnUse"><path d="M7 2 h14 v18 h-14 z" fill="none" stroke="#b08a3e" stroke-width="1.4"/><path d="M14 6 h4 v10 h-4 z" fill="#b08a3e"/></pattern></defs><rect width="1016" height="22" fill="url(#lw)"/></svg></div>
  <div class="sig sig-louchuang" aria-hidden="true"><svg viewBox="0 0 1016 26" preserveAspectRatio="none"><g fill="none" stroke="#4e8d7c" stroke-width="1.4"><path d="M0 0 h30 v26 h-30 z M30 0 h1016 v26 h-1016 z"/><path d="M6 6 h18 v14 h-18 z"/><path d="M1010 6 h-18 v14 h18 z"/></g><g fill="#4e8d7c"><circle cx="15" cy="13" r="2.5"/><circle cx="1001" cy="13" r="2.5"/></g></svg></div>"""

THEME_CTL = """  <span class="theme-ctl"><button type="button" onclick="toggleTheme()" id="themeBtn" title="吉金 / 漏窗 主题切换">式 · 吉金</button></span>"""

THEME_JS = """  <script>
  (function () {
    var KEY = 'lantai-theme';
    function cur() {
      return document.documentElement.getAttribute('data-theme') === 'louchuang' ? 'louchuang' : 'jijin';
    }
    function paint() {
      var b = document.getElementById('themeBtn');
      if (b) b.textContent = '式 · ' + (cur() === 'louchuang' ? '漏窗' : '吉金');
    }
    window.toggleTheme = function () {
      var next = cur() === 'louchuang' ? 'jijin' : 'louchuang';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem(KEY, next); } catch (e) {}
      paint();
      if (document.getElementById('svg')) location.reload();  // 星图配色随主题重绘
    };
    try {
      var s = localStorage.getItem(KEY);
      var m = location.search.match(/[?&]theme=([a-z]+)/);
      if (m) s = m[1];
      if (s) document.documentElement.setAttribute('data-theme', s);
    } catch (e) {}
    paint();
  })();
  </script>"""

CSSVAR_DEF = """function cssVar(n, fb) {
  try {
    var v = getComputedStyle(document.documentElement).getPropertyValue(n);
    return (v && v.trim()) ? v.trim() : fb;
  } catch (e) { return fb; }
}
"""


def replace_all(text, old, new, expect):
    n = text.count(old)
    if n != expect:
        raise SystemExit("替换计数不符: %r expect=%d got=%d" % (old[:60], expect, n))
    return text.replace(old, new)


def replace_re(text, pattern, repl, expect):
    n = len(re.findall(pattern, text))
    if n != expect:
        raise SystemExit("正则计数不符: %s expect=%d got=%d" % (pattern, expect, n))
    return re.sub(pattern, repl, text, count=expect)


with io.open(PATH, "r", encoding="utf-8") as f:
    src = f.read()

if MARK in src:
    print("ALREADY_APPLIED")
    sys.exit(0)

# 1) 每面板 style 末尾追加双主题覆盖层
src = replace_all(src, "</style>", OVERRIDE_CSS + "</style>", 6)

# 2) header 内加主题切换钮，header 后加签名饰带（5 个面板；index 无 header 单独处理）
src = replace_all(src, "</header>", THEME_CTL + "\n</header>\n" + SIG_STRIPS, 5)
src = replace_all(src, "<body>\n<main>", "<body>\n" + THEME_CTL + "\n<main>", 1)

# 3) 每面板 </body> 前注入主题脚本
src = replace_all(src, "</body>", THEME_JS + "\n</body>", 6)

# 4) 星图 JS 配色改为读取 CSS 变量（随主题重绘）
src = replace_re(src, r"var LANE_COLOR = \{[^}]*\};",
                 CSSVAR_DEF + "var LANE_COLOR = {fact:cssVar('--lane-fact','#e6dcc3'), rule:cssVar('--lane-rule','#7a9aa8'),\n"
                 "                  experience:cssVar('--lane-exp','#3e7a6b'), preference:cssVar('--lane-pref','#a33b2e'),\n"
                 "                  chat:cssVar('--lane-chat','#b08a3e'), general:cssVar('--lane-gen','#8a7a9a')};", 1)
src = replace_re(src, r"var EDGE_COLOR = \{[^}]*\};",
                 "var EDGE_COLOR = {supports:cssVar('--e-supports','#3e7a6b'), refines:cssVar('--e-refines','#7a9aa8'),\n"
                 "                  contradicts:cssVar('--e-contradicts','#a33b2e'), supersedes:cssVar('--e-supersedes','#b08a3e')};", 1)
src = replace_re(src, r"var SCENE_PALETTE = \[[^\]]*\];",
                 "var SCENE_PALETTE = [cssVar('--lane-exp','#3e7a6b'), cssVar('--lane-rule','#7a9aa8'),\n"
                 "                     cssVar('--lane-pref','#a33b2e'), cssVar('--lane-fact','#e6dcc3'),\n"
                 "                     cssVar('--lane-gen','#8a7a9a'), cssVar('--e-supersedes','#b08a3e'),\n"
                 "                     '#be7e4a', '#310f1b'];", 1)
src = replace_all(src, "ring.setAttribute('stroke', '#1ba784');",
                  "ring.setAttribute('stroke', cssVar('--accent','#1ba784'));", 1)
src = replace_all(src, "rect.setAttribute('fill', '#f8f4ed');",
                  "rect.setAttribute('fill', cssVar('--bg','#f8f4ed'));", 1)
src = replace_all(src, "rect.setAttribute('stroke', '#867e76');",
                  "rect.setAttribute('stroke', cssVar('--muted','#867e76'));", 1)
src = replace_all(src, "t.setAttribute('stroke', '#fffef8');",
                  "t.setAttribute('stroke', cssVar('--card','#fffef8'));", 1)
src = replace_all(src, "sceneDot.style.background = '#1ba784';",
                  "sceneDot.style.background = cssVar('--accent','#1ba784');", 1)
src = replace_all(src, "srcDot.style.background = '#6e8b74';",
                  "srcDot.style.background = cssVar('--lane-chat','#6e8b74');", 1)

with io.open(PATH, "w", encoding="utf-8", newline="") as f:
    f.write(src)

print("OK")
