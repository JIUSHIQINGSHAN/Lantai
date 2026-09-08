// 司天 · 后台运行监控面板（ADR-0044）
// 零构建原生 ES Module：只读聚合来自 /monitor/*，前端不复制任何业务规则。
import {api} from './api.js';

const REFRESH_MS = 10000;
let timer = null;
let notify = () => {};
let lastSnapshot = null;

const $ = selector => document.querySelector(selector);

function node(tag, className = '', text = '') {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== '') value.textContent = text;
  return value;
}

function fmtNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

function fmtBytes(bytes) {
  const size = Number(bytes || 0);
  if (!size) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const idx = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)));
  return `${(size / 1024 ** idx).toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

function fmtDuration(seconds) {
  const value = Number(seconds || 0);
  if (value < 60) return `${Math.round(value)} 秒`;
  if (value < 3600) return `${Math.round(value / 60)} 分钟`;
  if (value < 86400) return `${(value / 3600).toFixed(1)} 小时`;
  return `${(value / 86400).toFixed(1)} 天`;
}

function fmtAgo(seconds) {
  if (seconds === null || seconds === undefined) return '从未';
  return `${fmtDuration(seconds)}前`;
}

function fmtClock(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

function metricCard(value, label, tone = '') {
  const card = node('div', `metric-card${tone ? ` tone-${tone}` : ''}`);
  card.append(node('span', 'val', String(value)), node('span', 'lbl', label));
  return card;
}

function statusRow(label, value, tone = '') {
  const row = node('div', 'dep-row');
  row.append(node('span', 'dep-label', label));
  row.append(node('b', `dep-value${tone ? ` tone-${tone}` : ''}`, String(value)));
  return row;
}

function barRow(label, value, max, tone = '') {
  const row = node('div', 'bar-row');
  const head = node('div', 'bar-head');
  head.append(node('span', '', label), node('b', '', fmtNumber(value)));
  const track = node('div', 'bar-track');
  const fill = node('i', tone ? `tone-${tone}` : '');
  fill.style.width = `${max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0}%`;
  track.append(fill);
  row.append(head, track);
  return row;
}

// ===== 指标卡 =====
function renderMetrics(snapshot) {
  const box = $('#monitorMetrics');
  if (!box) return;
  const summary = snapshot.summary || {};
  const requests = (snapshot.requests || {}).window || {};
  const process = snapshot.process || {};
  const storage = (snapshot.storage || {}).database || {};
  const review = snapshot.review || {};
  const scheduler = snapshot.scheduler || {};
  const quality = snapshot.quality || {};
  const zeroRate = quality.zero_recall_rate;

  box.replaceChildren(
    metricCard(fmtDuration(summary.uptime_seconds), '进程运行时长'),
    metricCard(`${fmtNumber(requests.requests_per_minute, 1)}`, '请求 / 分钟',
      requests.error_rate > 0 ? 'bad' : 'ok'),
    metricCard(`${fmtNumber(requests.p95_ms, 0)} ms`, 'p95 延迟',
      Number(requests.p95_ms || 0) > 1000 ? 'warn' : 'ok'),
    metricCard(`${(Number(requests.error_rate || 0) * 100).toFixed(2)}%`, '5xx 错误率',
      Number(requests.error_rate || 0) > 0 ? 'warn' : 'ok'),
    metricCard(fmtNumber(summary.memories_total), '全库记忆'),
    metricCard(fmtNumber((review.candidates_pending_review || 0)
      + (review.proposals_pending || 0)), '待审积压',
      (review.candidates_pending_review || 0) > 100 ? 'warn' : ''),
    metricCard(`${scheduler.overdue_count || 0} / ${scheduler.job_count || 0}`, 'worker 逾期 / 作业数',
      scheduler.overdue_count ? 'bad' : 'ok'),
    metricCard(fmtBytes((storage.bytes || 0) + (storage.wal_bytes || 0)), '存储占用'),
    metricCard(zeroRate === undefined || zeroRate === null ? '—'
      : `${(Number(zeroRate) * 100).toFixed(1)}%`, '零召回率',
      Number(zeroRate || 0) > 0.3 ? 'warn' : 'ok'),
    metricCard(fmtNumber(process.rss_mb, 0), '进程内存 MB'),
  );
}

// ===== 告警 =====
function renderAlerts(snapshot) {
  const box = $('#monitorAlerts');
  if (!box) return;
  const alerts = snapshot.alerts || [];
  const badge = $('#monitorAlertCount');
  if (badge) badge.textContent = String(alerts.length);
  const nav = $('#navMonitorCount');
  if (nav) {
    nav.textContent = String(snapshot.summary?.critical_count || alerts.length);
    nav.classList.toggle('tone-bad', Boolean(snapshot.summary?.critical_count));
  }
  if (!alerts.length) {
    box.replaceChildren(node('div', 'alert-ok', '✅ 全部指标在阈值内，无告警'));
    return;
  }
  box.replaceChildren(...alerts.map(alert => {
    const card = node('div', `alert-card sev-${alert.severity}`);
    const head = node('div', 'alert-head');
    head.append(node('span', 'alert-sev', alert.severity), node('strong', '', alert.title));
    card.append(head, node('p', 'alert-detail', alert.detail));
    if (alert.suggestion) card.append(node('p', 'alert-fix', `建议：${alert.suggestion}`));
    return card;
  }));
}

// ===== 服务与依赖 =====
function renderDependencies(snapshot) {
  const box = $('#monitorDeps');
  if (!box) return;
  const security = snapshot.security || {};
  const dependency = snapshot.dependency || {};
  const storage = snapshot.storage || {};
  const scheduler = snapshot.scheduler || {};
  const platform = dependency.platform || {};
  const telemetry = security.telemetry || {};

  box.replaceChildren(
    statusRow('调度器', scheduler.running ? `运行中（${scheduler.job_count} 个作业）` : '未运行',
      scheduler.running ? 'ok' : 'bad'),
    statusRow('监听地址', `${security.host}:${security.port}`, security.loopback ? 'ok' : 'warn'),
    statusRow('API Key', security.api_key_configured ? '已配置' : '未配置（本机模式）',
      security.api_key_configured ? 'ok' : 'warn'),
    statusRow('API Key 记录', `${security.api_keys_active} 活跃 / ${security.api_keys_total} 总数`),
    statusRow('LLM', dependency.llm?.configured ? dependency.llm?.model : '未配置',
      dependency.llm?.configured ? 'ok' : 'warn'),
    statusRow('精排', dependency.reranker?.enabled ? dependency.reranker?.model : '已关闭'),
    statusRow('SQLite', storage.database?.available
      ? `${fmtBytes(storage.database?.bytes)} · schema v${storage.database?.schema_version}`
      : '不可用', storage.database?.available ? 'ok' : 'bad'),
    statusRow('FTS5 索引', `${fmtNumber(storage.table_rows?.memory_fts)} 行`),
    statusRow('向量库', storage.vector_store?.available
      ? `${fmtBytes(storage.vector_store?.bytes)}${storage.vector_store?.collection_count === null
        || storage.vector_store?.collection_count === undefined
        ? '' : ` · ${storage.vector_store.collection_count} 向量`}`
      : '目录不存在', storage.vector_store?.available ? 'ok' : 'warn'),
    statusRow('线程 / FD', `${snapshot.process?.threads} 线程 · ${snapshot.process?.open_fds} fd`),
    statusRow('CPU 时间', `${fmtNumber(snapshot.process?.cpu_seconds, 1)} 秒`),
    statusRow('遥测落库', `缓冲 ${telemetry.pending || 0} · 已写 ${telemetry.written || 0} · 采样 1/${telemetry.sample || 0}`),
    statusRow('运行平台', `${platform.system || ''} ${platform.release || ''} · ${platform.cpu_count} 核`),
  );
}

// ===== 请求趋势（纯 SVG，无外部图表库）=====
function renderChart(series) {
  const box = $('#monitorChart');
  if (!box) return;
  if (!series.length) {
    box.replaceChildren(node('div', 'inspector-empty', '暂无请求数据'));
    return;
  }
  const width = 760;
  const height = 170;
  const pad = {top: 12, right: 12, bottom: 22, left: 12};
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxCount = Math.max(1, ...series.map(row => row.count));
  const maxErr = Math.max(1, ...series.map(row => row.errors));
  const maxLat = Math.max(1, ...series.map(row => row.avg_ms));
  const step = innerW / series.length;

  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('class', 'chart-svg');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', '请求趋势图');

  [0.25, 0.5, 0.75].forEach(ratio => {
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', pad.left);
    line.setAttribute('x2', width - pad.right);
    line.setAttribute('y1', pad.top + innerH * ratio);
    line.setAttribute('y2', pad.top + innerH * ratio);
    line.setAttribute('class', 'chart-grid');
    svg.append(line);
  });

  series.forEach((row, index) => {
    const barHeight = (row.count / maxCount) * innerH;
    const rect = document.createElementNS(svgNS, 'rect');
    rect.setAttribute('x', pad.left + index * step + step * 0.15);
    rect.setAttribute('y', pad.top + innerH - barHeight);
    rect.setAttribute('width', Math.max(1, step * 0.7));
    rect.setAttribute('height', Math.max(row.count ? 1 : 0, barHeight));
    rect.setAttribute('class', 'chart-bar');
    const title = document.createElementNS(svgNS, 'title');
    title.textContent = `${fmtClock(row.ts * 1000)} · ${row.count} 请求 · ${row.errors} 错误 · 均 ${row.avg_ms}ms`;
    rect.append(title);
    svg.append(rect);
  });

  const polyline = (key, max, className) => {
    const points = series.map((row, index) => {
      const x = pad.left + index * step + step / 2;
      const y = pad.top + innerH - (row[key] / max) * innerH;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const line = document.createElementNS(svgNS, 'polyline');
    line.setAttribute('points', points);
    line.setAttribute('class', className);
    line.setAttribute('fill', 'none');
    svg.append(line);
  };
  polyline('errors', maxErr, 'chart-line chart-line-err');
  polyline('avg_ms', maxLat, 'chart-line chart-line-lat');

  const first = document.createElementNS(svgNS, 'text');
  first.setAttribute('x', pad.left);
  first.setAttribute('y', height - 6);
  first.setAttribute('class', 'chart-axis');
  first.textContent = fmtClock(series[0].ts * 1000);
  const last = document.createElementNS(svgNS, 'text');
  last.setAttribute('x', width - pad.right);
  last.setAttribute('y', height - 6);
  last.setAttribute('text-anchor', 'end');
  last.setAttribute('class', 'chart-axis');
  last.textContent = fmtClock(series[series.length - 1].ts * 1000);
  svg.append(first, last);

  box.replaceChildren(svg);
}

// ===== 端点表 =====
function renderEndpoints(snapshot) {
  const body = $('#monitorEndpoints tbody');
  if (!body) return;
  const rows = (snapshot.requests || {}).endpoints || [];
  const hint = $('#monitorWindowHint');
  if (hint) hint.textContent = `窗口 ${Math.round((snapshot.requests?.window_seconds || 0) / 60)} 分钟`;
  if (!rows.length) {
    body.replaceChildren(emptyRow(5, '窗口内暂无请求'));
    return;
  }
  body.replaceChildren(...rows.map(row => {
    const tr = node('tr');
    tr.append(
      node('td', 'mono', row.route),
      node('td', 'num', fmtNumber(row.count)),
      node('td', 'num', `${fmtNumber(row.avg_ms, 1)} ms`),
      node('td', 'num', `${fmtNumber(row.p95_ms, 1)} ms`),
      node('td', `num${row.errors ? ' tone-bad' : ''}`, fmtNumber(row.errors)),
    );
    return tr;
  }));
}

function emptyRow(cols, text) {
  const tr = node('tr');
  const td = node('td', 'empty-cell', text);
  td.colSpan = cols;
  tr.append(td);
  return tr;
}

// ===== 记忆管道 =====
function renderPipeline(snapshot) {
  const box = $('#monitorPipeline');
  if (!box) return;
  const memories = snapshot.memories || {};
  const pipeline = snapshot.pipeline || {};
  const lanes = memories.by_lane || {};
  const maxLane = Math.max(1, ...Object.values(lanes));
  const coalesce = pipeline.coalesce || {};

  const parts = [];
  const head = node('div', 'pipeline-head');
  head.append(
    node('span', '', `活跃 ${fmtNumber(memories.active)} · 归档 ${fmtNumber(memories.archived)} · 24h 新归档 ${fmtNumber(pipeline.archived_last_24h)}`),
  );
  parts.push(head);

  parts.push(node('div', 'pipeline-sub', '分轨分布'));
  Object.entries(lanes).sort((a, b) => b[1] - a[1]).forEach(([lane, count]) => {
    parts.push(barRow(lane, count, maxLane));
  });

  parts.push(node('div', 'pipeline-sub', '链路与积压'));
  const backlog = node('div', 'pipeline-stats');
  [
    ['待审候选', pipeline.candidates_pending_review],
    ['超 24h 未裁决', pipeline.candidates_pending_over_24h],
    ['待决提案', pipeline.proposals_pending],
    ['未决冲突', pipeline.conflicts_open],
    ['潮波缓冲', `${coalesce.active_keys || 0} 键 / ${coalesce.total_messages || 0} 条`],
    ['潮波冲刷', `${coalesce.flush_count || 0} 次`],
  ].forEach(([label, value]) => {
    const row = node('div', 'dep-row');
    row.append(node('span', 'dep-label', label), node('b', 'dep-value', String(value ?? '—')));
    backlog.append(row);
  });
  parts.push(backlog);

  const ingest = pipeline.ingest_jobs_by_status || {};
  const ingestText = Object.keys(ingest).length
    ? Object.entries(ingest).map(([status, count]) => `${status} ${count}`).join(' · ')
    : '暂无摄取任务';
  parts.push(node('div', 'pipeline-sub', `摄取任务：${ingestText}`));
  if (pipeline.latest_ingest_job?.error) {
    parts.push(node('p', 'alert-detail', `最近摄取错误：${pipeline.latest_ingest_job.error}`));
  }
  box.replaceChildren(...parts);
}

// ===== 调度器 =====
const WORKER_STATUS_LABEL = {
  ok: '正常', never: '尚未运行', overdue: '逾期', critical: '严重逾期',
  disabled: '已停用', unknown: '未知',
};

function renderWorkers(snapshot) {
  const body = $('#monitorWorkers tbody');
  if (!body) return;
  const scheduler = snapshot.scheduler || {};
  const hint = $('#monitorSchedulerHint');
  if (hint) {
    hint.textContent = scheduler.running
      ? `APScheduler 运行中 · ${scheduler.job_count} 个作业`
      : `APScheduler 未运行${scheduler.configured ? '（配置为启用）' : '（LANTAI_RUN_SCHEDULER=0）'}`;
  }
  const workers = scheduler.workers || [];
  if (!workers.length) {
    body.replaceChildren(emptyRow(6, '暂无受监控的 worker'));
    return;
  }
  body.replaceChildren(...workers.map(worker => {
    const tr = node('tr');
    const status = node('td');
    status.append(node('span', `pill pill-${worker.status}`, WORKER_STATUS_LABEL[worker.status] || worker.status));
    const action = node('td', 'num');
    const button = node('button', 'mini-btn', '立即运行');
    button.disabled = !worker.enabled;
    button.addEventListener('click', () => runWorker(worker.name, button));
    action.append(button);
    tr.append(
      node('td', 'mono', worker.name),
      node('td', '', fmtDuration(worker.period_seconds)),
      node('td', '', fmtClock(worker.last_run)),
      node('td', '', worker.last_run === null ? '从未' : fmtAgo(worker.last_run_age_seconds)),
      status,
      action,
    );
    return tr;
  }));
}

async function runWorker(name, button) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = '运行中…';
  try {
    await api(`/monitor/workers/${name}/run`, {method: 'POST'});
    notify(`worker ${name} 已触发`);
    await loadSnapshot({silent: true});
  } catch (error) {
    notify(`触发 ${name} 失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

// ===== 日志与配置 =====
function renderLogs(items) {
  const box = $('#monitorLogs');
  if (!box) return;
  if (!items.length) {
    box.replaceChildren(node('div', 'inspector-empty', '暂无错误或慢请求 🎉'));
    return;
  }
  box.replaceChildren(...items.map(item => {
    const row = node('div', `log-row${item.status_code >= 500 ? ' is-error' : ''}`);
    const head = node('div', 'log-head');
    head.append(
      node('span', `pill ${item.status_code >= 500 ? 'pill-critical' : 'pill-overdue'}`, String(item.status_code)),
      node('span', 'mono log-endpoint', item.endpoint),
      node('span', 'log-latency', `${fmtNumber(item.latency_ms, 0)} ms`),
    );
    row.append(head, node('div', 'log-meta', `${fmtClock(item.created_at)} · ${item.user_id || 'anonymous'}`));
    return row;
  }));
}

function renderConfig(groups) {
  const box = $('#monitorConfig');
  if (!box) return;
  const parts = [];
  Object.entries(groups || {}).forEach(([group, entries]) => {
    parts.push(node('div', 'pipeline-sub', group));
    Object.entries(entries || {}).forEach(([key, value]) => {
      const row = node('div', 'dep-row');
      const text = typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value);
      row.append(node('span', 'dep-label mono', key), node('b', 'dep-value mono', text));
      parts.push(row);
    });
  });
  box.replaceChildren(...parts);
}

// ===== 加载 =====
async function loadSnapshot({silent = false} = {}) {
  try {
    const [snapshot, series, logs, config] = await Promise.all([
      api('/monitor/overview'),
      api('/monitor/series?minutes=60'),
      api('/monitor/logs?limit=25&only_problems=true'),
      api('/monitor/config'),
    ]);
    lastSnapshot = snapshot;
    renderMetrics(snapshot);
    renderAlerts(snapshot);
    renderDependencies(snapshot);
    renderChart(series.series || []);
    renderEndpoints(snapshot);
    renderPipeline(snapshot);
    renderWorkers(snapshot);
    renderLogs(logs.items || []);
    renderConfig(config.settings || {});
    const subtitle = $('#monitorSubtitle');
    if (subtitle) {
      subtitle.textContent = `更新于 ${new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      }).format(new Date())} · 状态 ${snapshot.summary?.status || '—'} · 版本 ${snapshot.version || '—'}`;
    }
  } catch (error) {
    if (!silent) notify(`监控数据读取失败：${error.message}`);
    const box = $('#monitorAlerts');
    if (box && !silent) {
      box.replaceChildren(node('div', 'alert-card sev-critical', `监控接口不可用：${error.message}`));
    }
  }
}

function startTimer() {
  stopTimer();
  timer = setInterval(() => {
    const auto = $('#monitorAutoRefresh');
    if (document.hidden || (auto && !auto.checked)) return;
    loadSnapshot({silent: true});
  }, REFRESH_MS);
}

function stopTimer() {
  if (timer) clearInterval(timer);
  timer = null;
}

export function initMonitor(options = {}) {
  if (typeof options.notify === 'function') notify = options.notify;
  $('#monitorRefreshBtn')?.addEventListener('click', () => loadSnapshot());
  $('#monitorAutoRefresh')?.addEventListener('change', event => {
    if (event.target.checked) loadSnapshot({silent: true});
  });
}

export function activateMonitorView() {
  loadSnapshot();
  startTimer();
}

export function deactivateMonitor() {
  stopTimer();
}

export function monitorAlertCount() {
  return lastSnapshot?.summary?.alert_count ?? 0;
}

// 侧边栏徽标：轻量口径（quality=false 跳过零召回聚合），首屏与后台轮询共用
export async function refreshMonitorBadge() {
  const badge = document.querySelector('#navMonitorCount');
  if (!badge) return 0;
  try {
    const snapshot = await api('/monitor/overview?quality=false');
    lastSnapshot = snapshot;
    const count = snapshot.summary?.critical_count || snapshot.summary?.alert_count || 0;
    badge.textContent = String(count);
    badge.classList.toggle('tone-bad', Boolean(snapshot.summary?.critical_count));
    return count;
  } catch (error) {
    badge.textContent = '!';
    badge.classList.add('tone-bad');
    return 0;
  }
}
