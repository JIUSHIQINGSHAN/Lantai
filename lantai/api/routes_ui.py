"""兰台追忆漏斗控制台（借鉴 aiduMEI v18.2 控制台 RECALL 面板思想）。

零依赖静态页：后端直接托管 HTML（无 node/打包），页内调 POST /search?trace=true
渲染 意图→向量→衰减→(重排)→最终 的召回漏斗与结果。只读，不改变任何检索语义。
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_UI_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>兰台 · 追忆漏斗</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1c2430; --muted:#6b7686;
          --accent:#2563eb; --line:#e3e7ee; --ok:#16a34a; --warn:#d97706; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  header { padding:18px 24px; background:var(--card); border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header p { margin:4px 0 0; color:var(--muted); font-size:12px; }
  main { max-width:860px; margin:20px auto; padding:0 16px 48px; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:16px; margin-bottom:16px; }
  .controls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  input[type=text] { flex:1 1 260px; padding:8px 10px; border:1px solid var(--line);
                     border-radius:8px; font-size:14px; }
  input[type=number], input[type=password] { width:90px; padding:8px 10px;
                     border:1px solid var(--line); border-radius:8px; }
  label.chk { display:inline-flex; align-items:center; gap:4px; color:var(--muted); }
  button { padding:8px 18px; border:0; border-radius:8px; background:var(--accent);
           color:#fff; font-size:14px; cursor:pointer; }
  button:hover { opacity:.9; }
  .gate { display:flex; gap:10px; align-items:center; }
  .badge { padding:2px 10px; border-radius:999px; font-size:12px; }
  .ok { background:#e8f7ee; color:var(--ok); }
  .warn { background:#fdf0dd; color:var(--warn); }
  .step { display:flex; align-items:center; gap:10px; margin:8px 0; }
  .step .name { width:96px; color:var(--muted); font-size:13px; }
  .bar { flex:1; height:22px; background:#eef1f6; border-radius:6px; overflow:hidden; }
  .bar i { display:block; height:100%; background:linear-gradient(90deg,#60a5fa,#2563eb); }
  .step .meta { width:240px; font-size:12px; color:var(--muted); text-align:right; }
  .result { border-top:1px solid var(--line); padding:10px 0; }
  .result .head { display:flex; gap:8px; align-items:center; }
  .score { font-weight:700; color:var(--accent); }
  .tag { font-size:11px; color:var(--muted); background:#eef1f6; padding:1px 8px; border-radius:999px; }
  .result pre { white-space:pre-wrap; margin:6px 0 0; font-size:13px; color:#3d4757; }
  #err { color:#b91c1c; }
  .muted { color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>兰台 · 追忆漏斗</h1>
  <p>它凭什么想起这条？候选池 → 意图 → 向量 → 衰减 → (重排) → 最终，每步耗时与命中数全可见。</p>
</header>
<main>
  <div class="panel">
    <div class="controls">
      <input id="q" type="text" placeholder="输入查询，例如：上线部署怎么做">
      <input id="topk" type="number" value="5" min="1" max="50" title="top_k">
      <input id="apikey" type="password" placeholder="API Key（可选）" title="非回环部署时填写 X-API-Key">
      <label class="chk"><input id="rerank" type="checkbox" checked> 重排</label>
      <button onclick="runRecall()">追忆</button>
    </div>
  </div>
  <div class="panel" id="gate"></div>
  <div class="panel" id="funnel"></div>
  <div class="panel" id="results"><div class="muted">输入查询后点击「追忆」。</div></div>
  <div id="err"></div>
</main>
<script>
const STEP_LABEL = {intent:"意图分类", vector_search:"向量检索", decay_filter:"时间衰减",
                    rerank:"重排", final:"最终", fallback_fts:"FTS 兜底"};
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
async function runRecall() {
  const q = document.getElementById('q').value.trim();
  const err = document.getElementById('err');
  err.textContent = '';
  if (!q) { err.textContent = '请输入查询。'; return; }
  const topK = parseInt(document.getElementById('topk').value, 10) || 5;
  const useRerank = document.getElementById('rerank').checked;
  const key = document.getElementById('apikey').value.trim();
  if (key) localStorage.setItem('lantai_api_key', key);
  const headers = {'Content-Type': 'application/json'};
  if (key) headers['X-API-Key'] = key;
  try {
    const res = await fetch('/search?trace=true', {method: 'POST', headers,
      body: JSON.stringify({query: q, top_k: topK, use_rerank: useRerank})});
    const data = await res.json();
    if (!res.ok) { err.textContent = 'HTTP ' + res.status + ': ' + (data.detail || ''); return; }
    render(data);
  } catch (e) { err.textContent = '请求失败: ' + e; }
}
function render(data) {
  const gate = data.gate || {};
  const g = document.getElementById('gate');
  g.innerHTML = '';
  const row = el('div', 'gate');
  const ok = gate.needs_memory !== false;
  row.appendChild(el('span', 'badge ' + (ok ? 'ok' : 'warn'), ok ? '闸门放行' : '闸门拦截'));
  if (gate.reason) row.appendChild(el('span', 'muted', String(gate.reason)));
  if (data.event_id) row.appendChild(el('span', 'muted', 'event ' + data.event_id));
  g.appendChild(row);

  const funnel = document.getElementById('funnel');
  funnel.innerHTML = '';
  const steps = data.trace || [];
  const maxMs = Math.max(1, ...steps.map(s => s.elapsed_ms || 0));
  steps.forEach(s => {
    const row2 = el('div', 'step');
    row2.appendChild(el('span', 'name', STEP_LABEL[s.step] || s.step));
    const bar = el('div', 'bar');
    const fill = el('i');
    fill.style.width = Math.max(2, ((s.elapsed_ms || 0) / maxMs) * 100) + '%';
    bar.appendChild(fill);
    row2.appendChild(bar);
    const meta = [s.elapsed_ms != null ? s.elapsed_ms + 'ms' : '',
                  s.candidate_count != null ? '候选 ' + s.candidate_count : '',
                  s.score_range ? '分 ' + s.score_range[0] + '~' + s.score_range[1] : '']
                 .filter(Boolean).join(' · ');
    row2.appendChild(el('span', 'meta', meta));
    funnel.appendChild(row2);
  });
  if (!steps.length) funnel.appendChild(el('div', 'muted', '（无 trace 数据）'));

  const results = document.getElementById('results');
  results.innerHTML = '';
  const list = data.results || [];
  if (!list.length) { results.appendChild(el('div', 'muted', '零命中。')); return; }
  list.forEach((r) => {
    const mem = r.memory || {};
    const item = el('div', 'result');
    const head = el('div', 'head');
    head.appendChild(el('span', 'score', (r.score * 100).toFixed(1) + '%'));
    if (mem.memory_type) head.appendChild(el('span', 'tag', mem.memory_type));
    if (mem.lane) head.appendChild(el('span', 'tag', mem.lane));
    if (mem.key) head.appendChild(el('span', 'tag', String(mem.key).slice(0, 24)));
    item.appendChild(head);
    item.appendChild(el('pre', null, String(mem.content || '')));
    results.appendChild(item);
  });
}
document.getElementById('apikey').value = localStorage.getItem('lantai_api_key') || '';
document.getElementById('q').addEventListener('keydown', (e) => { if (e.key === 'Enter') runRecall(); });
</script>
</body>
</html>
"""


@router.get("/ui/recall", response_class=HTMLResponse)
def recall_console() -> str:
    return _UI_HTML


@router.get("/ui/pulse", response_class=HTMLResponse)
def pulse_console() -> str:
    return _PULSE_HTML


@router.get("/ui/evolve", response_class=HTMLResponse)
def evolve_console() -> str:
    return _EVOLVE_HTML


@router.get("/ui", response_class=HTMLResponse)
def ui_index() -> str:
    return _INDEX_HTML

_EVOLVE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>兰台 · 检索质量看板</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1c2430; --muted:#6b7686;
          --accent:#2563eb; --line:#e3e7ee; --ok:#16a34a; --warn:#d97706; --bad:#b91c1c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  header { padding:18px 24px; background:var(--card); border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header p { margin:4px 0 0; color:var(--muted); font-size:12px; }
  header a { float:right; color:var(--accent); text-decoration:none; font-size:13px; }
  main { max-width:900px; margin:20px auto; padding:0 16px 48px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }
  .card .num { font-size:26px; font-weight:700; }
  .card .lab { color:var(--muted); font-size:12px; }
  .card .num.bad { color:var(--bad); } .card .num.ok { color:var(--ok); }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; }
  .panel h2 { margin:0 0 10px; font-size:15px; }
  .bar-row { display:flex; align-items:center; gap:10px; margin:6px 0; }
  .bar-row .name { width:130px; color:var(--muted); font-size:13px; overflow:hidden; text-overflow:ellipsis; }
  .bar-row .track { flex:1; height:16px; background:#eef1f6; border-radius:6px; overflow:hidden; }
  .bar-row .track i { display:block; height:100%; background:linear-gradient(90deg,#60a5fa,#2563eb); }
  .bar-row .val { width:110px; font-size:12px; color:var(--muted); text-align:right; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:600; }
  .zero { color:var(--bad); } .noise { color:var(--muted); }
  #err { color:#b91c1c; margin:10px 0; }
</style>
</head>
<body>
<header>
  <h1>兰台 · 检索质量看板</h1>
  <p>零召回率、按 lane/意图分布、场景命中、token 成本——最近 N 天检索事件聚合。</p>
  <a href="/ui/recall">← 追忆漏斗</a>
</header>
<main>
  <div class="grid" id="cards"></div>
  <div class="panel"><h2>按 lane 分布</h2><div id="lanes"></div></div>
  <div class="panel"><h2>按意图分布</h2><div id="intents"></div></div>
  <div class="panel"><h2>最近事件流</h2><table id="events">
    <thead><tr><th>时间</th><th>查询</th><th>lane</th><th>意图</th><th>延迟</th><th>结果</th></tr></thead>
    <tbody></tbody></table></div>
  <div id="err"></div>
</main>
<script>
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('zh-CN', {hour12:false});
}
function bars(container, stats) {
  container.innerHTML = '';
  const rows = Object.entries(stats || {});
  if (!rows.length) { container.appendChild(el('div','muted','（无数据）')); return; }
  const max = Math.max(1, ...rows.map(([,v]) => v.total));
  rows.forEach(([name, v]) => {
    const row = el('div','bar-row');
    row.appendChild(el('span','name', name));
    const track = el('div','track');
    const fill = el('i');
    fill.style.width = Math.max(2, (v.total / max) * 100) + '%';
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el('span','val', v.total + ' 查询 · 零召回 ' + v.zero));
    container.appendChild(row);
  });
}
async function load() {
  const err = document.getElementById('err');
  err.textContent = '';
  const key = localStorage.getItem('lantai_api_key') || '';
  const headers = {};
  if (key) headers['X-API-Key'] = key;
  try {
    const [repRes, evRes] = await Promise.all([
      fetch('/retrieval/recall-report?days=7', {headers}),
      fetch('/retrieval/recent-events?limit=20', {headers})
    ]);
    const rep = await repRes.json();
    const ev = await evRes.json();
    if (!repRes.ok || !evRes.ok) { err.textContent = 'HTTP ' + repRes.status + ' / ' + evRes.status; return; }
    render(rep, ev.events || []);
  } catch (e) { err.textContent = '请求失败: ' + e; }
}
function render(rep, events) {
  const cards = document.getElementById('cards');
  cards.innerHTML = '';
  const mk = (num, lab, cls) => {
    const c = el('div','card');
    c.appendChild(el('div','num ' + (cls || ''), String(num)));
    c.appendChild(el('div','lab', lab));
    cards.appendChild(c);
  };
  mk(rep.real, '真实查询（' + rep.window_days + ' 天）');
  mk(rep.zero, '零召回', rep.zero_recall_rate > 0.1 ? 'bad' : 'ok');
  mk((rep.zero_recall_rate * 100).toFixed(1) + '%', '零召回率', rep.zero_recall_rate > 0.1 ? 'bad' : 'ok');
  mk(rep.estimated_tokens.total, 'token 粗估');
  mk(rep.system_noise, '系统噪音');
  if (rep.scene && rep.scene.enabled) {
    mk((rep.scene.hit_rate == null ? '-' : (rep.scene.hit_rate * 100).toFixed(1) + '%'), '场景命中率');
  }
  bars(document.getElementById('lanes'), rep.by_lane);
  bars(document.getElementById('intents'), rep.by_intent);
  const tbody = document.querySelector('#events tbody');
  tbody.innerHTML = '';
  if (!events.length) { tbody.appendChild(el('tr', null)); }
  events.forEach(e => {
    const tr = el('tr', e.is_system_noise ? 'noise' : '');
    [fmtTime(e.created_at), e.query || '-', e.lane, e.intent,
     e.latency_ms + 'ms', e.zero_result ? '零命中' : (e.estimated_tokens + ' tok')]
      .forEach(td => { const c = el('td', e.zero_result ? 'zero' : '', td); tr.appendChild(c); });
    tbody.appendChild(tr);
  });
}
load();
</script>
</body>
</html>
"""

_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>兰台 · 控制台</title>
<style>
  body { font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:#f6f7f9; color:#1c2430; margin:0; }
  main { max-width:640px; margin:60px auto; padding:0 20px; }
  h1 { font-size:22px; }
  p { color:#6b7686; }
  a.card { display:block; background:#fff; border:1px solid #e3e7ee; border-radius:12px;
           padding:18px 20px; margin:14px 0; text-decoration:none; color:#1c2430; }
  a.card:hover { border-color:#2563eb; }
  a.card b { font-size:16px; color:#2563eb; }
  a.card span { display:block; color:#6b7686; font-size:13px; }
</style></head>
<body>
<main>
  <h1>兰台 · 控制台</h1>
  <p>借鉴 aiduMEI v18.2 控制台：记忆如何被想起、检索质量如何——看得见、可追溯。</p>
  <a class="card" href="/ui/recall"><b>追忆漏斗</b><span>候选池 → 意图 → 向量 → 衰减 → (重排) → 最终，每步耗时与命中数。</span></a>
  <a class="card" href="/ui/evolve"><b>检索质量看板</b><span>最近 7 天零召回率、按 lane/意图分布、场景命中、token 成本、事件流。</span></a>
  <a class="card" href="/ui/pulse"><b>脉搏</b><span>服务状态与存储分层：记忆存量、分布、写入水位、近 7 天新增、worker 运行时间。</span></a>
  <a class="card" href="/ui/vault"><b>档案与锦囊</b><span>记忆档案浏览与过滤、锦囊待审裁决、衰减概览——存了什么、待裁什么。</span></a>
</main>
</body>
</html>
"""

_PULSE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>兰台 · 脉搏</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1c2430; --muted:#6b7686;
          --accent:#2563eb; --line:#e3e7ee; --ok:#16a34a; --warn:#d97706; --bad:#b91c1c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  header { padding:18px 24px; background:var(--card); border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header p { margin:4px 0 0; color:var(--muted); font-size:12px; }
  header a { float:right; color:var(--accent); text-decoration:none; font-size:13px; }
  main { max-width:900px; margin:20px auto; padding:0 16px 48px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px; }
  .card .num { font-size:24px; font-weight:700; }
  .card .lab { color:var(--muted); font-size:12px; }
  .card .num.ok { color:var(--ok); } .card .num.bad { color:var(--bad); }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; }
  .panel h2 { margin:0 0 10px; font-size:15px; }
  .bar-row { display:flex; align-items:center; gap:10px; margin:6px 0; }
  .bar-row .name { width:120px; color:var(--muted); font-size:13px; }
  .bar-row .track { flex:1; height:16px; background:#eef1f6; border-radius:6px; overflow:hidden; }
  .bar-row .track i { display:block; height:100%; background:linear-gradient(90deg,#60a5fa,#2563eb); }
  .bar-row .val { width:60px; font-size:12px; color:var(--muted); text-align:right; }
  .check { display:inline-block; margin-right:14px; }
  .check b { margin-left:4px; }
  .ok { color:var(--ok); } .warn { color:var(--warn); } .bad { color:var(--bad); }
  #err { color:#b91c1c; margin:10px 0; }
</style>
</head>
<body>
<header>
  <h1>兰台 · 脉搏</h1>
  <p>服务状态与存储分层：记忆存量、分布、写入水位、近 7 天新增、worker 运行时间。</p>
  <a href="/ui">← 控制台</a>
</header>
<main>
  <div class="panel"><h2>服务状态</h2><div id="checks"></div></div>
  <div class="grid" id="cards"></div>
  <div class="panel"><h2>近 7 天新增记忆</h2><div id="daily"></div></div>
  <div class="panel"><h2>分层分布</h2><div style="display:grid;grid-template-columns:1fr 1fr;gap:0 24px;">
    <div><h3 style="font-size:13px;color:var(--muted)">按 tier</h3><div id="tiers"></div></div>
    <div><h3 style="font-size:13px;color:var(--muted)">按 status</h3><div id="statuses"></div></div>
  </div></div>
  <div class="panel"><h2>按 lane 分布</h2><div id="lanes"></div></div>
  <div class="panel"><h2>worker 上次运行</h2><div id="workers"></div></div>
  <div id="err"></div>
</main>
<script>
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function bars(container, stats) {
  container.innerHTML = '';
  const rows = Object.entries(stats || {});
  if (!rows.length) { container.appendChild(el('div','', '(无数据)')); return; }
  const max = Math.max(1, ...rows.map(([,v]) => Number(v) || 0));
  rows.forEach(([name, v]) => {
    const row = el('div','bar-row');
    row.appendChild(el('span','name', name));
    const track = el('div','track');
    const fill = el('i');
    fill.style.width = Math.max(2, ((Number(v)||0) / max) * 100) + '%';
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el('span','val', String(v)));
    container.appendChild(row);
  });
}
function dailyBars(container, daily) {
  container.innerHTML = '';
  const days = Object.entries(daily || {});
  if (!days.length) { container.appendChild(el('div','', '(无数据)')); return; }
  const max = Math.max(1, ...days.map(([,v]) => v));
  const wrap = el('div','bar-row');
  days.forEach(([d, v]) => {
    const col = el('div', 'bar-row');
    col.style.flexDirection = 'column'; col.style.alignItems = 'center'; col.style.gap = '2px';
    const track = el('div','track');
    track.style.width = '26px'; track.style.height = '60px';
    track.style.display = 'flex'; track.style.alignItems = 'flex-end';
    const fill = el('i');
    fill.style.width = '100%';
    fill.style.height = Math.max(2, (v / max) * 100) + '%';
    track.appendChild(fill);
    col.appendChild(track);
    col.appendChild(el('span','name', d.slice(5)));
    col.appendChild(el('span','val', String(v)));
    wrap.appendChild(col);
  });
  container.appendChild(wrap);
}
async function load() {
  const err = document.getElementById('err');
  err.textContent = '';
  const key = localStorage.getItem('lantai_api_key') || '';
  const headers = {};
  if (key) headers['X-API-Key'] = key;
  try {
    const [stRes, usRes, dpRes] = await Promise.all([
      fetch('/stats', {headers}),
      fetch('/usage', {headers}),
      fetch('/health/deep', {headers})
    ]);
    if (!stRes.ok || !usRes.ok || !dpRes.ok) {
      err.textContent = 'HTTP ' + stRes.status + ' / ' + usRes.status + ' / ' + dpRes.status;
      return;
    }
    const st = await stRes.json();
    const us = await usRes.json();
    const dp = await dpRes.json();
    render(st, us, dp);
  } catch (e) { err.textContent = '请求失败: ' + e; }
}
function render(st, us, dp) {
  const checks = document.getElementById('checks');
  checks.innerHTML = '';
  Object.entries((dp.checks) || {}).forEach(([name, v]) => {
    const ok = v === 'ok', skip = String(v).startsWith('skip');
    const span = el('span', 'check');
    span.appendChild(el('b', null, name));
    span.appendChild(el('span', ok ? 'ok' : (skip ? 'warn' : 'bad'), v));
    checks.appendChild(span);
  });
  const cards = document.getElementById('cards');
  cards.innerHTML = '';
  const mk = (num, lab, cls) => {
    const c = el('div','card');
    c.appendChild(el('div','num ' + (cls||''), String(num)));
    c.appendChild(el('div','lab', lab));
    cards.appendChild(c);
  };
  mk(st.total_memories, '记忆总数');
  const wl = st.coalesce_buffer || {};
  mk(wl.total_messages || 0, '写入缓冲消息');
  mk(wl.active_keys || 0, '缓冲活跃键');
  mk(wl.flush_count || 0, '累计冲刷');
  bars(document.getElementById('tiers'), st.by_tier);
  bars(document.getElementById('statuses'), st.by_status);
  bars(document.getElementById('lanes'), st.by_lane);
  dailyBars(document.getElementById('daily'), us.daily_new);
  const workers = document.getElementById('workers');
  workers.innerHTML = '';
  const rows = Object.entries(st.workers || {});
  if (!rows.length) { workers.appendChild(el('div','', '(暂无 worker 运行记录)')); return; }
  rows.forEach(([name, ts]) => {
    const row = el('div','bar-row');
    row.appendChild(el('span','name', name));
    row.appendChild(el('span','val', String(ts).slice(0, 19).replace('T',' ')));
    workers.appendChild(row);
  });
}
load();
</script>
</body>
</html>
"""


@router.get("/ui/vault", response_class=HTMLResponse)
def ui_vault():
    """档案与锦囊控制台（VAULT，借鉴 aiduMEI v18.2 控制台）：只读页面。"""
    return _VAULT_HTML


_VAULT_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>兰台 · 档案与锦囊</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1c2430; --muted:#6b7686;
          --accent:#2563eb; --line:#e3e7ee; --ok:#16a34a; --warn:#d97706; --bad:#b91c1c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  header { padding:18px 24px; background:var(--card); border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:18px; }
  header p { margin:4px 0 0; color:var(--muted); font-size:12px; }
  main { max-width:960px; margin:20px auto; padding:0 16px 48px; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:16px; margin-bottom:16px; }
  h2 { font-size:15px; margin:0 0 12px; }
  h3 { font-size:13px; color:var(--muted); margin:8px 0; }
  .cards { display:flex; gap:12px; flex-wrap:wrap; }
  .card { flex:1 1 130px; background:#f8fafc; border:1px solid var(--line);
          border-radius:10px; padding:12px; text-align:center; }
  .card .num { font-size:22px; font-weight:700; color:var(--accent); }
  .card .lab { font-size:12px; color:var(--muted); }
  .controls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
  select, input[type=password] { padding:7px 10px; border:1px solid var(--line);
           border-radius:8px; font-size:13px; background:#fff; }
  button { padding:7px 14px; border:0; border-radius:8px; background:var(--accent);
           color:#fff; font-size:13px; cursor:pointer; }
  button:hover { opacity:.9; }
  button.ghost { background:#eef1f6; color:var(--ink); }
  button.ok { background:var(--ok); } button.bad { background:var(--bad); }
  .row { border-top:1px solid var(--line); padding:10px 0; }
  .row:first-of-type { border-top:0; }
  .row .head { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .row .body { white-space:pre-wrap; font-size:13px; color:#3d4757; margin:6px 0; }
  .row .actions { display:flex; gap:8px; }
  .tag { font-size:11px; color:var(--muted); background:#eef1f6; padding:1px 8px; border-radius:999px; }
  .muted { color:var(--muted); font-size:12px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);
           vertical-align:top; }
  th { color:var(--muted); font-weight:600; white-space:nowrap; }
  td.c { max-width:340px; white-space:pre-wrap; }
  .bar-row { display:flex; align-items:center; gap:10px; margin:6px 0; }
  .bar-row .name { width:110px; color:var(--muted); font-size:12px; }
  .track { flex:1; height:14px; background:#eef1f6; border-radius:6px; overflow:hidden; }
  .track i { display:block; height:100%; background:linear-gradient(90deg,#60a5fa,#2563eb); }
  .bar-row .val { width:44px; font-size:12px; color:var(--muted); text-align:right; }
  #err { color:var(--bad); margin-top:8px; }
</style>
</head>
<body>
<header>
  <h1>兰台 · 档案与锦囊</h1>
  <p>记忆档案浏览、锦囊待审裁决、衰减概览——存了什么、待裁什么、如何衰减，一眼可见。</p>
</header>
<main>
  <div class="panel"><div class="cards" id="cards"></div></div>
  <div class="panel">
    <h2>锦囊 · 待审候选</h2>
    <div id="jinnang"></div>
  </div>
  <div class="panel">
    <h2>记忆档案</h2>
    <div class="controls">
      <select id="f_lane"></select>
      <select id="f_status"></select>
      <select id="f_decay"></select>
      <button onclick="loadMemories(0)">查询</button>
      <span class="muted" id="pageinfo"></span>
    </div>
    <div id="memories"></div>
    <div class="controls" style="margin-top:10px">
      <button class="ghost" onclick="loadMemories(offset - PAGE)">← 上一页</button>
      <button class="ghost" onclick="loadMemories(offset + PAGE)">下一页 →</button>
    </div>
  </div>
  <div class="panel"><h2>衰减概览</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 24px;">
      <div><h3>按 decay_class</h3><div id="decay"></div></div>
      <div><h3>按 lane</h3><div id="lanes"></div></div>
    </div>
  </div>
  <div id="err"></div>
</main>
<script>
var PAGE = 20, offset = 0, total = 0;
var LANES = ['fact','rule','experience','preference','chat','general'];
var STATUSES = ['active','archived'];
var DECAYS = ['procedural','semantic','episodic'];

function el(tag, cls, text) {
  var e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function api(path, opts) {
  var o = opts || {};
  var headers = o.headers || {};
  var key = localStorage.getItem('lantai_api_key') || '';
  if (key) headers['X-API-Key'] = key;
  if (o.body) headers['Content-Type'] = 'application/json';
  o.headers = headers;
  return fetch(path, o);
}
function setErr(msg) { document.getElementById('err').textContent = msg || ''; }
function fillSelect(id, values, label) {
  var sel = document.getElementById(id);
  sel.appendChild(el('option', null, label));
  values.forEach(function (v) {
    var o = el('option', null, v); o.value = v; sel.appendChild(o);
  });
}
function bars(container, stats) {
  container.innerHTML = '';
  var rows = Object.entries(stats || {});
  if (!rows.length) { container.appendChild(el('div','muted','(无数据)')); return; }
  var max = Math.max(1, rows.map(function (kv) { return Number(kv[1]) || 0; })
                     .reduce(function (a, b) { return Math.max(a, b); }, 0));
  rows.forEach(function (kv) {
    var row = el('div','bar-row');
    row.appendChild(el('span','name', kv[0]));
    var track = el('div','track');
    var fill = el('i');
    fill.style.width = Math.max(2, ((Number(kv[1]) || 0) / max) * 100) + '%';
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el('span','val', String(kv[1])));
    container.appendChild(row);
  });
}
function renderCards(st, pending) {
  var cards = document.getElementById('cards');
  cards.innerHTML = '';
  var mk = function (num, lab) {
    var c = el('div','card');
    c.appendChild(el('div','num', String(num)));
    c.appendChild(el('div','lab', lab));
    cards.appendChild(c);
  };
  mk(st.total_memories, '记忆总数');
  mk((st.by_status && st.by_status.active) || 0, 'active');
  mk((st.by_status && st.by_status.archived) || 0, 'archived');
  mk(pending.candidates ? pending.candidates.length : 0, '待审锦囊');
}
function renderJinnang(pending) {
  var box = document.getElementById('jinnang');
  box.innerHTML = '';
  var list = pending.candidates || [];
  if (!list.length) { box.appendChild(el('div','muted','锦囊已清空——没有待裁决的候选。')); return; }
  list.forEach(function (c) {
    var row = el('div','row');
    var head = el('div','head');
    head.appendChild(el('span','tag', c.lane || 'general'));
    if (c.confidence !== undefined) head.appendChild(el('span','tag','置信 ' + c.confidence));
    if (c.review_due_at) head.appendChild(el('span','tag','due ' + String(c.review_due_at).slice(0, 16).replace('T',' ')));
    row.appendChild(head);
    var body = el('div','body');
    body.textContent = String(c.content || c.summary || '').slice(0, 160);
    row.appendChild(body);
    var act = el('div','actions');
    var ok = el('button','ok','采纳');
    ok.onclick = function () { decide(c.id, true); };
    var bad = el('button','bad','驳回');
    bad.onclick = function () { decide(c.id, false); };
    act.appendChild(ok); act.appendChild(bad);
    row.appendChild(act);
    box.appendChild(row);
  });
}
function decide(id, approve) {
  setErr('');
  api('/candidates/' + encodeURIComponent(id) + '/review', {
    method: 'POST', body: JSON.stringify({ approve: approve })
  }).then(function (r) {
    if (!r.ok) { setErr('裁决失败 HTTP ' + r.status); return; }
    loadJinnang();
  }).catch(function (e) { setErr('裁决请求失败: ' + e); });
}
function renderMemories(page) {
  var box = document.getElementById('memories');
  box.innerHTML = '';
  var rows = page.memories || [];
  document.getElementById('pageinfo').textContent =
    '第 ' + (page.offset + 1) + '–' + (page.offset + rows.length) + ' 条 / 共 ' + page.total + ' 条';
  if (!rows.length) { box.appendChild(el('div','muted','(无符合条件的记忆)')); return; }
  var table = el('table');
  var thead = el('thead');
  var hr = el('tr');
  ['lane','类型','衰减','分','用','更新','内容'].forEach(function (h) {
    hr.appendChild(el('th', null, h));
  });
  thead.appendChild(hr); table.appendChild(thead);
  var tb = el('tbody');
  rows.forEach(function (m) {
    var tr = el('tr');
    tr.appendChild(el('td', null, m.lane));
    tr.appendChild(el('td', null, m.memory_type));
    tr.appendChild(el('td', null, m.decay_class));
    tr.appendChild(el('td', null, String(m.decay_score !== undefined ? m.decay_score.toFixed(2) : '')));
    tr.appendChild(el('td', null, String(m.use_count)));
    tr.appendChild(el('td', null, String(m.updated_at || '').slice(0, 16).replace('T',' ')));
    var c = el('td','c');
    c.textContent = m.content || '';
    tr.appendChild(c);
    tb.appendChild(tr);
  });
  table.appendChild(tb);
  box.appendChild(table);
}
function loadMemories(nextOffset) {
  setErr('');
  if (nextOffset !== undefined) offset = Math.max(0, Math.min(nextOffset, Math.max(0, total - PAGE)));
  var q = 'limit=' + PAGE + '&offset=' + offset;
  var lane = document.getElementById('f_lane').value;
  var status = document.getElementById('f_status').value;
  var decay = document.getElementById('f_decay').value;
  if (lane) q += '&lane=' + encodeURIComponent(lane);
  if (status) q += '&status=' + encodeURIComponent(status);
  if (decay) q += '&decay_class=' + encodeURIComponent(decay);
  api('/memories?' + q).then(function (r) {
    if (!r.ok) { setErr('档案请求失败 HTTP ' + r.status); return; }
    return r.json();
  }).then(function (page) {
    if (!page) return;
    total = page.total;
    renderMemories(page);
  }).catch(function (e) { setErr('档案请求失败: ' + e); });
}
function loadJinnang() {
  api('/candidates/pending').then(function (r) {
    if (!r.ok) { setErr('锦囊请求失败 HTTP ' + r.status); return; }
    return r.json();
  }).then(function (p) { if (p) renderJinnang(p); })
    .catch(function (e) { setErr('锦囊请求失败: ' + e); });
}
function load() {
  setErr('');
  fillSelect('f_lane', LANES, 'lane（全部）');
  fillSelect('f_status', STATUSES, 'status（全部）');
  fillSelect('f_decay', DECAYS, 'decay_class（全部）');
  Promise.all([api('/stats'), api('/candidates/pending')])
    .then(function (rs) {
      return Promise.all([rs[0].json(), rs[1].json()]);
    })
    .then(function (data) {
      renderCards(data[0], data[1]);
      bars(document.getElementById('decay'), (data[0].by_decay_class) || {});
      bars(document.getElementById('lanes'), data[0].by_lane || {});
      renderJinnang(data[1]);
      loadMemories(0);
    })
    .catch(function (e) { setErr('加载失败: ' + e); });
}
load();
</script>
</body>
</html>
"""
