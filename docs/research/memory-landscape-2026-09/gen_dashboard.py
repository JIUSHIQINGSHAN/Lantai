# -*- coding: utf-8 -*-
"""R15: 生成 dashboard.html——单文件零依赖可视化框架（内嵌真实数据, vanilla JS + SVG）"""
import json, io

F = json.load(io.open('framework.json', encoding='utf-8'))
L = json.load(io.open('landscape.json', encoding='utf-8'))
P = json.load(io.open('papers.json', encoding='utf-8'))
iters = [json.loads(l) for l in io.open('iterations.jsonl', encoding='utf-8')]

DIMS = [{'id': d['id'], 'name': d['name'], 'weight': d['weight']} for d in F['dimensions']]
def gs(v):
    return float(v.get('score', 0)) if isinstance(v, dict) else float(v)
systems = []
for sid, sv in F['systems_scores'].items():
    systems.append({'id': sid, 'scores': {d['id']: gs(sv['scores'][d['id']]) for d in DIMS},
                    'total': F['weighted_totals'].get(sid, 0),
                    'note': sv.get('evidence_note', '')})
landscape_meta = [{'id': s['id'], 'name': s['name'], 'category': s.get('category', ''),
                   'url': s.get('url', ''), 'stars': (s.get('stars') or {}).get('value'),
                   'verification': s.get('verification', ''), 'license': s.get('license', ''),
                   'storage': s.get('storage', ''), 'mcp': s.get('mcp'),
                   'mechanisms': s.get('key_mechanisms', [])[:5]} for s in L['systems']]
papers_meta = [{'id': p['id'], 'title': p['title'], 'arxiv': p.get('arxiv'),
                'year': p.get('year'), 'mechanism': p.get('mechanism', ''),
                'url': p.get('url', ''), 'venue': p.get('venue', '')} for p in P['papers']]
iter_data = [{'iter': r['iter'], 'title': r['title'], 'type': r['type'],
              'total': r.get('lantai_total'), 'delta': r.get('lantai_score_delta_sum', 0),
              'systems': r.get('coverage', {}).get('systems', 0),
              'papers': r.get('coverage', {}).get('papers', 0),
              'churn': r.get('roadmap_churn', {})} for r in iters]

DATA = {'dims': DIMS, 'systems': systems, 'landscape': landscape_meta,
        'papers': papers_meta, 'iterations': iter_data,
        'totals': F['weighted_totals']}

html = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>兰台记忆方向调研 · 可视化框架（2026-09）</title>
<style>
:root{--ink:#2b2b2b;--paper:#f7f4ee;--card:#ffffff;--brand:#8b1a1a;--blue:#1f4e79;--line:#e4ddd0;}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;background:var(--paper);color:var(--ink);line-height:1.6}
header{background:linear-gradient(135deg,#3a2410,#6b3a12 55%,#8b1a1a);color:#f5ead6;padding:34px 6vw 26px}
header h1{font-size:26px;letter-spacing:1px}
header p{opacity:.85;font-size:13px;margin-top:6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:18px}
.kpi{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.18);border-radius:10px;padding:10px 14px}
.kpi b{display:block;font-size:24px;color:#ffd98a}
.kpi span{font-size:12px;opacity:.85}
main{padding:26px 6vw 60px;max-width:1280px;margin:0 auto}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:22px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
h2{font-size:18px;color:var(--brand);border-left:4px solid var(--brand);padding-left:10px;margin-bottom:14px}
h3{font-size:14px;margin:14px 0 8px;color:#5b4632}
.note{font-size:12px;color:#8a7f6f;margin-top:8px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{background:#f2ecdf;position:sticky;top:0}
td.num{text-align:center;font-weight:600}
.lantai-row{background:#fdf3f0}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;margin-right:4px;background:#eee6d8;color:#6b5a41}
.tag.verified{background:#e2efda;color:#2e6b2e}
.tag.inherited{background:#fdeeda;color:#9a6a1b}
.scroll{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:8px}
svg text{font-family:inherit}
.flex{display:flex;gap:22px;flex-wrap:wrap}
.flex>div{flex:1 1 420px}
footer{font-size:12px;color:#8a7f6f;text-align:center;padding:18px}
a{color:var(--blue)}
</style>
</head>
<body>
<header>
  <h1>兰台（Lantai）记忆系统 · 全景调研与方向迭代可视化框架</h1>
  <p>检索/迭代日期 2026-09-19 · 20 个记忆系统 + 32 篇论文/基准一手核验 · 15 轮迭代 · 评分冻结 3.56/5.00 · 路线图 v2（P0×4 / P1×3 / P2×4）</p>
  <div class="kpis">
    <div class="kpi"><b>20</b><span>记忆系统（全部核验）</span></div>
    <div class="kpi"><b>32</b><span>论文与基准</span></div>
    <div class="kpi"><b>3.56</b><span>兰台加权总分（冻结）</span></div>
    <div class="kpi"><b>59</b><span>兰台 MCP 工具（实核）</span></div>
    <div class="kpi"><b>15</b><span>轮迭代（iterations.jsonl）</span></div>
    <div class="kpi"><b>40+</b><span>一手来源</span></div>
  </div>
</header>
<main>

<section id="sec-matrix">
  <h2>① 能力评分矩阵（最终结果）</h2>
  <div class="scroll"><table id="matrix"></table></div>
  <p class="note">0–5 分，步长 0.5；按加权总分排序。绿色=强，红色=弱。评分只依据公开可证实机制（官方 README/docs/arXiv），厂商自报基准分不入证据。兰台行为内部代码核验（temporal 3.0 / integration 3.5 于 R7 修正）。</p>
</section>

<section id="sec-iter">
  <h2>② 迭代过程（15 轮）</h2>
  <div class="flex">
    <div><div id="iter-line"></div><p class="note">兰台加权总分演进：R7 内部核验一次性修正（temporal −0.5 / integration +1.0），R13 起冻结。</p></div>
    <div><div id="iter-cov"></div><p class="note">覆盖增长：系统 12→20，论文 17→32。R2–R9 调研吸收，R10–R13 主题综合，R14 路线图收口，R15 可视化冻结。</p></div>
  </div>
  <div class="scroll" style="max-height:300px;margin-top:10px"><table id="iter-table"></table></div>
</section>

<section id="sec-quadrant">
  <h2>③ 格局定位图（分析师判定，非实测）</h2>
  <div id="quadrant"></div>
</section>

<section id="sec-radar">
  <h2>④ 能力雷达：兰台 vs 三代表系统</h2>
  <div id="radar"></div>
  <p class="note">对照选择：Mem0（生态最大）、Zep/Graphiti（时间最强）、agentmemory（最同类：SQLite 零依赖 + MCP 工具面 54 vs 兰台 59）。</p>
</section>

<section id="sec-roadmap">
  <h2>⑤ 路线图 v2（R14 收口）</h2>
  <h3>定位声明：兰台 = 面向个人维护与多宿主 Agent 的、全生命周期可治理的本地长期记忆层——唯一把「人工闸门—回滚—注入围栏—状态机遗忘」做成闭环的系统；用操作级自证替代榜单叙事。</h3>
  <table id="roadmap"></table>
  <p class="note">churn 相对 2026-09-18 主线 A/B/C：改 2（MCP 扩容→宿主矩阵+注入回执；时间主线升 P1 首位）、增 3（RL 影子观察、治理四原语对照表、巩固产物过审）。全文见 roadmap-v2.md。</p>
</section>

<section id="sec-landscape">
  <h2>⑥ 记忆系统台账（20 个，含机制摘要）</h2>
  <div class="scroll"><table id="landscape"></table></div>
</section>

<section id="sec-papers">
  <h2>⑦ 论文与基准台账（32 篇）</h2>
  <div class="scroll"><table id="papers"></table></div>
</section>

<section id="sec-framework">
  <h2>⑧ 评测框架说明</h2>
  <table id="dims"></table>
  <p class="note">锚点：0=无机制；1=仅概念；2=基础机制；3=可用且有测试/证据；4=带治理与降级的完整闭环；5=行业领先且有公开可复现证据。来源：framework.json v6。</p>
</section>

</main>
<footer>生成于 2026-09-19 · 数据文件：framework.json / landscape.json / papers.json / iterations.jsonl · 本页零外部依赖，可直接离线打开</footer>
<script>
const DATA = __DATA_JSON__;

// 截图/打印模式：URL 带 #full 时展开全部内滚容器，保证长图完整
if (location.hash === '#full') {
  window.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.scroll').forEach(e => e.style.maxHeight = 'none');
  });
}

// ---------- ① matrix ----------
const dimIds = DATA.dims.map(d=>d.id);
const dimNames = DATA.dims.map(d=>d.name);
const sorted = [...DATA.systems].sort((a,b)=>b.total-a.total);
function cellColor(v){
  const t=Math.max(0,Math.min(1,v/5));
  const r=Math.round(215-130*t), g=Math.round(120+110*t), b=Math.round(90+60*t);
  return `rgb(${r},${g},${b})`;
}
let h='<thead><tr><th>系统</th><th>加权</th>'+dimNames.map(n=>`<th>${n}</th>`).join('')+'</tr></thead><tbody>';
for(const s of sorted){
  const cls = s.id==='lantai' ? ' class="lantai-row"' : '';
  h+=`<tr${cls}><td><b>${s.id==='lantai'?'兰台':s.id}</b></td><td class="num">${s.total.toFixed(2)}</td>`;
  for(const d of dimIds){
    const v=s.scores[d];
    h+=`<td class="num" style="background:${cellColor(v)}">${v}</td>`;
  }
  h+='</tr>';
}
document.getElementById('matrix').innerHTML=h+'</tbody>';

// ---------- ② iteration line + coverage ----------
function lineChart(el, series, opts){
  const W=560,H=260,P={l:46,r:14,t:18,b:30};
  const all=series.flatMap(s=>s.ys.filter(v=>v!=null));
  let lo=Math.min(...all), hi=Math.max(...all);
  if(opts.forceLo!=null){lo=opts.forceLo; hi=opts.forceHi;}
  const n=Math.max(...series.map(s=>s.xs.length));
  const X=i=>P.l+(W-P.l-P.r)*(i/(n-1));
  const Y=v=>P.t+(H-P.t-P.b)*(1-(v-lo)/(hi-lo||1));
  let s=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
  for(let k=0;k<=4;k++){const y=P.t+(H-P.t-P.b)*k/4;
    s+=`<line x1="${P.l}" y1="${y}" x2="${W-P.r}" y2="${y}" stroke="#eee4d4"/>`;
    s+=`<text x="${P.l-6}" y="${y+4}" font-size="10" text-anchor="end" fill="#8a7f6f">${(hi-(hi-lo)*k/4).toFixed(opts.dec??1)}</text>`;}
  for(const sr of series){
    const pts=sr.xs.map((x,i)=>[X(i), sr.ys[i]==null?null:Y(sr.ys[i])]);
    let path='',pen=false;
    for(const [px,py] of pts){ if(py==null){pen=false;continue;}
      path+=(pen?'L':'M')+px.toFixed(1)+' '+py.toFixed(1); pen=true;}
    s+=`<path d="${path}" fill="none" stroke="${sr.color}" stroke-width="2"/>`;
    pts.forEach(([px,py],i)=>{ if(py!=null) s+=`<circle cx="${px}" cy="${py}" r="2.6" fill="${sr.color}"/>`;});
  }
  s+=`<text x="${W/2}" y="${H-6}" font-size="11" text-anchor="middle" fill="#5b4632">${opts.xlabel||''}</text>`;
  s+='</svg>';
  document.getElementById(el).innerHTML=s;
}
const its=DATA.iterations;
lineChart('iter-line',[{xs:its.map(r=>r.iter),ys:its.map(r=>r.total),color:'#8b1a1a'}],
 {forceLo:3.2,forceHi:3.8,xlabel:'迭代轮次 →  加权总分',dec:2});
lineChart('iter-cov',[
 {xs:its.map(r=>r.iter),ys:its.map(r=>r.systems),color:'#1f4e79'},
 {xs:its.map(r=>r.iter),ys:its.map(r=>r.papers),color:'#c55a11'}],
 {forceLo:0,forceHi:35,xlabel:'迭代轮次 →  累计覆盖（蓝=系统 / 橙=论文）',dec:0});
let it='<thead><tr><th>轮</th><th>标题</th><th>类型</th><th>总分</th><th>|Δ|</th><th>churn</th></tr></thead><tbody>';
for(const r of its){
  it+=`<tr><td>${r.iter}</td><td>${r.title}</td><td>${r.type}</td><td class="num">${r.total??''}</td><td class="num">${r.delta||0}</td><td class="num">+${r.churn.added}/${r.churn.changed}</td></tr>`;
}
document.getElementById('iter-table').innerHTML=it+'</tbody>';

// ---------- ③ quadrant ----------
const pos={'lantai':[1.2,1.3],'agentmemory':[1.6,2.2],'memobase':[4.0,4.2],'langmem':[4.6,5.2],
 'letta':[4.0,6.2],'mem0':[5.2,6.6],'supermemory':[7.6,5.2],'memori':[6.6,5.6],
 'zep_graphiti':[4.4,8.6],'cognee':[4.0,7.6],'memos':[4.6,8.0],'mirix':[5.0,7.0],
 'openviking':[5.6,7.8],'chatgpt_memory':[9.5,5.4],'claude_memory':[8.8,7.0],
 'cursor_memories':[8.2,3.0],'openai_agents_sessions':[3.0,2.8]};
const stars=Object.fromEntries(DATA.landscape.map(s=>[s.id,s.stars]));
{
 const W=900,H=560,P=40;
 const X=x=>P+(W-2*P)*x/10, Y=y=>H-P-(H-2*P)*y/10;
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%">`;
 s+=`<rect x="${P}" y="${P}" width="${W-2*P}" height="${H-2*P}" fill="#fffdf8" stroke="#e4ddd0"/>`;
 s+=`<line x1="${X(5)}" y1="${Y(10)}" x2="${X(5)}" y2="${Y(0)}" stroke="#d8cfc0"/>`;
 s+=`<line x1="${X(0)}" y1="${Y(5)}" x2="${X(10)}" y2="${Y(5)}" stroke="#d8cfc0"/>`;
 s+=`<text x="${X(1)}" y="${Y(0.4)}" font-size="13" fill="#2e7d32" font-weight="bold">本地优先 × 轻量内嵌</text>`;
 s+=`<text x="${X(7.4)}" y="${Y(0.4)}" font-size="13" fill="#c55a11" font-weight="bold">托管/闭源</text>`;
 for(const [id,[x,y]] of Object.entries(pos)){
   const st=stars[id]||0; const r=id==='lantai'?9:Math.max(5,Math.min(16,Math.sqrt(st)/16));
   const c=id==='lantai'?'#8b1a1a':'#5b7ea8';
   s+=`<circle cx="${X(x)}" cy="${Y(y)}" r="${r}" fill="${c}" opacity=".78" stroke="#fff"/>`;
   const below=['chatgpt_memory','cursor_memories'].includes(id);
   s+=`<text x="${X(x)}" y="${Y(y)+(below?r+13:-r-6)}" font-size="11.5" text-anchor="middle" fill="${c}" font-weight="${id==='lantai'?'bold':'normal'}">${id==='lantai'?'兰台':id}</text>`;
 }
 s+=`<text x="${W/2}" y="${H-8}" font-size="11" text-anchor="middle" fill="#8a7f6f">横轴：本地优先 ←→ 托管优先；纵轴：轻量内嵌 ←→ 重量基础设施；气泡≈GitHub stars（2026-09-19）</text>`;
 s+='</svg>';
 document.getElementById('quadrant').innerHTML=s;
}

// ---------- ④ radar ----------
{
 const peers={'lantai':'#8b1a1a','mem0':'#1f4e79','zep_graphiti':'#2e7d32','agentmemory':'#c55a11'};
 const byId=Object.fromEntries(DATA.systems.map(s=>[s.id,s]));
 const W=640,H=520,cx=W/2,cy=H/2+6,R=190,n=dimIds.length;
 let s=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:640px">`;
 for(let ring=1;ring<=5;ring++){
   let pts=[];
   for(let i=0;i<n;i++){const a=-Math.PI/2+2*Math.PI*i/n;pts.push(`${cx+R*ring/5*Math.cos(a)},${cy+R*ring/5*Math.sin(a)}`);}
   s+=`<polygon points="${pts.join(' ')}" fill="none" stroke="#eee4d4"/>`;
   s+=`<text x="${cx+6}" y="${cy-R*ring/5+4}" font-size="9" fill="#b3a890">${ring}</text>`;
 }
 for(let i=0;i<n;i++){
   const a=-Math.PI/2+2*Math.PI*i/n;
   s+=`<line x1="${cx}" y1="${cy}" x2="${cx+R*Math.cos(a)}" y2="${cy+R*Math.sin(a)}" stroke="#eee4d4"/>`;
   const lx=cx+(R+30)*Math.cos(a), ly=cy+(R+22)*Math.sin(a);
   s+=`<text x="${lx}" y="${ly}" font-size="11.5" text-anchor="middle" fill="#5b4632">${dimNames[i]}</text>`;
 }
 for(const [id,color] of Object.entries(peers)){
   const sc=byId[id].scores;
   let pts=[];
   for(let i=0;i<n;i++){const a=-Math.PI/2+2*Math.PI*i/n;const r=R*sc[dimIds[i]]/5;pts.push(`${cx+r*Math.cos(a)},${cy+r*Math.sin(a)}`);}
   s+=`<polygon points="${pts.join(' ')}" fill="${color}" fill-opacity=".10" stroke="${color}" stroke-width="2"/>`;
 }
 let ly=H-58;
 const label={'lantai':'兰台','mem0':'Mem0','zep_graphiti':'Zep/Graphiti','agentmemory':'agentmemory'};
 for(const [id,color] of Object.entries(peers)){
   s+=`<rect x="${cx-200}" y="${ly-10}" width="12" height="12" fill="${color}"/>`;
   s+=`<text x="${cx-182}" y="${ly}" font-size="12">${label[id]} ${byId[id].total.toFixed(2)}</text>`;
   ly+=18;
 }
 s+='</svg>';
 document.getElementById('radar').innerHTML=s;
}

// ---------- ⑤ roadmap ----------
const roadmap=[
 ['P0-1','可靠性收口','解释/修复已知测试失败，全量门禁绿；核心函数不 mock 冒烟','继承主线 A'],
 ['P0-2','撤回/删除四分法','纠错/撤回/归档/删除语义分离，覆盖 SQLite/FTS5/Chroma/摘要/缓存/宿主副本；撤回后禁用命中=0','iter-12 D23'],
 ['P0-3','E1 阶段化评测','dry-run 拆「提取→闸门→入库→索引→召回→注入」各步成功率与失败归因（HaluMem 式）','iter-09 E1'],
 ['P0-4','注入回执链','事件链绑定 request/session/turn/memory id；宿主回执证明实际注入；来源可追溯率 100%','iter-13 D24'],
 ['P1-1','事件时间与时效视图','MemoryItem 增 event_time（可空+精度）；时间窗过滤+当前/历史双视图；迟到更正走冲突裁决','iter-11 D20（P1 首位）'],
 ['P1-2','宿主矩阵','shell hook 泛化为 Claude Code/Codex/Cursor 钩子适配；≥3 宿主端到端冒烟','iter-13 D24'],
 ['P1-3','巩固可信度','沉潜/反思产物一律过审+快照；巩固产物 100% 带提案记录','iter-08（TRUSTMEM）'],
 ['P2-1','episode 影子→离线消融','区分召回/注入/采用/任务完成；≥100 有效样本；不足不调权','D18'],
 ['P2-2','RL 影子观察','记录记忆操作与结果供离线分析（Mem-α/Memory-R1 路线）；不承诺实现、不进生产调权','iter-08 D16'],
 ['P2-3','操作级自证发布','E1/E2 指标+judge 协议公开；LongMemEval-S/MemoryAgentBench 子集作外部锚点；不追 LoCoMo 榜','iter-09 E3'],
 ['P2-4','考功/结晶转化率','技能结晶、经验→规则的实际转化率公开数字','iter-10 D19'],
 ['P2-5','治理四原语对照表','scoped retrieval / temporal supersession / provenance / policy propagation 对齐文档','iter-12 D22'],
];
let rh='<thead><tr><th>编号</th><th>事项</th><th>内容与验收</th><th>依据</th></tr></thead><tbody>';
for(const [no,t,c,ev] of roadmap){rh+=`<tr><td><b>${no}</b></td><td>${t}</td><td>${c}</td><td>${ev}</td></tr>`;}
document.getElementById('roadmap').innerHTML=rh+'</tbody>';

// ---------- ⑥ landscape ----------
let lh='<thead><tr><th>系统</th><th>类别</th><th>许可</th><th>stars*</th><th>核验</th><th>MCP</th><th>存储</th><th>关键机制（≤5）</th></tr></thead><tbody>';
for(const s of DATA.landscape){
  const cat={'oss_leader':'头部开源','oss_emerging':'新兴开源','platform':'平台原生','framework':'框架原生','upstream':'上游','self':'本项目'}[s.category]||s.category;
  const v=s.verification||''; const vc=v.includes('verified')?'verified':'inherited';
  lh+=`<tr${s.id==='lantai'?' class="lantai-row"':''}><td><a href="${s.url}" target="_blank">${s.id==='lantai'?'兰台 (Lantai)':s.name}</a></td><td>${cat}</td><td>${s.license||''}</td><td class="num">${s.stars==null?'—':s.stars.toLocaleString()}</td><td><span class="tag ${vc}">${vc==='verified'?'已核验':'继承'}</span></td><td>${s.mcp===true?'有':s.mcp===false?'无':String(s.mcp||'')}</td><td>${s.storage||''}</td><td>${(s.mechanisms||[]).map(m=>'· '+m).join('<br>')}</td></tr>`;
}
document.getElementById('landscape').innerHTML=lh+'</tbody>';

// ---------- ⑦ papers ----------
let ph='<thead><tr><th>论文/基准</th><th>年份</th><th>arXiv/出处</th><th>机制/定位</th></tr></thead><tbody>';
for(const p of DATA.papers){
  const link=p.url&&p.url.startsWith('http')?`<a href="${p.url}" target="_blank">${p.arxiv||p.venue||'链接'}</a>`:(p.arxiv||p.venue||'');
  ph+=`<tr><td>${p.title}</td><td class="num">${p.year||''}</td><td>${link}</td><td>${p.mechanism||''}</td></tr>`;
}
document.getElementById('papers').innerHTML=ph+'</tbody>';

// ---------- ⑧ dims ----------
let dh='<thead><tr><th>维度</th><th>权重</th><th>定义</th></tr></thead><tbody>';
const defmap={'写入与提取':'提取、缓冲合并、去重、置信度分诊、噪声过滤、异步化','召回与检索':'混合检索（词法/向量/结构）、多跳联想、降级容灾、可诊断性','时间感知与冲突消解':'双时间轴、时效失效而非硬删、冲突裁决及理由留存','遗忘与生命周期':'衰减/强化、可逆归档、巩固压缩、生命状态机、退役','治理与安全':'provenance、纠错/撤回/删除语义、权限隔离、注入防御、审计','人机协同':'待审队列、回滚检查点、主动探针、受控自主审批','多宿主接入':'MCP/插件/客户端矩阵、注入通道、可移植性','评估自证':'公开基准或自建可复现评测、影子评估、回归门禁','程序性与技能记忆':'skill 分层存储、复用结晶、失败学习闭环','性能与运维':'轻量内嵌、异步管线、可观测面板、延迟与 token 成本'};
for(const d of DATA.dims){dh+=`<tr><td><b>${d.name}</b></td><td class="num">${d.weight}</td><td>${defmap[d.name]||''}</td></tr>`;}
document.getElementById('dims').innerHTML=dh+'</tbody>';
</script>
</body>
</html>
"""

html = html.replace('__DATA_JSON__', json.dumps(DATA, ensure_ascii=False))
with io.open('dashboard.html', 'w', encoding='utf-8') as f:
    f.write(html)
print('dashboard.html written:', len(html), 'chars')
