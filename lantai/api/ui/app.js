import {api, clearApiKey, getApiKey, onUnauthorized, saveApiKey} from './api.js';
import {$, formatDate, node, setAfterUndo, showToast} from './dom.js';
import { initTerminal, activateTerminalView, deactivateTerminal } from './terminal.js';
import {
  activateMonitorView, deactivateMonitor, initMonitor, refreshMonitorBadge,
} from './monitor.js';
import {initVault, loadLimbo, loadTree, loadVault} from './vault.js';
import {
  initStudio, loadConsolidationReport, loadPersona, loadScratchpad,
  triggerConsolidation,
} from './studio.js';
import {initPlayground} from './playground.js';

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
const HEALTH_LABELS = {healthy: '健康', degraded: '波动', critical: '告急'};

const state = {
  sections: Object.fromEntries(SECTION_ORDER.map(name => [name, {items: [], total: 0, limit: 50, error: ''}])),
  selected: new Map(), activeId: '', activeDetail: null, detailDirty: false, refreshPending: false,
  filters: {q: '', kind: '', risk: ''}, view: 'tasks', tree: null,
  aiTriageMap: new Map(),
};

const shell = $('.app-shell');
const queue = $('#queue');
const inspector = $('#inspector');
let refreshTimer = null;

function updateConnection() {
  const hasKey = Boolean(getApiKey());
  $('#connectionText').textContent = hasKey ? '已设置密钥' : '本机连接';
  $('#connectionDot').style.background = hasKey ? 'var(--accent)' : 'var(--green)';
}

// api.js 401 统一钩子：提示并引导到连接设置（节流，避免轮询刷屏）
let unauthorizedAt = 0;
onUnauthorized(() => {
  const now = Date.now();
  if (now - unauthorizedAt < 10000) return;
  unauthorizedAt = now;
  showToast('未授权（401）：请在连接设置中检查 API Key');
  const dialog = $('#connectionDialog');
  if (dialog && !dialog.open) {
    $('#apiKeyInput').value = getApiKey();
    $('#rememberKey').checked = Boolean(localStorage.getItem('lantai_api_key'));
    dialog.showModal();
  }
});

function setView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(value => value.classList.remove('active'));
  document.querySelectorAll('[data-view]').forEach(value => {
    const active = value.dataset.view === view;
    value.classList.toggle('active', active);
    if (active) value.setAttribute('aria-current', 'page');
    else value.removeAttribute('aria-current');
  });
  const targetView = $(`#${view}View`);
  if (targetView) targetView.classList.add('active');
  if (view !== 'tasks') closeInspector();
  if (view !== 'monitor') deactivateMonitor();
  if (view !== 'terminal') deactivateTerminal();

  if (view === 'monitor') {
    activateMonitorView();
  } else if (view === 'overview') {
    loadOverview();
  } else if (view === 'vault') {
    loadVault();
    loadLimbo();
    loadTree();
  } else if (view === 'terminal') {
    activateTerminalView();
  } else if (view === 'studio') {
    loadPersona();
    loadScratchpad($('#scratchpadSession')?.value || 'default');
    loadConsolidationReport();
  } else if (view === 'tasks') {
    loadQueue();
  }
}

// ===== 0. 中枢总览 (Overview) =====
async function loadOverview() {
  try {
    const [stats, work, persona, monitor, digest] = await Promise.all([
      api('/stats').catch(() => ({})),
      api('/work-items?section=immediate_action&limit=1').catch(() => ({})),
      api('/persona').catch(() => ({})),
      api('/monitor/overview').catch(() => ({})),
      api('/digest/today').catch(() => null),
    ]);

    const total = stats.total_memories ?? stats.total ?? '—';
    $('#ovTotalMem').textContent = String(total);
    $('#ovPendingTasks').textContent = String(work.counts?.immediate_action ?? work.total ?? 0);
    $('#ovHealthStatus').textContent = HEALTH_LABELS[monitor.summary?.status] || '—';
    const zeroRate = monitor.quality?.zero_recall_rate;
    $('#ovZeroRecall').textContent = (zeroRate === undefined || zeroRate === null)
      ? '—' : `${(Number(zeroRate) * 100).toFixed(1)}%`;
    $('#ovUserMem').textContent = String(stats.by_domain?.user ?? '—');
    $('#ovSessionMem').textContent = String(stats.by_domain?.session ?? '—');
    $('#ovAgentMem').textContent = String(stats.by_domain?.agent ?? '—');
    $('#ovActivePersona').textContent = (persona && persona.name) || '兰台执笔';

    // 今日盘点（digest/today：stats 优先，content 首段兑底）
    const digestBox = $('#ovDigestContent');
    if (digestBox) {
      if (!digest || digest.ok === false) {
        digestBox.textContent = '今日盘点尚未生成';
      } else if (digest.stats && Object.keys(digest.stats).length) {
        // 嵌套 stats 拍平成点路径行（避免横向滚动的 JSON 坨）
        const rows = flattenStats(digest.stats).slice(0, 20);
        digestBox.replaceChildren(...rows.map(([k, v]) => {
          const row = node('div', 'digest-row');
          row.append(node('span', 'digest-key', k), node('b', '', v));
          return row;
        }));
      } else {
        digestBox.textContent = String(digest.content || '今日盘点尚未生成').slice(0, 600);
      }
    }
  } catch (err) {
    console.warn('读取总览指标失败', err);
  }
}

// stats 嵌套对象 → [[点路径, 标量字符串]]；数组保留 JSON（如 conf_buckets 的桶）
function flattenStats(obj, prefix = '', out = []) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) flattenStats(v, key, out);
    else out.push([key, Array.isArray(v) ? JSON.stringify(v) : String(v)]);
  }
  return out;
}

// ===== 结晶检测入口（POST /crystals/detect） =====
async function runCrystalDetect() {
  const btn = $('#crystalDetectBtn');
  if (btn) { btn.disabled = true; btn.textContent = '🔮 检测中...'; }
  try {
    const res = await api('/crystals/detect', {method: 'POST'});
    const found = res.created ?? res.candidates ?? res.count ?? 0;
    showToast(`结晶检测完成：新增 ${found} 个候选`);
    await loadQueue();
  } catch (err) {
    showToast(err.status === 404 ? '结晶功能未启用（FEATURE_CRYSTALS 已关闭）' : `结晶检测失败: ${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔮 结晶检测'; }
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
    slot.error = error.status === 404
      ? '该分区功能未启用（特性开关已关闭）'  // P1-12：特性路由 404 友好降级
      : error.message;
    if (error.status === 401) showToast('连接需要 API Key');
  }
}

async function loadQueue({silent = false} = {}) {
  if (!silent) renderLoading();
  await Promise.all(SECTION_ORDER.map(fetchSection));
  // P1-6：清理已不在队列中的选中项（防止批量栏计数虚高、批量操作打到幽灵 id）
  const alive = new Set(SECTION_ORDER.flatMap(name => (state.sections[name].items || []).map(item => item.id)));
  for (const id of [...state.selected.keys()]) {
    if (!alive.has(id)) state.selected.delete(id);
  }
  renderQueue({force: true});
  if (state.activeId) {
    if (state.detailDirty) markRefreshPending();
    else await openInspector(state.activeId, {silent: true});
  }
}

// P1-5：编辑未提交时来了新数据——只在首次进入 pending 状态时提示一次，不每轮轮询轰炸
function markRefreshPending() {
  if (state.refreshPending) return;
  state.refreshPending = true;
  showToast('详情已有新数据；你的编辑未提交，提交前请先刷新（会丢弃未提交修改）');
}

let lastQueueFingerprint = '';

function queueFingerprint() {
  return JSON.stringify(SECTION_ORDER.map(name => {
    const slot = state.sections[name];
    return [slot.total, slot.error, (slot.items || []).map(item =>
      `${item.id}:${item.status ?? ''}:${item.due_at ?? ''}:${item.priority ?? ''}`)];
  }));
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

function renderQueue({force = false} = {}) {
  // 轮询指纹比对：队列未变化时跳过全量重建，避免丢焦点/滚动位置
  const fp = queueFingerprint();
  if (!force && fp === lastQueueFingerprint) return;
  lastQueueFingerprint = fp;
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
  main.append(node('strong', '', item.title), node('small', '', item.summary || (item.badges || []).join(' · ')));
  
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
  // P1-5 补充：有未提交修改时，手动切换/重开会覆盖编辑——先确认，不静默丢弃
  if (!silent && state.detailDirty) {
    if (!window.confirm('当前详情有未提交的修改，继续将丢弃这些修改。确认？')) return;
    state.detailDirty = false;
  }
  state.activeId = id;
  shell.classList.add('inspector-open'); inspector.setAttribute('aria-hidden', 'false');
  document.querySelectorAll('.work-row').forEach(row => row.classList.toggle('active', row.dataset.id === id));
  $('#inspectorKind').textContent = KIND_LABELS[item.kind];
  $('#inspectorTitle').textContent = item.title;
  if (!silent) $('#inspectorBody').replaceChildren(node('div', 'inspector-empty', '正在读取详情'));
  try {
    const detail = await api(`/work-items/detail/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.source_id)}`);
    if (state.detailDirty && silent) { markRefreshPending(); return; }
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
      const field = $('#crystalSteps');
      if (!field || !field.isConnected) { showToast('详情面板已过期，请重新打开该条目'); return; }
      const steps = field.value.split('\n').map(value => value.trim()).filter(Boolean);
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
    await api(`/conflicts/${detail.item.source_id}/resolve`, {
      method: 'POST',
      body: JSON.stringify({decision, note: answer.value}),
    });
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
    // 提纯是 LLM 长任务：放宽到 3 分钟
    const res = await api(`/candidates/${encodeURIComponent(item.source_id)}/refine`, {method: 'POST', timeout: 180000});
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
    // LLM 逐条研判是长任务：单次超时放宽到 5 分钟（默认 30s 会在候选多时误报超时）
    const res = await api('/candidates/ai_triage?limit=50', {method: 'POST', timeout: 300000});
    const recs = res.recommendations || [];
    state.aiTriageMap.clear();
    recs.forEach(r => state.aiTriageMap.set(r.id, r));
    renderQueue({force: true});
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

function bindEvents() {
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
  $('#refreshButton').addEventListener('click', () => loadQueue());
  $('#aiTriageBtn')?.addEventListener('click', runAiAutoTriage);
  $('#systemRefresh').addEventListener('click', async () => { setView('tasks'); await loadQueue(); });
  $('#closeInspector').addEventListener('click', closeInspector);
  $('#clearSelection').addEventListener('click', () => { state.selected.clear(); renderQueue({force: true}); });
  $('#searchInput').addEventListener('input', debounce(event => { state.filters.q = event.target.value.trim(); loadQueue(); }, 260));
  $('#kindFilter').addEventListener('change', event => { state.filters.kind = event.target.value; loadQueue(); });
  $('#riskFilter').addEventListener('change', event => { state.filters.risk = event.target.value; loadQueue(); });
  $('#connectionButton').addEventListener('click', () => {
    $('#apiKeyInput').value = getApiKey(); $('#rememberKey').checked = Boolean(localStorage.getItem('lantai_api_key'));
    $('#connectionDialog').showModal();
  });
  $('#connectionDialog').addEventListener('close', async event => {
    // P1-8：只在真正改动了连接配置时才全量刷新；取消不应触发队列重建
    if (event.target.returnValue === 'save') {
      saveApiKey($('#apiKeyInput').value, $('#rememberKey').checked);
      updateConnection(); await loadQueue();
    } else if (event.target.returnValue === 'clear') {
      clearApiKey();
      updateConnection(); await loadQueue();
    }
  });
  $('#themeButton').addEventListener('click', toggleTheme);

  // 各视图模块自行绑定（模式同 terminal/monitor）
  initStudio();
  initVault();
  initPlayground();
  $('#crystalDetectBtn')?.addEventListener('click', runCrystalDetect);
  $('#quickConsolidateBtn')?.addEventListener('click', triggerConsolidation);

  document.querySelectorAll('[data-goto]').forEach(btn => {
    btn.addEventListener('click', () => setView(btn.dataset.goto));
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
  initTheme(); updateConnection(); bindEvents();
  setAfterUndo(loadQueue);
  initMonitor({notify: message => showToast(message)});
  await loadQueue();
  refreshMonitorBadge();
  refreshTimer = setInterval(() => {
    if (document.hidden) return;
    if (state.view === 'tasks') loadQueue({silent: true});
    refreshMonitorBadge();
  }, 30000);
}

window.addEventListener('beforeunload', () => clearInterval(refreshTimer));
init();
