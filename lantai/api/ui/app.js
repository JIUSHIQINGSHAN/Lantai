import {api, clearApiKey, getApiKey, saveApiKey} from './api.js';

import { initTerminal, activateTerminalView } from './terminal.js';

// Init Terminal on load
document.addEventListener('DOMContentLoaded', () => {
  initTerminal();
});

const SECTION_ORDER = ['immediate_action', 'pending_decisions', 'organization_needed', 'runtime_status'];
const SECTION_LABELS = {
  immediate_action: '立即处理', pending_decisions: '待决',
  organization_needed: '待整理', runtime_status: '系统状态',
};
const KIND_LABELS = {
  candidate: '候选', proposal: '提案', conflict: '冲突', parameter: '参数',
  crystal: '结晶', memory: '记忆', worker: 'Worker',
};
const RISK_LABELS = {critical: '严重', high: '高风险', medium: '中风险', low: '低风险'};

const state = {
  sections: Object.fromEntries(SECTION_ORDER.map(name => [name, {items: [], total: 0, limit: 50, error: ''}])),
  selected: new Map(), activeId: '', activeDetail: null, detailDirty: false, refreshPending: false,
  filters: {q: '', kind: '', risk: ''}, view: 'tasks', tree: null,
  aiTriageMap: new Map(),
};

const $ = selector => document.querySelector(selector);
const shell = $('.app-shell');
const queue = $('#queue');
const inspector = $('#inspector');
const toast = $('#toast');
let toastTimer = null;
let refreshTimer = null;

function node(tag, className = '', text = '') {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== '') value.textContent = text;
  return value;
}

function formatDate(value, short = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', short
    ? {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}
    : {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}
  ).format(date);
}

function showToast(message, undo) {
  clearTimeout(toastTimer);
  toast.replaceChildren(document.createTextNode(message));
  if (undo) {
    const button = node('button', '', '撤销');
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { await undo(); showToast('已撤销'); await loadQueue(); }
      catch (error) { showToast(`撤销失败：${error.message}`); }
    });
    toast.append(button);
  }
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, undo ? 9000 : 4200);
}

function updateConnection() {
  const hasKey = Boolean(getApiKey());
  $('#connectionText').textContent = hasKey ? '已设置密钥' : '本机连接';
  $('#connectionDot').style.background = hasKey ? 'var(--accent)' : 'var(--green)';
}

function setView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(value => value.classList.remove('active'));
  document.querySelectorAll('[data-view]').forEach(value => value.classList.toggle('active', value.dataset.view === view));
  const targetView = $(`#${view}View`);
  if (targetView) targetView.classList.add('active');
  if (view !== 'tasks') closeInspector();

  if (view === 'overview') {
    loadOverview();
  } else if (view === 'vault') {
    loadVault();
  } else if (view === 'terminal') {
    activateTerminalView();
  } else if (view === 'studio') {
    loadPersona();
    loadScratchpad($('#scratchpadSession')?.value || 'default');
    loadConsolidationReport();
  } else if (view === 'system') {
    loadMonitor();
  } else if (view === 'tasks') {
    loadQueue();
  }
}

// ===== 0. 中枢总览 (Overview) =====
async function loadOverview() {
  try {
    const [stats, work, persona, monitor] = await Promise.all([
      api('/stats').catch(() => ({})),
      api('/work-items?section=immediate_action&limit=1').catch(() => ({})),
      api('/persona').catch(() => ({})),
      api('/monitor/health').catch(() => null),
    ]);

    const total = stats.total_memories ?? stats.total ?? '—';
    $('#ovTotalMem').textContent = String(total);
    $('#ovPendingTasks').textContent = String(work.counts?.immediate_action ?? work.total ?? 0);
    const healthMap = {ok: '健康', degraded: '降级', unknown: '未知'};
    if (monitor) {
      $('#ovHealthStatus').textContent = healthMap[monitor.overall] || '—';
      $('#ovHealthStatus').className = monitor.overall === 'ok' ? '' : 'val mon-warn';
    } else {
      $('#ovHealthStatus').textContent = '—';
    }
    $('#ovUserMem').textContent = String(stats.by_domain?.user ?? stats.by_lane?.user ?? '—');
    $('#ovSessionMem').textContent = String(stats.by_domain?.session ?? stats.by_lane?.session ?? '—');
    $('#ovAgentMem').textContent = String(stats.by_domain?.agent ?? stats.by_lane?.agent ?? '—');
    $('#ovActivePersona').textContent = (persona && persona.name) || '兰台执笔';
  } catch (err) {
    console.warn('读取总览指标失败', err);
  }
}

// ===== 1. 档案星图工作区 (Vault) =====
async function loadVault() {
  const q = $('#vaultSearchInput')?.value?.trim() || '';
  const domain = $('#vaultDomainFilter')?.value || '';
  const list = $('#vaultList');
  list.innerHTML = '<div class="inspector-empty">正在加载档案库记忆...</div>';

  try {
    const res = await api('/search', {
      method: 'POST',
      body: JSON.stringify({query: q || '记忆', top_k: 20, force: true, domain: domain || undefined}),
    });
    const items = res.results || res.memories || [];
    if (!items.length) {
      list.innerHTML = '<div class="inspector-empty">档案库暂无匹配记录</div>';
      return;
    }

    list.innerHTML = '';
    items.forEach((resItem, idx) => {
      const m = resItem.memory || resItem;
      const card = node('div', 'result-item');
      const meta = node('div', 'meta-row');
      meta.innerHTML = `<span>#${idx+1} [${m.domain || 'user'}/${m.lane || 'general'}] <code style="font-size:11px;color:var(--muted);">${m.id}</code></span><span>v${m.version || 1} · ${(m.memory_type || 'semantic')}</span>`;
      const body = node('div', 'text-body', m.content || m.key || '—');
      const breakdown = node('div', 'score-breakdown');
      breakdown.innerHTML = `<span>时效衰减: ${(m.decay_score ?? 1.0).toFixed(2)}</span><span>置信度: ${(m.confidence ?? 0.9).toFixed(2)}</span><span>重要性: ${(m.importance ?? 0.8).toFixed(2)}</span><span>创建: ${formatDate(m.created_at, true)}</span>`;
      card.appendChild(meta);
      card.appendChild(body);
      card.appendChild(breakdown);
      list.appendChild(card);
    });
  } catch (err) {
    list.innerHTML = `<div class="inspector-empty" style="color:var(--bad)">读取档案失败: ${err.message}</div>`;
  }
}

// ===== 2. 认知进化工作室 (Studio Tabs) =====
function setStudioTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.studioTab === tab));
  document.querySelectorAll('.studio-tab-panel').forEach(p => p.classList.remove('active'));
  const target = $(`#panel${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
  if (target) target.classList.add('active');
}

async function loadPersona() {
  try {
    const data = await api('/persona');
    if (data) {
      $('#personaName').value = data.name || 'default';
      $('#personaStyle').value = data.linguistic_style || '';
      $('#personaGuidelines').value = data.guidelines || '';
      $('#personaFacts').value = data.epistemic_facts || '';
    }
  } catch (err) {
    showToast(`读取人格失败: ${err.message}`);
  }
}

async function savePersona(e) {
  e.preventDefault();
  const payload = {
    name: $('#personaName').value.trim() || 'default',
    linguistic_style: $('#personaStyle').value.trim(),
    guidelines: $('#personaGuidelines').value.trim(),
    epistemic_facts: $('#personaFacts').value.trim(),
    is_active: true,
  };
  try {
    await api('/persona', {method: 'POST', body: JSON.stringify(payload)});
    showToast('👑 器识人格基座已成功更新并落库！');
  } catch (err) {
    showToast(`更新人格失败: ${err.message}`);
  }
}

async function loadScratchpad(sessionId) {
  try {
    const data = await api(`/scratchpad?session_id=${encodeURIComponent(sessionId)}`);
    const content = (data && data.content) || '';
    $('#scratchpadContent').value = content;
    $('#scratchpadCount').textContent = `${content.length} / 1000 字符`;
  } catch (err) {
    showToast(`读取札记失败: ${err.message}`);
  }
}

async function saveScratchpad(e) {
  e.preventDefault();
  const sid = $('#scratchpadSession').value.trim() || 'default';
  const content = $('#scratchpadContent').value;
  try {
    await api('/scratchpad', {method: 'POST', body: JSON.stringify({session_id: sid, content})});
    showToast('📝 札记工作区便签已成功保存！');
  } catch (err) {
    showToast(`保存札记失败: ${err.message}`);
  }
}

async function loadConsolidationReport() {
  try {
    const report = await api('/evolution/consolidate/report');
    if (report) {
      $('#conStatus').textContent = report.status || 'idle';
      $('#conGroups').textContent = report.consolidated_groups || 0;
      $('#conNew').textContent = report.new_memories || 0;
      $('#conPruned').textContent = report.pruned_count || 0;
      $('#conReportRaw').textContent = JSON.stringify(report, null, 2);
    }
  } catch (err) {
    $('#conReportRaw').textContent = `读取沉淀报告异常: ${err.message}`;
  }
}

async function triggerConsolidation() {
  const btn = $('#triggerConsolidateBtn');
  const quickBtn = $('#quickConsolidateBtn');
  if (btn) { btn.disabled = true; btn.textContent = '🌙 正在沉淀聚类与修剪...'; }
  if (quickBtn) { quickBtn.disabled = true; quickBtn.textContent = '🌙 沉淀中...'; }
  try {
    const res = await api('/evolution/consolidate', {method: 'POST'});
    showToast(`沉潜完成：提纯 ${res.new_memories || 0} 条主记忆，修剪 ${res.pruned_count || 0} 条衰减噪音`);
    await loadConsolidationReport();
  } catch (err) {
    showToast(`沉淀执行失败: ${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🌙 立即触发夜梦沉淀'; }
    if (quickBtn) { quickBtn.disabled = false; quickBtn.textContent = '🌙 立即夜梦沉淀'; }
  }
}


// ===== 3. 四路检索与探针演练场 (Playground) =====
async function runPlaygroundSearch() {
  const query = $('#playgroundQuery').value.trim();
  if (!query) {
    showToast('请输入检索测试文本');
    return;
  }
  const domain = $('#playgroundDomain').value;
  const force = Boolean($('#playgroundForce')?.checked);
  const resultsBox = $('#playgroundResults');
  const searchBtn = $('#playgroundSearchBtn');
  
  searchBtn.disabled = true;
  searchBtn.textContent = '🔍 检索中...';
  resultsBox.innerHTML = '<div class="inspector-empty">正在进行四路检索与拓扑探针检测...</div>';

  try {
    const payload = {
      query,
      top_k: 6,
      force,
      domain: domain === 'all' ? undefined : domain,
    };

    const [searchRes, probeRes] = await Promise.all([
      api('/search', {method: 'POST', body: JSON.stringify(payload)}),
      api('/probing/detect', {method: 'POST', body: JSON.stringify({query})}),
    ]);

    // 探针展示
    const alertBox = $('#probingAlertBox');
    if (probeRes && probeRes.probes && probeRes.probes.length > 0) {
      alertBox.hidden = false;
      $('#probeQuestionText').textContent = probeRes.probes[0].question || '存在未决记忆冲突，建议向用户求证。';
    } else {
      alertBox.hidden = true;
    }

    // 结果渲染
    const items = (searchRes && (searchRes.results || searchRes.memories)) || [];
    if (!items.length) {
      const gateMsg = searchRes && searchRes.gate && !searchRes.gate.needs_memory
        ? `（相关性闸门拦截: ${searchRes.gate.reason}，可勾选“强制放行”重试）`
        : '（未命中任何相关记忆）';
      resultsBox.innerHTML = `<div class="inspector-empty">零召回 ${gateMsg}</div>`;
      return;
    }

    resultsBox.innerHTML = '';
    items.forEach((resItem, idx) => {
      const m = resItem.memory || resItem;
      const score = typeof resItem.score === 'number' ? resItem.score : (m.score || 1.0);
      const card = node('div', 'result-item');
      
      const meta = node('div', 'meta-row');
      meta.innerHTML = `<span>#${idx+1} [${m.domain || 'user'}/${m.lane || 'general'}] <code style="font-size:11px;color:var(--muted);">${m.id || '—'}</code></span><span class="score-tag">得分: ${score.toFixed(4)}</span>`;
      
      const body = node('div', 'text-body', m.content || m.key || '—');
      
      const breakdown = node('div', 'score-breakdown');
      const decay = typeof m.decay_score === 'number' ? m.decay_score.toFixed(2) : '1.00';
      const conf = typeof m.confidence === 'number' ? m.confidence.toFixed(2) : '0.90';
      const imp = typeof m.importance === 'number' ? m.importance.toFixed(2) : '0.80';
      const ver = m.version || 1;
      const mtype = m.memory_type || 'semantic';
      breakdown.innerHTML = `<span>类型: ${mtype}</span><span>时效衰减: ${decay}</span><span>置信度: ${conf}</span><span>重要性: ${imp}</span><span>版本: v${ver}</span>`;
      
      card.appendChild(meta);
      card.appendChild(body);
      card.appendChild(breakdown);
      resultsBox.appendChild(card);
    });
  } catch (err) {
    resultsBox.innerHTML = `<div class="inspector-empty" style="color:var(--bad)">检索异常: ${err.message}</div>`;
  } finally {
    searchBtn.disabled = false;
    searchBtn.textContent = '🔍 检索测试';
  }
}


async function fetchSection(section) {
  const slot = state.sections[section];
  const params = new URLSearchParams({section, limit: String(slot.limit), offset: '0'});
  if (state.filters.q) params.set('q', state.filters.q);
  if (state.filters.kind) params.set('kind', state.filters.kind);
  if (state.filters.risk) params.set('risk', state.filters.risk);
  try {
    const data = await api(`/work-items?${params}`);
    slot.items = data.items;
    slot.total = data.total;
    slot.error = '';
  } catch (error) {
    slot.error = error.message;
    if (error.status === 401) showToast('连接需要 API Key');
  }
}

async function loadQueue({silent = false} = {}) {
  if (!silent) renderLoading();
  await Promise.all(SECTION_ORDER.map(fetchSection));
  renderQueue();
  if (state.activeId) {
    if (state.detailDirty) state.refreshPending = true;
    else await openInspector(state.activeId, {silent: true});
  }
}

function renderLoading() {
  queue.replaceChildren();
  SECTION_ORDER.forEach(section => {
    const wrap = node('section', 'queue-section');
    const header = node('div', 'section-header');
    header.append(node('h2', '', SECTION_LABELS[section]));
    wrap.append(header, node('div', 'queue-loading', '正在读取'));
    queue.append(wrap);
  });
}

function renderQueue() {
  queue.replaceChildren();
  let total = 0;
  let systemCount = 0;
  SECTION_ORDER.forEach(section => {
    const slot = state.sections[section];
    total += slot.total;
    if (section === 'runtime_status' || section === 'immediate_action') {
      systemCount += slot.items.filter(item => item.kind === 'worker').length;
    }
    const wrap = node('section', 'queue-section');
    const header = node('div', 'section-header');
    header.append(node('h2', '', SECTION_LABELS[section]), node('span', '', String(slot.total)));
    wrap.append(header);
    if (slot.error) {
      const error = node('div', 'queue-error');
      error.append(document.createTextNode(`读取失败：${slot.error}`));
      const retry = node('button', '', '重试');
      retry.addEventListener('click', async () => { await fetchSection(section); renderQueue(); });
      error.append(retry); wrap.append(error); queue.append(wrap); return;
    }
    if (!slot.items.length) {
      wrap.append(node('div', 'queue-empty', '当前无待办'));
      queue.append(wrap); return;
    }
    const columns = node('div', 'column-header');
    ['', '案牍', '原因', '类型', '风险', '期限'].forEach(label => columns.append(node('span', '', label)));
    wrap.append(columns);
    slot.items.forEach(item => wrap.append(renderRow(item)));
    if (slot.total > slot.items.length && slot.limit < 200) {
      const moreWrap = node('div', 'load-more-wrap', `已显示 ${slot.items.length} / ${slot.total}`);
      const more = node('button', 'load-more', '加载更多');
      more.addEventListener('click', async () => {
        slot.limit = Math.min(200, slot.limit + 50);
        await fetchSection(section); renderQueue();
      });
      moreWrap.append(more); wrap.append(moreWrap);
    }
    queue.append(wrap);
  });
  $('#queueSummary').textContent = total ? `${total} 项需要关注` : '当前没有需要处理的事项';
  $('#navTaskCount').textContent = String(total);
  $('#navSystemCount').textContent = String(systemCount);
  updateBatchbar();
}

function renderRow(item) {
  const row = node('div', 'work-row');
  row.dataset.id = item.id;
  row.dataset.priority = item.priority;
  row.tabIndex = 0;
  row.classList.toggle('active', item.id === state.activeId);
  const check = node('input', 'row-check');
  check.type = 'checkbox'; check.checked = state.selected.has(item.id);
  check.setAttribute('aria-label', `选择 ${item.title}`);
  check.addEventListener('click', event => {
    event.stopPropagation();
    if (check.checked) state.selected.set(item.id, item);
    else state.selected.delete(item.id);
    updateBatchbar();
  });
  const main = node('div', 'row-main');
  main.append(node('strong', '', item.title), node('small', '', item.summary || item.badges.join(' · ')));
  
  // AI 智能预审研判建议徽标
  const aiRec = state.aiTriageMap?.get(item.source_id);
  if (aiRec) {
    const badgeWrap = node('div', 'ai-triage-wrap');
    const ACTION_MAP = {
      approve: { text: '🟢 AI 建议批准', cls: 'approve' },
      reject: { text: '🔴 AI 建议淘汰', cls: 'reject' },
      refine: { text: '✨ AI 建议提纯', cls: 'refine' },
      manual: { text: '🟡 AI 建议复核', cls: 'manual' },
    };
    const confText = Math.round((aiRec.confidence_score || 0.5) * 100);
    const info = ACTION_MAP[aiRec.action] || ACTION_MAP.manual;
    const badge = node('span', `ai-triage-badge ${info.cls}`, `${info.text} (${confText}%)`);
    const reason = node('span', 'ai-triage-reason', aiRec.reason || '');
    badgeWrap.append(badge, reason);
    main.append(badgeWrap);
  }

  const reason = node('div', 'row-reason', item.reason);
  const kind = node('span', 'kind-label', KIND_LABELS[item.kind]);
  const time = node('span', 'row-time', formatDate(item.due_at || item.created_at, true));
  const risk = node('span', 'risk-label', RISK_LABELS[item.risk]); risk.dataset.risk = item.risk;
  row.append(check, main, reason, kind, risk, time);
  row.addEventListener('click', () => openInspector(item.id));
  row.addEventListener('keydown', event => {
    if (event.key === 'Enter') openInspector(item.id);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') moveRowFocus(row, event.key === 'ArrowDown' ? 1 : -1);
  });
  return row;
}

function moveRowFocus(current, delta) {
  const rows = [...document.querySelectorAll('.work-row')];
  const next = rows[rows.indexOf(current) + delta];
  if (next) next.focus();
}

function allItems() {
  return SECTION_ORDER.flatMap(section => state.sections[section].items);
}

async function openInspector(id, {silent = false} = {}) {
  const item = allItems().find(value => value.id === id);
  if (!item) { closeInspector(); return; }
  state.activeId = id;
  shell.classList.add('inspector-open'); inspector.setAttribute('aria-hidden', 'false');
  document.querySelectorAll('.work-row').forEach(row => row.classList.toggle('active', row.dataset.id === id));
  $('#inspectorKind').textContent = KIND_LABELS[item.kind];
  $('#inspectorTitle').textContent = item.title;
  if (!silent) $('#inspectorBody').replaceChildren(node('div', 'inspector-empty', '正在读取详情'));
  try {
    const detail = await api(`/work-items/detail/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.source_id)}`);
    if (state.detailDirty && silent) { state.refreshPending = true; return; }
    state.activeDetail = detail; state.detailDirty = false; state.refreshPending = false;
    renderDetail(detail);
  } catch (error) {
    if (error.status === 404) { showToast('该案牍已发生变化'); state.activeId = ''; await loadQueue(); return; }
    $('#inspectorBody').replaceChildren(node('div', 'inspector-empty', `读取失败：${error.message}`));
  }
}

function closeInspector() {
  state.activeId = ''; state.activeDetail = null; state.detailDirty = false; state.refreshPending = false;
  shell.classList.remove('inspector-open'); inspector.setAttribute('aria-hidden', 'true');
  document.querySelectorAll('.work-row').forEach(row => row.classList.remove('active'));
}

function detailSection(title, content) {
  const section = node('section', 'detail-section');
  section.append(node('h3', '', title));
  if (content instanceof Node) section.append(content);
  else section.append(node('p', '', String(content || '—')));
  return section;
}

function metaSection(item) {
  const grid = node('div', 'meta-grid');
  const values = [
    ['状态', item.status], ['风险', RISK_LABELS[item.risk]], ['进入原因', item.reason],
    ['截止', formatDate(item.due_at)], ['创建', formatDate(item.created_at)],
    ['分组', item.group_id || '—'],
  ];
  values.forEach(([label, value]) => {
    const cell = node('div'); cell.append(node('span', '', label), node('b', '', value)); grid.append(cell);
  });
  return detailSection('状态与期限', grid);
}

function rawSection(detail) {
  const box = node('details', 'raw-details');
  box.append(node('summary', '', '查看原始记录'));
  const pre = node('pre'); pre.textContent = JSON.stringify({source: detail.source, related: detail.related}, null, 2);
  box.append(pre); return detailSection('原始详情', box);
}

function renderDetail(detail) {
  const {item, source, related} = detail;
  const body = $('#inspectorBody'); body.replaceChildren();
  if (item.kind === 'candidate') {
    const doc = related.document || {};
    body.append(detailSection('来源与提取内容', `${doc.title || source.summary}\n\n${doc.content || source.summary || '—'}`));
    const claims = [...(source.claims || []), ...(source.actions || [])].join('\n');
    if (claims) body.append(detailSection('提取结果', claims));
  } else if (item.kind === 'proposal') {
    body.append(renderDiff(source.proposed_patch || {}, related.target_memory || {}));
    body.append(detailSection('变更原因', source.reason));
  } else if (item.kind === 'conflict') {
    body.append(detailSection('冲突来源', source.incoming_ref));
    body.append(detailSection('现有记忆', (related.memory || {}).content));
  } else if (item.kind === 'parameter') {
    body.append(renderParameterDiff(source));
    body.append(detailSection('预期收益', source.expected_benefit));
    body.append(detailSection('风险与验证', `${source.risk_notes || '—'}\n\n${source.validation_plan || '—'}`));
  } else if (item.kind === 'crystal') {
    body.append(detailSection('触发条件', source.trigger_rule));
    const field = node('textarea'); field.id = 'crystalSteps'; field.value = stepsFromCrystal(source);
    field.addEventListener('input', () => { state.detailDirty = true; });
    body.append(detailSection('执行步骤', field));
  } else if (item.kind === 'memory') {
    body.append(detailSection('记忆内容', source.content));
    body.append(renderTreePicker(related.tree || {}));
  } else if (item.kind === 'worker') {
    body.append(detailSection('运行异常', item.summary));
    body.append(detailSection('计划周期', scheduleText(related.schedule || {})));
  }
  body.append(metaSection(item));
  if (related.duplicates?.length) body.append(detailSection('重复候选', related.duplicates.map(value => value.summary).join('\n')));
  body.append(rawSection(detail));
  renderInspectorActions(detail);
  if (state.refreshPending) showToast('详情已有新数据，提交前请刷新');
}

function renderDiff(patch, target) {
  const box = node('div');
  const keys = new Set([...Object.keys(patch || {}), ...['content', 'key', 'memory_type', 'lane']]);
  keys.forEach(key => {
    if (!(key in patch)) return;
    const row = node('div', 'diff-row'); row.append(node('span', '', key));
    const values = node('div', 'diff-values');
    values.append(node('del', '', stringifyValue(target?.[key])));
    values.append(node('ins', '', stringifyValue(patch[key])));
    row.append(values); box.append(row);
  });
  return detailSection('字段差异', box);
}

function renderParameterDiff(source) {
  const box = node('div');
  (source.changes || []).forEach(change => {
    const row = node('div', 'diff-row'); row.append(node('span', '', change.name));
    const values = node('div', 'diff-values');
    values.append(node('del', '', String(change.before)), node('ins', '', `${change.after}\n${change.reason || ''}`));
    row.append(values); box.append(row);
  });
  return detailSection('参数差异', box);
}

function stringifyValue(value) {
  if (value === undefined || value === null || value === '') return '—';
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
}

function stepsFromCrystal(source) {
  return String(source.procedure || '').split('\n').map(value => value.replace(/^[-*]\s*/, '').trim()).filter(Boolean).join('\n');
}

function renderTreePicker(tree) {
  const wrap = node('div');
  const select = node('select'); select.id = 'treePath';
  select.append(new Option('选择分类节点', ''));
  (tree.nodes || []).forEach(value => select.append(new Option(`${'　'.repeat(Math.max(0, value.depth - 1))}${value.name}`, value.node_path)));
  select.addEventListener('change', () => { state.detailDirty = true; });
  wrap.append(select); return detailSection('分类树挂载', wrap);
}

function scheduleText(schedule) {
  if (!schedule.seconds) return '—';
  const hours = schedule.seconds / 3600;
  return hours >= 24 ? `${hours / 24} 天` : `${hours} 小时`;
}

function renderInspectorActions(detail) {
  const footer = $('#inspectorActions'); footer.replaceChildren();
  const {item} = detail;
  const add = (label, action, className = '') => {
    const button = node('button', className, label); button.addEventListener('click', action); footer.append(button);
  };
  if (item.kind === 'candidate') add('✨ AI 披沙提纯', () => refineCandidateItem(detail), 'secondary-button');
  if (item.allowed_actions.includes('defer')) add('延期', () => chooseDefer(detail), 'secondary-button');
  if (item.allowed_actions.includes('regenerate')) add('重新生成', () => regenerateParameter(detail), 'secondary-button');
  if (item.allowed_actions.includes('organize')) add('挂载', () => organizeMemory(detail));
  if (item.allowed_actions.includes('run')) add('立即运行', () => runWorker(detail));
  if (item.allowed_actions.includes('resolve')) add('确认冲突', () => decideConflict(detail, 'resolved'));
  if (item.allowed_actions.includes('dismiss')) add('标为误报', () => decideConflict(detail, 'dismissed'), 'secondary-button');
  if (item.allowed_actions.includes('reject')) add('拒绝', () => rejectItem(detail), 'danger');
  if (item.allowed_actions.includes('approve')) add(item.kind === 'candidate' ? '生成提案' : '批准', () => approveItem(detail));
}

async function ask({title, note = '', field = null, confirm = '确认', danger = false}) {
  const dialog = $('#actionDialog'); $('#dialogTitle').textContent = title; $('#dialogBody').replaceChildren();
  if (note) $('#dialogBody').append(node('p', 'dialog-note', note));
  let input = null;
  if (field) {
    const label = node('label', 'form-field'); label.append(node('span', '', field.label));
    input = field.type === 'select' ? node('select') : (field.type === 'textarea' ? node('textarea') : node('input'));
    if (field.type === 'select') field.options.forEach(value => input.append(new Option(value.label, value.value)));
    if (field.value) input.value = field.value;
    if (field.required) input.required = true;
    label.append(input); $('#dialogBody').append(label);
  }
  $('#dialogConfirm').textContent = confirm; $('#dialogConfirm').classList.toggle('danger', danger);
  dialog.showModal(); if (input) setTimeout(() => input.focus(), 0);
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'
      ? {confirmed: true, value: input ? input.value.trim() : ''} : {confirmed: false, value: ''}), {once: true});
  });
}

async function rejectItem(detail) {
  const answer = await ask({title: `拒绝${KIND_LABELS[detail.item.kind]}`, note: detail.item.title,
    field: {label: '裁决理由', type: 'textarea', required: true}, confirm: '确认拒绝', danger: true});
  if (!answer.confirmed || !answer.value) return;
  const {kind, source_id: id} = detail.item;
  try {
    if (kind === 'candidate') await api(`/candidates/${id}/review`, {method: 'POST', body: JSON.stringify({approve: false, reason: answer.value})});
    else if (kind === 'proposal') await api(`/proposals/${id}/decide`, {method: 'POST', body: JSON.stringify({approve: false, reason: answer.value})});
    else if (kind === 'parameter') await api(`/param-suggestions/${id}/decision`, {method: 'POST', body: JSON.stringify({decision: 'rejected', note: answer.value})});
    else if (kind === 'crystal') await api(`/crystals/${id}/decide`, {method: 'POST', body: JSON.stringify({approve: false, reason: answer.value, steps: []})});
    showToast('已拒绝'); closeInspector(); await loadQueue();
  } catch (error) { handleActionError(error); }
}

async function approveItem(detail) {
  const {item, source} = detail;
  const note = item.kind === 'candidate'
    ? '批准后只生成待审提案，尚不会写入记忆。'
    : '确认前请核对上方差异与影响。';
  const answer = await ask({title: item.kind === 'candidate' ? '生成待审提案' : `批准${KIND_LABELS[item.kind]}`, note, confirm: '确认'});
  if (!answer.confirmed) return;
  try {
    if (item.kind === 'candidate') {
      const result = await api(`/candidates/${item.source_id}/review`, {method: 'POST', body: JSON.stringify({approve: true, reason: 'console review'})});
      showToast('已生成待审提案'); closeInspector(); await loadQueue();
      const proposal = allItems().find(value => value.kind === 'proposal' && value.source_id === result.proposal_id);
      if (proposal) await openInspector(proposal.id);
    } else if (item.kind === 'proposal') {
      await api(`/proposals/${item.source_id}/decide`, {method: 'POST', body: JSON.stringify({approve: true, reason: 'console approved'})});
      showToast('提案已应用'); closeInspector(); await loadQueue();
    } else if (item.kind === 'parameter') {
      await api(`/param-suggestions/${item.source_id}/decision`, {method: 'POST', body: JSON.stringify({decision: 'accepted', note: 'console approved', expected_base_snapshot_hash: source.base_snapshot_hash})});
      showToast('参数建议已应用'); closeInspector(); await loadQueue();
    } else if (item.kind === 'crystal') {
      const steps = $('#crystalSteps').value.split('\n').map(value => value.trim()).filter(Boolean);
      if (!steps.length) { showToast('至少填写一个执行步骤'); return; }
      await api(`/crystals/${item.source_id}/decide`, {method: 'POST', body: JSON.stringify({approve: true, reason: 'console approved', steps})});
      showToast('技能已创建'); closeInspector(); await loadQueue();
    }
  } catch (error) { handleActionError(error); }
}

async function chooseDefer(detail) {
  const answer = await ask({title: '延期候选', field: {label: '延期时长', type: 'select', options: [
    {label: '3 天', value: '3'}, {label: '7 天', value: '7'},
  ]}, confirm: '延期'});
  if (!answer.confirmed) return;
  try {
    const result = await api(`/candidates/${detail.item.source_id}/defer`, {method: 'POST', body: JSON.stringify({days: Number(answer.value), expected_review_due_at: detail.source.review_due_at})});
    showToast('候选已延期', () => api(`/candidates/${detail.item.source_id}/defer/undo`, {method: 'POST', body: JSON.stringify({expected_review_due_at: result.review_due_at})}));
    await loadQueue();
  } catch (error) { handleActionError(error); }
}

async function organizeMemory(detail) {
  const path = $('#treePath')?.value;
  if (!path) { showToast('请选择分类节点'); return; }
  try {
    await api('/tree/assign', {method: 'POST', body: JSON.stringify({memory_id: detail.item.source_id, node_path: path})});
    showToast('记忆已挂载', () => api('/tree/unassign', {method: 'POST', body: JSON.stringify({memory_id: detail.item.source_id})}));
    closeInspector(); await loadQueue();
  } catch (error) { handleActionError(error); }
}

async function decideConflict(detail, decision) {
  const answer = await ask({title: decision === 'resolved' ? '确认冲突' : '标为误报',
    field: {label: '裁决理由', type: 'textarea', required: true}, confirm: '确认'});
  if (!answer.confirmed || !answer.value) return;
  try {
    const params = new URLSearchParams({decision, note: answer.value});
    await api(`/conflicts/${detail.item.source_id}/resolve?${params}`, {method: 'POST'});
    showToast('冲突账本已更新'); closeInspector(); await loadQueue();
  } catch (error) { handleActionError(error); }
}

async function regenerateParameter(detail) {
  const answer = await ask({title: '重新生成参数建议', note: '旧建议将被拒绝，来源论文重新进入建议队列。', confirm: '重新生成'});
  if (!answer.confirmed) return;
  try {
    await api(`/param-suggestions/${detail.item.source_id}/regenerate`, {method: 'POST'});
    showToast('来源论文已重新入队'); closeInspector(); await loadQueue();
  } catch (error) { handleActionError(error); }
}

async function runWorker(detail) {
  const answer = await ask({title: `运行 ${detail.item.source_id}`, note: '同名任务正在运行时不会重复启动。', confirm: '立即运行'});
  if (!answer.confirmed) return;
  try {
    await api(`/workers/${encodeURIComponent(detail.item.source_id)}/run`, {method: 'POST'});
    showToast('Worker 运行完成'); await loadQueue();
  } catch (error) { handleActionError(error); }
}

function handleActionError(error) {
  if (error.status === 409) { showToast('记录已变化，请重新确认'); loadQueue(); }
  else showToast(`操作失败：${error.message}`);
}

function updateBatchbar() {
  const bar = $('#batchbar'); const actions = $('#batchActions'); actions.replaceChildren();
  const values = [...state.selected.values()]; bar.hidden = values.length === 0;
  $('#selectionCount').textContent = `已选 ${values.length} 项`;
  if (!values.length) return;
  if (values.every(item => item.allowed_actions.includes('reject'))) {
    const button = node('button', 'danger', '批量拒绝'); button.addEventListener('click', batchReject); actions.append(button);
  }
  if (values.every(item => item.kind === 'memory')) {
    const button = node('button', '', '批量挂载'); button.addEventListener('click', batchOrganize); actions.append(button);
  }
  
  // AI 预审批量采纳快捷按钮
  const selectedCandidates = values.filter(item => item.kind === 'candidate');
  if (selectedCandidates.length && state.aiTriageMap.size > 0) {
    const aiRejects = selectedCandidates.filter(c => state.aiTriageMap.get(c.source_id)?.action === 'reject');
    const aiApproves = selectedCandidates.filter(c => state.aiTriageMap.get(c.source_id)?.action === 'approve');
    if (aiRejects.length) {
      const btn = node('button', 'danger', `采纳 AI 淘汰 (${aiRejects.length})`);
      btn.addEventListener('click', () => batchApplyAiDecision(aiRejects, 'reject'));
      actions.append(btn);
    }
    if (aiApproves.length) {
      const btn = node('button', '', `采纳 AI 批准 (${aiApproves.length})`);
      btn.addEventListener('click', () => batchApplyAiDecision(aiApproves, 'approve'));
      actions.append(btn);
    }
  }
}

async function refineCandidateItem(detail) {
  const {item} = detail;
  showToast('AI 披沙提纯中...');
  try {
    const res = await api(`/candidates/${encodeURIComponent(item.source_id)}/refine`, {method: 'POST'});
    showToast('披沙提纯完成');
    await openInspector(item.id);
    await loadQueue({silent: true});
  } catch (error) {
    handleActionError(error);
  }
}

async function runAiAutoTriage() {
  const btn = $('#aiTriageBtn');
  if (btn) { btn.disabled = true; btn.textContent = '🤖 研判中...'; }
  showToast('AI 正在扫描案牍并研判决策...');
  try {
    const res = await api('/candidates/ai_triage?limit=50', {method: 'POST'});
    const recs = res.recommendations || [];
    state.aiTriageMap.clear();
    recs.forEach(r => state.aiTriageMap.set(r.id, r));
    renderQueue();
    const rejectCount = recs.filter(r => r.action === 'reject').length;
    const approveCount = recs.filter(r => r.action === 'approve').length;
    showToast(`AI 研判完成：${approveCount} 建议批准，${rejectCount} 建议淘汰`);
  } catch (err) {
    showToast(`AI 预审失败: ${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🤖 AI 智能预审'; }
  }
}

async function batchApplyAiDecision(candidates, action) {
  const label = action === 'approve' ? '批准' : '淘汰';
  const answer = await ask({
    title: `批量采纳 AI ${label}建议`,
    note: `即将对已选的 ${candidates.length} 条候选执行【${label}】。`,
    confirm: `确认批量${label}`,
    danger: action === 'reject'
  });
  if (!answer.confirmed) return;

  const payload = candidates.map(c => ({
    id: c.source_id,
    action: action,
    reason: state.aiTriageMap.get(c.source_id)?.reason || `AI 批量${label}`
  }));

  try {
    const res = await api('/candidates/batch_apply_triage', {
      method: 'POST',
      body: JSON.stringify({actions: payload})
    });
    showToast(`批量处理完成：${label === '批准' ? res.approved : res.rejected} 项已处理`);
    state.selected.clear();
    await loadQueue();
  } catch (err) {
    handleActionError(err);
  }
}

async function batchReject() {
  const values = [...state.selected.values()];
  const grouped = values.reduce((acc, item) => ((acc[item.kind] ||= []).push(item), acc), {});
  const counts = Object.entries(grouped)
    .map(([kind, items]) => `${KIND_LABELS[kind]} ${items.length}`).join('，');
  const answer = await ask({title: `拒绝 ${values.length} 项`, note: counts,
    field: {label: '统一裁决理由', type: 'textarea', required: true}, confirm: '确认拒绝', danger: true});
  if (!answer.confirmed || !answer.value) return;
  try {
    const result = await api('/work-items/batch/reject', {method: 'POST', body: JSON.stringify({
      reason: answer.value, items: values.map(item => ({kind: item.kind, source_id: item.source_id})),
    })});
    state.selected.clear(); showToast(result.failed.length ? `${result.succeeded.length} 项成功，${result.failed.length} 项失败` : '批量拒绝完成'); await loadQueue();
  } catch (error) { handleActionError(error); }
}

async function batchDefer() {
  const values = [...state.selected.values()];
  const answer = await ask({title: `延期 ${values.length} 项候选`, field: {label: '延期时长', type: 'select', options: [
    {label: '3 天', value: '3'}, {label: '7 天', value: '7'},
  ]}, confirm: '延期'});
  if (!answer.confirmed) return;
  try {
    const result = await api('/work-items/batch/defer', {method: 'POST', body: JSON.stringify({days: Number(answer.value), items: values.map(item => ({candidate_id: item.source_id, expected_review_due_at: item.due_at}))})});
    state.selected.clear(); showToast(result.failed.length ? `${result.succeeded.length} 项成功，${result.failed.length} 项失败` : '批量延期完成'); await loadQueue();
  } catch (error) { handleActionError(error); }
}

async function batchOrganize() {
  const values = [...state.selected.values()];
  let tree;
  try { tree = await api('/tree'); } catch (error) { showToast(`分类树读取失败：${error.message}`); return; }
  const options = (tree.nodes || []).map(value => ({label: value.node_path, value: value.node_path}));
  if (!options.length) { showToast('分类树暂无节点'); return; }
  const answer = await ask({title: `挂载 ${values.length} 条记忆`, field: {label: '目标节点', type: 'select', options}, confirm: '挂载'});
  if (!answer.confirmed) return;
  try {
    const result = await api('/work-items/batch/organize', {method: 'POST', body: JSON.stringify({memory_ids: values.map(item => item.source_id), node_path: answer.value})});
    state.selected.clear(); showToast(result.failed.length ? `${result.succeeded.length} 项成功，${result.failed.length} 项失败` : '批量挂载完成'); await loadQueue();
  } catch (error) { handleActionError(error); }
}

// ===== 7. 瞭望台 · 后台监控面板 (Monitor) =====
const monitorState = { timer: null, loading: false, lastSnapshot: null };

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Number(bytes), i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtUptime(seconds) {
  if (!seconds && seconds !== 0) return '—';
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}天 ${h}时`;
  if (h > 0) return `${h}时 ${m}分`;
  return `${m}分`;
}

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return '从未';
  if (seconds < 60) return `${Math.floor(seconds)} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function fmtInterval(seconds) {
  if (!seconds && seconds !== 0) return '—';
  if (seconds % 86400 === 0) return `${seconds / 86400} 天`;
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  return `${Math.round(seconds / 60)} 分钟`;
}

function monEl(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = text;
  return el;
}

const MON_HEALTH = {
  ok: {label: '健康', cls: 'mon-ok'},
  degraded: {label: '降级', cls: 'mon-bad'},
  unknown: {label: '未知', cls: 'mon-warn'},
};
const MON_WORKER_STATE = {
  ok: {label: '正常', cls: 'mon-ok'},
  overdue: {label: '漏跑', cls: 'mon-bad'},
  disabled: {label: '已停用', cls: 'mon-muted'},
  never_run: {label: '未运行', cls: 'mon-warn'},
};

async function loadMonitor() {
  if (monitorState.loading) return;
  monitorState.loading = true;
  const btn = $('#monRefreshBtn');
  if (btn) btn.classList.add('spinning');
  try {
    const snap = await api('/monitor/snapshot');
    monitorState.lastSnapshot = snap;
    renderMonitor(snap);
  } catch (err) {
    $('#monSubtitle').textContent = `监控数据加载失败：${err.message}`;
  } finally {
    monitorState.loading = false;
    if (btn) btn.classList.remove('spinning');
  }
}

function renderMonitor(snap) {
  $('#monSubtitle').textContent = `数据更新于 ${formatDate(snap.generated_at, true)} · 依赖健康、闸门队列、Worker 调度、检索质量与吞吐一览`;

  // ---- 告警
  const alertsBox = $('#monAlerts');
  alertsBox.innerHTML = '';
  if (snap.alerts && snap.alerts.length) {
    alertsBox.hidden = false;
    snap.alerts.forEach(a => {
      const row = monEl('div', `mon-alert mon-alert-${a.level === 'critical' ? 'bad' : 'warn'}`);
      row.append(monEl('b', '', a.level === 'critical' ? '🔴 ' : '🟡 '),
                 monEl('span', '', a.message));
      alertsBox.appendChild(row);
    });
  } else {
    alertsBox.hidden = true;
  }

  // ---- 顶部指标卡
  const h = MON_HEALTH[snap.health.overall] || MON_HEALTH.unknown;
  const healthVal = $('#monHealth');
  healthVal.textContent = h.label;
  healthVal.className = `val ${h.cls}`;
  const degraded = Object.entries(snap.health.checks).filter(([, c]) => c.status === 'degraded');
  $('#monHealthDetail').textContent = degraded.length
    ? `${degraded.map(([n]) => n).join(' / ')} 异常`
    : 'SQLite · ChromaDB · LLM 均正常';

  $('#monUptime').textContent = fmtUptime(snap.runtime.uptime_seconds);
  $('#monVersion').textContent = `v${snap.runtime.version} · :${snap.runtime.port}`;

  const r24 = snap.retrieval.last_24h;
  const zeroPct = (r24.zero_recall_rate * 100).toFixed(1);
  const zeroVal = $('#monZeroRate');
  zeroVal.textContent = r24.real ? `${zeroPct}%` : '—';
  zeroVal.className = `val ${r24.real >= 10 && r24.zero_recall_rate >= 0.5 ? 'mon-bad' : 'mon-ok'}`;
  $('#monZeroDetail').textContent = `${r24.real} 次真实检索 · ${r24.zero_recall} 次零召回`;

  const p95 = r24.latency_p95_ms || 0;
  $('#monLatency').textContent = r24.real ? `${Math.round(p95)} ms` : '—';
  $('#monLatencyDetail').textContent = `均值 ${Math.round(r24.latency_avg_ms || 0)} ms · 估算 ${fmtTokens(r24.estimated_tokens)} tokens`;

  const q = snap.queues;
  const backlog = q.candidates_pending + q.proposals_pending + q.conflicts_open;
  const backlogVal = $('#monBacklog');
  backlogVal.textContent = backlog;
  backlogVal.className = `val ${backlog > 50 ? 'mon-bad' : backlog > 0 ? 'mon-warn' : 'mon-ok'}`;

  const storageTotal = (snap.storage.sqlite_bytes || 0) + (snap.storage.chromadb_bytes || 0);
  $('#monStorage').textContent = storageTotal ? fmtBytes(storageTotal) : '—';
  $('#monStorageDetail').textContent = `DB ${fmtBytes(snap.storage.sqlite_bytes)} · 向量 ${fmtBytes(snap.storage.chromadb_bytes)}`;

  // ---- 依赖健康
  const checks = $('#monChecks');
  checks.innerHTML = '';
  Object.entries(snap.health.checks).forEach(([name, c]) => {
    const state = MON_HEALTH[c.status] || MON_HEALTH.unknown;
    const chip = monEl('div', `mon-check ${state.cls === 'mon-ok' ? 'mon-ok' : c.status === 'degraded' ? 'mon-bad' : 'mon-warn'}`);
    chip.append(monEl('i', 'mon-dot'), monEl('b', '', name), monEl('span', '', c.detail || state.label));
    checks.appendChild(chip);
  });

  // ---- 闸门队列
  const queues = $('#monQueues');
  queues.innerHTML = '';
  const queueItems = [
    {label: '待审候选', value: q.candidates_pending, warn: 50, goto: 'tasks'},
    {label: '待决提案', value: q.proposals_pending, warn: 10, goto: 'tasks'},
    {label: '未消解冲突', value: q.conflicts_open, warn: 1, goto: 'tasks'},
    {label: '待裁参数建议', value: q.param_suggestions_pending, warn: 5, goto: 'tasks'},
    {label: '待审技能结晶', value: q.crystals_candidate, warn: 5, goto: 'tasks'},
  ];
  queueItems.forEach(item => {
    const row = monEl('div', 'mon-queue-row');
    row.append(monEl('span', 'mon-queue-label', item.label));
    const bar = monEl('div', 'mon-mini-bar');
    const fill = monEl('i');
    fill.style.width = `${Math.min(100, (item.value / Math.max(1, item.warn)) * 100)}%`;
    fill.className = item.value >= item.warn ? 'fill-bad' : item.value > 0 ? 'fill-warn' : 'fill-ok';
    bar.appendChild(fill);
    row.append(bar, monEl('b', item.value >= item.warn ? 'mon-bad' : item.value > 0 ? 'mon-warn' : 'mon-ok', String(item.value)));
    queues.appendChild(row);
  });

  // ---- 检索质量（7d）
  const r7 = snap.retrieval.last_7d;
  const retrieval = $('#monRetrieval');
  retrieval.innerHTML = '';
  const kv = (label, value, cls = '') => {
    const row = monEl('div', 'mon-kv');
    row.append(monEl('span', '', label), monEl('b', cls, value));
    return row;
  };
  retrieval.append(
    kv('7 天检索总量', `${r7.total} 次（真实 ${r7.real} · 噪音 ${r7.system_noise}）`),
    kv('零召回率', r7.real ? `${(r7.zero_recall_rate * 100).toFixed(1)}%（${r7.zero_recall} 次）` : '—',
       r7.real >= 20 && r7.zero_recall_rate >= 0.4 ? 'mon-bad' : ''),
    kv('平均延迟', r7.real ? `${Math.round(r7.latency_avg_ms)} ms` : '—'),
    kv('P95 延迟', r7.real ? `${Math.round(r7.latency_p95_ms)} ms` : '—'),
    kv('估算注入 tokens', fmtTokens(r7.estimated_tokens)),
    kv('潮波缓冲', `${snap.coalesce_buffer.total_messages || 0} 条 / ${snap.coalesce_buffer.active_keys || 0} 键（累计冲刷 ${snap.coalesce_buffer.flush_count || 0}）`),
  );

  // ---- 吞吐双序列柱图
  renderThroughput(snap.retrieval.daily_series);

  // ---- Worker 表
  const workersBody = $('#monWorkers');
  workersBody.innerHTML = '';
  snap.workers.forEach(w => {
    const state = MON_WORKER_STATE[w.state] || MON_WORKER_STATE.ok;
    const tr = monEl('tr');
    const nameTd = monEl('td', 'mon-worker-name');
    nameTd.append(document.createTextNode(w.label));
    nameTd.appendChild(monEl('span', 'mon-worker-id', w.name));
    const statusTd = monEl('td');
    statusTd.appendChild(monEl('span', `mon-pill ${state.cls}`, state.label));
    tr.append(
      nameTd,
      statusTd,
      monEl('td', 'mon-muted', w.enabled ? fmtInterval(w.interval_seconds) : '—'),
      monEl('td', 'mon-muted', w.last_run ? formatDate(w.last_run, true) : '—'),
      monEl('td', w.state === 'overdue' ? 'mon-bad' : 'mon-muted', w.enabled ? fmtAge(w.age_seconds) : '—'),
    );
    const actionTd = monEl('td');
    if (w.enabled) {
      const runBtn = monEl('button', 'mon-run-btn', '立即运行');
      runBtn.addEventListener('click', () => runMonitorWorker(w.name, runBtn));
      actionTd.appendChild(runBtn);
    }
    tr.appendChild(actionTd);
    workersBody.appendChild(tr);
  });

  // ---- 摄取任务
  const ingest = $('#monIngest');
  ingest.innerHTML = '';
  if (snap.ingestion.recent_jobs.length) {
    const table = monEl('table', 'mon-table');
    const thead = monEl('thead');
    const hr = monEl('tr');
    ['来源', '状态', '开始时间', '错误'].forEach(t => hr.appendChild(monEl('th', '', t)));
    thead.appendChild(hr); table.appendChild(thead);
    const tb = monEl('tbody');
    snap.ingestion.recent_jobs.forEach(j => {
      const tr = monEl('tr');
      const statusPill = monEl('span', `mon-pill ${j.status === 'failed' ? 'mon-bad' : j.status === 'done' ? 'mon-ok' : 'mon-warn'}`,
        {done: '完成', failed: '失败', running: '进行中', pending: '待处理'}[j.status] || j.status);
      const tdStatus = monEl('td'); tdStatus.appendChild(statusPill);
      tr.append(
        monEl('td', 'mon-muted', j.source_id),
        tdStatus,
        monEl('td', 'mon-muted', j.started_at ? formatDate(j.started_at, true) : '—'),
        monEl('td', j.error ? 'mon-bad' : 'mon-muted', j.error || '—'),
      );
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    ingest.appendChild(table);
  } else {
    ingest.appendChild(monEl('p', 'mon-muted', '暂无摄取任务记录'));
  }
  const srcInfo = monEl('p', 'mon-muted', `来源 ${snap.ingestion.sources_enabled}/${snap.ingestion.sources_total} 启用 · 近 24h 失败 ${snap.ingestion.jobs_failed_24h} 个`);
  ingest.appendChild(srcInfo);

  // ---- 运行时
  const runtime = $('#monRuntime');
  runtime.innerHTML = '';
  runtime.append(
    kv('服务版本', `v${snap.runtime.version}`),
    kv('监听', `${snap.runtime.host}:${snap.runtime.port}`),
    kv('调度器', snap.runtime.scheduler_enabled ? '运行中' : '已关闭'),
    kv('服务器时间', formatDate(snap.runtime.server_time, true)),
  );
  const featBox = monEl('div', 'mon-features');
  const featLabels = {
    digest: '每日盘点', reflect: '反思蒸馏', autodream: '雾梦蒸馏',
    param_advice: '参数建议', coalesce: '潮波合并', reranker: '重排器', scene_layer: '场景层',
  };
  Object.entries(snap.runtime.features).forEach(([key, on]) => {
    const pill = monEl('span', `mon-pill ${on ? 'mon-ok' : 'mon-muted'}`, `${featLabels[key] || key} · ${on ? '开' : '关'}`);
    featBox.appendChild(pill);
  });
  runtime.appendChild(featBox);

  // ---- 慢查询
  const slowBody = $('#monSlow');
  slowBody.innerHTML = '';
  if (snap.retrieval.slow_queries.length) {
    snap.retrieval.slow_queries.forEach(e => {
      const tr = monEl('tr');
      tr.append(
        monEl('td', 'mon-query-cell', e.query || '(空查询)'),
        monEl('td', 'mon-muted', e.lane),
        monEl('td', e.latency_ms > 2000 ? 'mon-bad' : e.latency_ms > 800 ? 'mon-warn' : '', `${e.latency_ms} ms`),
        monEl('td', e.zero_result ? 'mon-bad' : 'mon-ok', e.zero_result ? '零召回' : '有结果'),
        monEl('td', 'mon-muted', e.created_at ? formatDate(e.created_at, true) : '—'),
      );
      slowBody.appendChild(tr);
    });
  } else {
    const tr = monEl('tr');
    const td = monEl('td', 'mon-muted', '近 7 天暂无检索事件');
    td.colSpan = 5;
    tr.appendChild(td);
    slowBody.appendChild(tr);
  }
}

function fmtTokens(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万`;
  return String(n);
}

function renderThroughput(series) {
  const box = $('#monThroughput');
  box.innerHTML = '';
  if (!series || !series.length) { box.appendChild(monEl('p', 'mon-muted', '暂无数据')); return; }
  const maxV = Math.max(1, ...series.map(d => Math.max(d.new_memories, d.retrievals)));
  const wrap = monEl('div', 'mon-bars');
  series.forEach(d => {
    const col = monEl('div', 'mon-bar-col');
    const track = monEl('div', 'mon-bar-track');
    const memBar = monEl('i', 'bar-mem');
    memBar.style.height = `${(d.new_memories / maxV) * 100}%`;
    memBar.title = `新增记忆 ${d.new_memories}`;
    const reBar = monEl('i', 'bar-re');
    reBar.style.height = `${(d.retrievals / maxV) * 100}%`;
    reBar.title = `检索 ${d.retrievals}`;
    track.append(reBar, memBar);
    col.append(track, monEl('span', 'mon-bar-val', `${d.new_memories}/${d.retrievals}`),
               monEl('span', 'mon-bar-date', d.date.slice(5)));
    wrap.appendChild(col);
  });
  box.appendChild(wrap);
}

async function runMonitorWorker(name, btn) {
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = '运行中…';
  try {
    const result = await api(`/workers/${encodeURIComponent(name)}/run`, {method: 'POST'});
    showToast(`${name} 手动运行完成${result.result ? '' : ''}`);
    await loadMonitor();
  } catch (err) {
    showToast(`运行失败：${err.message}`);
    btn.disabled = false;
    btn.textContent = old;
  }
}

function startMonitorAutoRefresh() {
  if (monitorState.timer) clearInterval(monitorState.timer);
  monitorState.timer = setInterval(() => {
    if (!document.hidden && state.view === 'system' && $('#monAutoRefresh')?.checked) {
      loadMonitor();
    }
  }, 30000);
}

function bindEvents() {
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  $('#refreshButton').addEventListener('click', () => loadQueue());
  $('#aiTriageBtn')?.addEventListener('click', runAiAutoTriage);
  $('#monRefreshBtn')?.addEventListener('click', () => loadMonitor());
  $('#closeInspector').addEventListener('click', closeInspector);
  $('#clearSelection').addEventListener('click', () => { state.selected.clear(); renderQueue(); });
  $('#searchInput').addEventListener('input', debounce(event => { state.filters.q = event.target.value.trim(); loadQueue(); }, 260));
  $('#kindFilter').addEventListener('change', event => { state.filters.kind = event.target.value; loadQueue(); });
  $('#riskFilter').addEventListener('change', event => { state.filters.risk = event.target.value; loadQueue(); });
  $('#connectionButton').addEventListener('click', () => {
    $('#apiKeyInput').value = getApiKey(); $('#rememberKey').checked = Boolean(localStorage.getItem('lantai_api_key'));
    $('#connectionDialog').showModal();
  });
  $('#connectionDialog').addEventListener('close', async event => {
    if (event.target.returnValue === 'save') saveApiKey($('#apiKeyInput').value, $('#rememberKey').checked);
    if (event.target.returnValue === 'clear') clearApiKey();
    updateConnection(); await loadQueue();
  });
  $('#themeButton').addEventListener('click', toggleTheme);
  
  // 悬镜 Studio 表单绑定
  $('#personaForm')?.addEventListener('submit', savePersona);
  $('#scratchpadForm')?.addEventListener('submit', saveScratchpad);
  $('#scratchpadSession')?.addEventListener('change', e => loadScratchpad(e.target.value.trim() || 'default'));
  $('#scratchpadContent')?.addEventListener('input', e => {
    $('#scratchpadCount').textContent = `${e.target.value.length} / 1000 字符`;
  });
  $('#triggerConsolidateBtn')?.addEventListener('click', triggerConsolidation);
  $('#playgroundSearchBtn')?.addEventListener('click', runPlaygroundSearch);
  $('#playgroundQuery')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); runPlaygroundSearch(); }
  });

  // 选项卡与中枢快捷跳转
  document.querySelectorAll('[data-studio-tab]').forEach(btn => {
    btn.addEventListener('click', () => setStudioTab(btn.dataset.studioTab));
  });
  document.querySelectorAll('[data-goto]').forEach(btn => {
    btn.addEventListener('click', () => setView(btn.dataset.goto));
  });
  $('#quickConsolidateBtn')?.addEventListener('click', triggerConsolidation);

  // 档案库搜索
  $('#vaultSearchBtn')?.addEventListener('click', () => loadVault());
  $('#vaultSearchInput')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); loadVault(); }
  });
  $('#vaultDomainFilter')?.addEventListener('change', () => loadVault());

  // 演练场快捷词点击
  document.querySelectorAll('.chip-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const q = btn.dataset.query;
      if (q) {
        $('#playgroundQuery').value = q;
        runPlaygroundSearch();
      }
    });
  });

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && shell.classList.contains('inspector-open')) closeInspector();
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); setView('tasks'); $('#searchInput').focus();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      if (state.view === 'studio') {
        event.preventDefault();
        $('#scratchpadForm')?.requestSubmit();
      }
    }
  });
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'louchuang' ? 'jijin' : 'louchuang';
  document.documentElement.dataset.theme = next; localStorage.setItem('lantai-theme', next);
}

function debounce(fn, wait) {
  let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
}

function initTheme() {
  const queryTheme = new URLSearchParams(location.search).get('theme');
  document.documentElement.dataset.theme = queryTheme === 'louchuang' ? 'louchuang' : (localStorage.getItem('lantai-theme') || 'jijin');
}

async function init() {
  initTheme(); updateConnection(); bindEvents(); await loadQueue();
  startMonitorAutoRefresh();
  refreshTimer = setInterval(() => { if (!document.hidden && state.view === 'tasks') loadQueue({silent: true}); }, 30000);
}

window.addEventListener('beforeunload', () => clearInterval(refreshTimer));
init();
