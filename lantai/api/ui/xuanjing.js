/**
 * 兰台 · 悬镜（Xuanjing）记忆星图与 3D/力导向可视化观测台
 *
 * 连接 /graph/xuanjing 服务端拓扑聚合接口，结合 D3 力导向仿真与恒星光晕衰减映射，
 * 提供全景神经元记忆星图、因果溯源连线与场景星系聚簇观测。
 */

import { api } from './api.js';
import { $ } from './dom.js';

let simulation = null;
let currentData = { nodes: [], edges: [], clusters: [], stats: {} };
let selectedNode = null;
let zoomBehavior = null;

export function initXuanjing() {
  const refreshBtn = $('#xuanjingRefreshBtn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadXuanjingGraph());
  }

  const laneFilter = $('#xuanjingLaneFilter');
  if (laneFilter) {
    laneFilter.addEventListener('change', () => loadXuanjingGraph());
  }

  const limitFilter = $('#xuanjingLimitFilter');
  if (limitFilter) {
    limitFilter.addEventListener('change', () => loadXuanjingGraph());
  }

  const searchInput = $('#xuanjingSearchInput');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => filterNodes(e.target.value));
  }

  const closeDrawerBtn = $('#xuanjingCloseDrawer');
  if (closeDrawerBtn) {
    closeDrawerBtn.addEventListener('click', () => closeDrawer());
  }
}

export function activateXuanjingView() {
  requestAnimationFrame(() => {
    loadXuanjingGraph();
  });
}

export function deactivateXuanjing() {
  if (simulation) {
    simulation.stop();
  }
}

export async function loadXuanjingGraph() {
  const container = $('#xuanjingSvgContainer');
  if (!container) return;

  const lane = $('#xuanjingLaneFilter')?.value || '';
  const limit = $('#xuanjingLimitFilter')?.value || '200';
  let url = `/graph/xuanjing?limit=${encodeURIComponent(limit)}`;
  if (lane && lane !== 'all') {
    url += `&lane=${encodeURIComponent(lane)}`;
  }

  try {
    const statusEl = $('#xuanjingStatus');
    if (statusEl) statusEl.textContent = '正在观测星图脉络…';

    const data = await api(url);
    currentData = data || { nodes: [], edges: [], clusters: [], stats: {} };

    updateHeaderStats(currentData.stats);
    renderXuanjing(currentData);

    if (statusEl) {
      statusEl.textContent = `已投射 ${currentData.nodes.length} 恒星节点 · ${currentData.edges.length} 引力因果边`;
    }
  } catch (err) {
    console.error('加载悬镜星图失败', err);
    const statusEl = $('#xuanjingStatus');
    if (statusEl) statusEl.textContent = `星图投射受阻: ${err.message}`;
  }
}

function updateHeaderStats(stats) {
  if (!stats) return;
  const countEl = $('#xuanjingNodeCount');
  if (countEl) countEl.textContent = stats.total_nodes ?? currentData.nodes.length;
  const edgeEl = $('#xuanjingEdgeCount');
  if (edgeEl) edgeEl.textContent = stats.total_edges ?? currentData.edges.length;
  const avgDecayEl = $('#xuanjingAvgDecay');
  if (avgDecayEl) {
    const val = stats.avg_decay_score != null ? (stats.avg_decay_score * 100).toFixed(1) + '%' : '—';
    avgDecayEl.textContent = val;
  }
}

export function renderXuanjing(data) {
  const svgEl = document.querySelector('#xuanjingSvg');
  if (!svgEl || !window.d3) return;

  const d3 = window.d3;
  const svg = d3.select(svgEl);
  svg.selectAll('*').remove();

  const width = svgEl.clientWidth || 960;
  const height = svgEl.clientHeight || 640;

  // 定义渐变与光晕滤镜 (Glow Filter for Star Halos)
  const defs = svg.append('defs');

  const filter = defs.append('filter')
    .attr('id', 'stellar-glow')
    .attr('x', '-50%')
    .attr('y', '-50%')
    .attr('width', '200%')
    .attr('height', '200%');
  filter.append('feGaussianBlur')
    .attr('stdDeviation', '4')
    .attr('result', 'coloredBlur');
  const feMerge = filter.append('feMerge');
  feMerge.append('feMergeNode').attr('in', 'coloredBlur');
  feMerge.append('feMergeNode').attr('in', 'SourceGraphic');

  // 主图层
  const g = svg.append('g').attr('class', 'xuanjing-viewport');

  zoomBehavior = d3.zoom()
    .scaleExtent([0.15, 4])
    .on('zoom', (event) => {
      g.attr('transform', event.transform);
    });
  svg.call(zoomBehavior);

  const nodes = data.nodes.map(d => ({ ...d }));
  const nodeMap = new Map(nodes.map(n => [n.id, n]));

  // 转换边引用的 source 与 target 为节点对象
  const links = data.edges
    .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
    .map(e => ({
      ...e,
      source: nodeMap.get(e.source),
      target: nodeMap.get(e.target),
    }));

  // 创建 D3 力导向仿真
  if (simulation) simulation.stop();
  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(d => {
      if (['causes', 'leads_to', 'triggered_by'].includes(d.relation)) return 70;
      return 110;
    }))
    .force('charge', d3.forceManyBody().strength(d => -80 - (d.weight || 1) * 35))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => getNodeRadius(d) + 12));

  // 绘制边 (Links)
  const link = g.append('g')
    .attr('class', 'xuanjing-links')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', d => getEdgeColor(d.relation))
    .attr('stroke-width', d => isCausalEdge(d.relation) ? 2.2 : 1.2)
    .attr('stroke-opacity', d => isCausalEdge(d.relation) ? 0.85 : 0.45)
    .attr('stroke-dasharray', d => isCausalEdge(d.relation) ? 'none' : '4,3');

  // 节点组 (Nodes)
  const nodeGroup = g.append('g')
    .attr('class', 'xuanjing-nodes')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('class', 'xuanjing-node-item')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended));

  // 恒星外部衰减光晕 (Stellar Decay Halo)
  nodeGroup.append('circle')
    .attr('class', 'stellar-halo')
    .attr('r', d => getNodeRadius(d) + (d.decay_score || 0.5) * 8)
    .attr('fill', d => getLaneColor(d.lane))
    .attr('opacity', d => Math.max(0.12, (d.decay_score || 0.5) * 0.45))
    .attr('filter', 'url(#stellar-glow)');

  // 恒星核心球体 (Core Sphere)
  nodeGroup.append('circle')
    .attr('class', 'stellar-core')
    .attr('r', d => getNodeRadius(d))
    .attr('fill', d => getLaneColor(d.lane))
    .attr('stroke', d => d.shared_scope === 'swarm_shared' ? '#f59e0b' : '#ffffff')
    .attr('stroke-width', d => d.shared_scope === 'swarm_shared' ? 2.5 : 1.2)
    .attr('stroke-opacity', 0.9);

  // 同袍殿堂徽章（若是 swarm_shared 则画一圈金色光环点）
  nodeGroup.filter(d => d.shared_scope === 'swarm_shared')
    .append('circle')
    .attr('r', d => getNodeRadius(d) + 4)
    .attr('fill', 'none')
    .attr('stroke', '#fbbf24')
    .attr('stroke-width', 1)
    .attr('stroke-dasharray', '2,2');

  // 节点文字摘要 (Node Label)
  nodeGroup.append('text')
    .text(d => truncate(d.content || d.key || d.id, 12))
    .attr('x', d => getNodeRadius(d) + 6)
    .attr('y', 4)
    .attr('fill', 'var(--fg-2, #d0d6e0)')
    .attr('font-size', '11px')
    .attr('font-family', 'var(--font-mono, monospace)')
    .style('pointer-events', 'none')
    .style('user-select', 'none');

  // 节点交互事件
  nodeGroup.on('click', (event, d) => {
    event.stopPropagation();
    openDrawer(d);
  });

  svg.on('click', () => {
    closeDrawer();
  });

  // 仿真步进更新
  simulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x)
      .attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x)
      .attr('y2', d => d.target.y);

    nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
  }

  function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
  }

  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  }
}

function getNodeRadius(d) {
  const w = d.weight || 1.0;
  return Math.min(24, Math.max(7, Math.round(5 + w * 2.5)));
}

function getLaneColor(lane) {
  switch (lane) {
    case 'user': return '#10b981'; // 翡翠绿
    case 'agent': return '#8b5cf6'; // 智慧紫
    case 'session': return '#3b82f6'; // 澄澈蓝
    case 'fact': return '#ec4899'; // 事实粉
    case 'preference': return '#f59e0b'; // 偏好琥珀
    default: return '#c69b51'; // 吉金原色
  }
}

function getEdgeColor(relation) {
  switch (relation) {
    case 'causes':
    case 'leads_to':
    case 'triggered_by':
      return '#f97316'; // 因果火橙色
    case 'supports': return '#10b981'; // 支持生机绿
    case 'refines': return '#06b6d4'; // 精化青
    case 'contradicts': return '#ef4444'; // 矛盾赤红
    case 'supersedes': return '#a855f7'; // 代替绛紫
    default: return '#6b7280';
  }
}

function isCausalEdge(relation) {
  return ['causes', 'leads_to', 'triggered_by'].includes(relation);
}

function truncate(str, max) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

function filterNodes(keyword) {
  const query = (keyword || '').trim().toLowerCase();
  const d3 = window.d3;
  if (!d3) return;

  d3.selectAll('.xuanjing-node-item').each(function(d) {
    const matched = !query ||
      (d.content && d.content.toLowerCase().includes(query)) ||
      (d.key && d.key.toLowerCase().includes(query)) ||
      (d.id && d.id.toLowerCase().includes(query));
    d3.select(this)
      .transition()
      .duration(200)
      .style('opacity', matched ? 1 : 0.15);
  });
}

function openDrawer(node) {
  selectedNode = node;
  const drawer = $('#xuanjingDrawer');
  if (!drawer) return;

  drawer.removeAttribute('hidden');
  drawer.classList.add('open');

  $('#xjdId').textContent = node.id || '—';
  $('#xjdContent').textContent = node.content || '（空）';
  $('#xjdLane').textContent = node.lane || '通用';
  $('#xjdScene').textContent = node.scene_id || '未分类星系';
  $('#xjdScope').textContent = node.shared_scope === 'swarm_shared' ? '🏛️ 同袍公共知识殿堂' : '🔒 私有记忆';
  $('#xjdWeight').textContent = (node.weight != null ? node.weight.toFixed(2) : '1.00');

  const decayPct = ((node.decay_score || 0) * 100).toFixed(1);
  $('#xjdDecay').textContent = `${decayPct}%`;
  const decayBar = $('#xjdDecayBar');
  if (decayBar) decayBar.style.width = `${Math.min(100, Math.max(0, decayPct))}%`;

  $('#xjdUseCount').textContent = node.use_count ?? 0;
  $('#xjdImportance').textContent = node.importance != null ? node.importance.toFixed(2) : '0.50';
}

function closeDrawer() {
  selectedNode = null;
  const drawer = $('#xuanjingDrawer');
  if (drawer) {
    drawer.classList.remove('open');
    drawer.setAttribute('hidden', '');
  }
}
