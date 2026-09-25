# -*- coding: utf-8 -*-
"""月度执行甘特图（2026-09-26 → 10-24），延续 memory-landscape charts 风格"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import date

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei']
plt.rcParams['axes.unicode_minus'] = False

# (票据, 开始, 结束, 类别) 类别: P0/P1/critical
rows = [
    ('01 可靠性收口',        date(2026, 9, 26), date(2026, 9, 29), 'P0'),
    ('02 笔削验收收口',      date(2026, 9, 29), date(2026, 10, 2), 'P0'),
    ('03 E1 阶段化评测',     date(2026, 9, 28), date(2026, 10, 9), 'P0'),
    ('04 注入回执链',        date(2026, 9, 28), date(2026, 10, 9), 'P0'),
    ('05 更漏 Schema+迁移',  date(2026, 9, 30), date(2026, 10, 6), 'critical'),
    ('07 巩固产物过审',      date(2026, 10, 6), date(2026, 10, 9), 'P1'),
    ('08 写入侧提取纪律',    date(2026, 10, 7), date(2026, 10, 12), 'P1'),
    ('09 检索双视图',        date(2026, 10, 10), date(2026, 10, 16), 'critical'),
    ('10 迟到更正闭环',      date(2026, 10, 10), date(2026, 10, 16), 'critical'),
    ('11 E2 时间用例+收口',  date(2026, 10, 17), date(2026, 10, 23), 'critical'),
    ('06 宿主矩阵',          date(2026, 10, 17), date(2026, 10, 22), 'P1'),
]
colors = {'P0': '#1f4e79', 'P1': '#2e7d32', 'critical': '#8b1a1a'}

fig, ax = plt.subplots(figsize=(11.5, 6))
for i, (name, s, e, cat) in enumerate(rows):
    ax.barh(i, (e - s).days + 1, left=s, height=0.55,
            color=colors[cat], alpha=0.9 if cat == 'critical' else 0.65,
            edgecolor='white')
    ax.text(s, i - 0.42, name, fontsize=8.5, ha='left',
            color=colors[cat], fontweight='bold' if cat == 'critical' else 'normal')

for d, label in [(date(2026, 9, 26), 'W1'), (date(2026, 10, 3), 'W2'),
                 (date(2026, 10, 10), 'W3'), (date(2026, 10, 17), 'W4')]:
    ax.axvline(d, color='#cccccc', lw=1, ls='--')
    ax.text(d, len(rows) - 0.1, label, fontsize=10, color='#5b4632', fontweight='bold')
ax.axvline(date(2026, 10, 24), color='#cccccc', lw=1, ls='--')
for d in [date(2026, 10, 2), date(2026, 10, 9), date(2026, 10, 16)]:
    ax.scatter([d], [len(rows) - 0.35], marker='v', color='#c55a11', s=42, zorder=5)

ax.set_yticks([]); ax.invert_yaxis()
ax.set_ylim(len(rows) - 0.2, -0.8)
ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
ax.set_title('兰台月度执行序（2026-09-26 → 10-24）｜红=关键路径 05→{08,09,10}→11；▼=周五周报；数据源 spec.md §七',
             fontsize=12, fontweight='bold')
ax.grid(alpha=0.25, axis='x')
fig.tight_layout()
fig.savefig('charts/06_monthly_gantt_2026-10.png', dpi=150)
print('gantt saved')
