# -*- coding: utf-8 -*-
"""R15: 生成迭代过程与最终结果图表（matplotlib, 零网络依赖）"""
import json, io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

iters = [json.loads(l) for l in io.open('iterations.jsonl', encoding='utf-8')]
F = json.load(io.open('framework.json', encoding='utf-8'))
DIMS = [d['id'] for d in F['dimensions']]
DNAMES = [d['name'] for d in F['dimensions']]
WG = [d['weight'] for d in F['dimensions']]

def gs(v):
    return float(v.get('score', 0)) if isinstance(v, dict) else float(v)

# ---------- 01 iteration process ----------
fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
fig.suptitle('兰台记忆方向调研：15 轮迭代过程（2026-09-19）', fontsize=15, fontweight='bold')

ax = axes[0][0]
xs = [r['iter'] for r in iters]
ys = [r.get('lantai_total', None) for r in iters]
ax.step(xs, ys, where='post', marker='o', color='#8b1a1a', lw=2)
ax.annotate('iter-03 程序化复核:\n修正 iter-01 手算误差\n(3.40→3.52, 非评分变化)', xy=(3, 3.52), xytext=(1.1, 3.66),
            fontsize=8, arrowprops=dict(arrowstyle='->', color='gray'))
ax.annotate('R7 内部核验:\ntemporal-0.5 / integration+1.0', xy=(7, 3.56), xytext=(7.8, 3.33),
            fontsize=8, arrowprops=dict(arrowstyle='->', color='gray'))
ax.annotate('D25 评分冻结', xy=(13, 3.56), xytext=(10.6, 3.70), fontsize=8)
ax.set_title('(a) 兰台加权总分演进（0-5 分制）', fontsize=11)
ax.set_ylim(3.2, 3.8); ax.set_xlabel('迭代轮次'); ax.set_ylabel('加权总分'); ax.grid(alpha=0.3)

ax = axes[0][1]
ds = [r.get('lantai_score_delta_sum', 0) for r in iters]
colors = ['#8b1a1a' if d > 0 else '#9aa5b1' for d in ds]
ax.bar(xs, ds, color=colors)
ax.set_title('(b) 每轮评分变化幅度（各维|Δ|之和；iter-03 含基线修正 0.12）', fontsize=11)
ax.set_xlabel('迭代轮次'); ax.set_ylabel('score_delta_sum'); ax.grid(alpha=0.3, axis='y')

ax = axes[1][0]
cov_sys, cov_pp, smax, pmax = [], [], 0, 0
for r in iters:
    c = r.get('coverage', {})
    smax = max(smax, c.get('systems', 0)); pmax = max(pmax, c.get('papers', 0))
    cov_sys.append(c.get('systems', smax)); cov_pp.append(c.get('papers', pmax))
ax.step(xs, cov_sys, where='post', marker='s', label='记忆系统 (landscape.json)', color='#1f4e79')
ax.step(xs, cov_pp, where='post', marker='^', label='论文/基准 (papers.json)', color='#c55a11')
ax.set_title('(c) 调研覆盖增长', fontsize=11)
ax.set_xlabel('迭代轮次'); ax.set_ylabel('累计条目'); ax.legend(fontsize=9); ax.grid(alpha=0.3)

ax = axes[1][1]
ca = [r['roadmap_churn']['added'] for r in iters]
cc = [r['roadmap_churn']['changed'] for r in iters]
ax.bar(xs, ca, label='路线图新增', color='#2e7d32')
ax.bar(xs, cc, bottom=ca, label='路线图修改', color='#f9a825')
ax.set_title('(d) 路线图 churn（R14 一次收口）', fontsize=11)
ax.set_xlabel('迭代轮次'); ax.set_ylabel('条目数'); ax.legend(fontsize=9); ax.grid(alpha=0.3, axis='y')
fig.tight_layout(rect=[0, 0, 1, 0.96]); fig.savefig('charts/01_iteration_process.png', dpi=150); plt.close(fig)

# ---------- 02 radar ----------
fig, ax = plt.subplots(figsize=(9, 7), subplot_kw=dict(polar=True))
n = len(DNAMES)
ang = np.linspace(0, 2*np.pi, n, endpoint=False).tolist(); ang += ang[:1]
series = [
    ('兰台 3.56', [gs(F['systems_scores']['lantai']['scores'][d]) for d in DIMS], '#8b1a1a'),
    ('Mem0 2.76', [gs(F['systems_scores']['mem0']['scores'][d]) for d in DIMS], '#1f4e79'),
    ('Zep/Graphiti 3.00', [gs(F['systems_scores']['zep_graphiti']['scores'][d]) for d in DIMS], '#2e7d32'),
    ('agentmemory 2.95', [gs(F['systems_scores']['agentmemory']['scores'][d]) for d in DIMS], '#c55a11'),
]
for name, vals, color in series:
    v = vals + vals[:1]
    ax.plot(ang, v, color=color, lw=1.8, label=name)
    ax.fill(ang, v, color=color, alpha=0.12)
ax.set_xticks(ang[:-1]); ax.set_xticklabels(DNAMES, fontsize=8)
ax.set_ylim(0, 5); ax.set_yticks([1, 2, 3, 4, 5])
ax.set_yticklabels(['1', '2', '3', '4', '5'], fontsize=7)
ax.legend(loc='lower right', bbox_to_anchor=(1.32, -0.10), fontsize=9)
ax.set_title('能力雷达：兰台 vs 三代表系统（verified 评分，2026-09-19）',
             fontsize=13, fontweight='bold', pad=28)
fig.tight_layout(); fig.savefig('charts/02_radar_peers.png', dpi=150); plt.close(fig)

# ---------- 03 quadrant ----------
pl = [
 ('lantai', '兰台', 1.2, 1.3, None),
 ('agentmemory', 'agentmemory', 1.6, 2.2, 28600),
 ('memobase', 'Memobase', 4.0, 4.2, 2900),
 ('langmem', 'LangMem', 4.6, 5.2, 1700),
 ('letta', 'Letta', 4.0, 6.2, 24800),
 ('mem0', 'Mem0', 5.2, 6.6, 65600),
 ('supermemory', 'Supermemory', 7.6, 5.2, 30200),
 ('memori', 'Memori', 6.6, 5.6, 16800),
 ('zep_graphiti', 'Zep/Graphiti', 4.4, 8.6, 31000),
 ('cognee', 'Cognee', 4.0, 7.6, 30800),
 ('memos', 'MemOS', 4.6, 8.0, 11500),
 ('mirix', 'MIRIX', 5.0, 7.0, 3400),
 ('openviking', 'OpenViking', 5.6, 7.8, 38000),
 ('chatgpt_memory', 'ChatGPT Memory', 9.5, 5.4, None),
 ('claude_memory', 'Claude Memory', 8.8, 7.0, None),
 ('cursor_memories', 'Cursor Mem.(已移除)', 8.2, 3.0, None),
 ('openai_agents_sessions', 'OpenAI Sessions', 3.0, 2.8, None),
]
fig, ax = plt.subplots(figsize=(11.5, 8.5))
ax.axvline(5, color='#cccccc', lw=1); ax.axhline(5, color='#cccccc', lw=1)
for pid, name, x, y, st in pl:
    size = 90 if st is None else max(90, min(2600, st/38.0))
    color = '#8b1a1a' if pid == 'lantai' else '#5b7ea8'
    ax.scatter([x], [y], s=size, color=color, alpha=0.75, edgecolors='white', zorder=3)
    below = pid in ('chatgpt_memory', 'cursor_memories')
    ax.annotate(name, (x, y), textcoords='offset points',
                xytext=(0, -16 if below else 10), ha='center', fontsize=8.5, color=color,
                fontweight='bold' if pid == 'lantai' else 'normal')
ax.text(1.0, 0.35, '本地优先 × 轻量内嵌', fontsize=10, color='#2e7d32', fontweight='bold')
ax.text(8.0, 0.35, '托管/闭源', fontsize=10, color='#c55a11', fontweight='bold')
ax.text(0.4, 9.55, '重量基础设施（图库/数据库集群）', fontsize=9, color='gray')
ax.set_xlabel(' ← 本地优先        托管优先 → ', fontsize=11)
ax.set_ylabel(' ← 轻量内嵌        重量基础设施 → ', fontsize=11)
ax.set_xlim(0, 10); ax.set_ylim(0, 10)
ax.set_title('2026-09 记忆系统格局定位图（气泡≈GitHub stars；位置为分析师判定，非实测）',
             fontsize=12.5, fontweight='bold')
ax.grid(alpha=0.2)
fig.tight_layout(); fig.savefig('charts/03_quadrant.png', dpi=150); plt.close(fig)

# ---------- 04 dimension comparison ----------
best = {d: max(gs(F['systems_scores'][s]['scores'][d])
               for s in F['systems_scores'] if s != 'lantai') for d in DIMS}
lant = [gs(F['systems_scores']['lantai']['scores'][d]) for d in DIMS]
bestv = [best[d] for d in DIMS]
ypos = np.arange(len(DIMS))
fig, ax = plt.subplots(figsize=(10, 6))
ax.barh(ypos+0.19, lant, height=0.38, color='#8b1a1a', label='兰台 3.56')
ax.barh(ypos-0.19, bestv, height=0.38, color='#9aa5b1', label='对照系统最佳值')
ax.set_yticks(ypos); ax.set_yticklabels([f'{n} (w={w})' for n, w in zip(DNAMES, WG)], fontsize=9)
ax.invert_yaxis(); ax.set_xlim(0, 5.4)
for i, (lv, bv) in enumerate(zip(lant, bestv)):
    ax.text(lv+0.08, i+0.19, f'{lv:g}', va='center', fontsize=8.5, color='#8b1a1a')
    ax.text(bv+0.08, i-0.19, f'{bv:g}', va='center', fontsize=8.5, color='#666')
ax.set_title('兰台 10 维得分 vs 对照系统最佳值（2026-09-19 verified）', fontsize=12.5, fontweight='bold')
ax.legend(loc='lower right'); ax.grid(alpha=0.25, axis='x')
fig.tight_layout(); fig.savefig('charts/04_dimension_comparison.png', dpi=150); plt.close(fig)

# ---------- 05 score matrix heatmap ----------
names = list(F['systems_scores'].keys())
disp = {'lantai': '兰台', 'mem0': 'Mem0', 'zep_graphiti': 'Zep/Graphiti', 'letta': 'Letta',
        'langmem': 'LangMem', 'supermemory': 'Supermemory', 'chatgpt_memory': 'ChatGPT Memory',
        'claude_memory': 'Claude Memory', 'cursor_memories': 'Cursor Mem.*',
        'openai_agents_sessions': 'OAISessions', 'memos': 'MemOS', 'memobase': 'Memobase',
        'cognee': 'Cognee', 'memu': 'MemU', 'mirix': 'MIRIX', 'memori': 'Memori',
        'agentmemory': 'agentmemory', 'openviking': 'OpenViking'}
M = np.array([[gs(F['systems_scores'][s]['scores'][d]) for d in DIMS] for s in names])
totals = [F['weighted_totals'].get(s, 0) for s in names]
order = np.argsort([-t for t in totals])
fig, ax = plt.subplots(figsize=(12.5, 8))
im = ax.imshow(M[order], cmap='RdYlGn', vmin=0, vmax=5, aspect='auto')
ax.set_xticks(range(len(DIMS))); ax.set_xticklabels(DNAMES, rotation=38, ha='right', fontsize=9)
ax.set_yticks(range(len(names)))
ax.set_yticklabels([f"{disp[names[i]]}  {totals[i]:.2f}" for i in order], fontsize=9.5)
for i in range(len(names)):
    for j in range(len(DIMS)):
        ax.text(j, i, f'{M[order[i]][j]:g}', ha='center', va='center', fontsize=8, color='#222')
ax.set_title('记忆系统能力评分矩阵（0-5，按加权总分排序；* Cursor Memories 已于 2.1.17 移除）',
             fontsize=12.5, fontweight='bold')
fig.colorbar(im, ax=ax, shrink=0.7, label='得分')
fig.tight_layout(); fig.savefig('charts/05_score_matrix.png', dpi=150); plt.close(fig)
print('charts done; systems scored:', len(names))
