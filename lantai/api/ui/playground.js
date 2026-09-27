// 四路检索与探针演练场（Playground）：从 app.js 拆出
// 新增：结果卡 👍/👎 检索反馈（POST /feedback）

import {api} from './api.js';
import {$, emptyState, renderMemoryCard, showToast} from './dom.js';

async function sendFeedback(m, helped, query, btn, card) {
  const memoryId = m.id;
  if (!memoryId) return;
  try {
    await api('/feedback', {
      method: 'POST',
      body: JSON.stringify({
        memory_id: memoryId,
        query: query || '',
        helped,
        user_accepted: helped,
      }),
    });
    // 同一张卡只保留一次反馈，防连点
    card.querySelectorAll('.card-actions button').forEach(b => { b.disabled = true; });
    btn.textContent = helped ? '已记 👍' : '已记 👎';
    showToast(helped ? '已记录：这条记忆有帮助' : '已记录：这条记忆无帮助');
  } catch (err) {
    showToast(`反馈失败: ${err.message}`);
  }
}

export async function runPlaygroundSearch() {
  const query = $('#playgroundQuery').value.trim();
  if (!query) {
    showToast('请输入检索测试文本');
    return;
  }
  const domain = $('#playgroundDomain').value;
  const force = Boolean($('#playgroundForce')?.checked);
  const graphMode = Boolean($('#playgroundGraphExpand')?.checked);
  const maxHops = Math.min(3, Math.max(1, Number($('#playgroundMaxHops')?.value) || 2));
  const resultsBox = $('#playgroundResults');
  const searchBtn = $('#playgroundSearchBtn');

  searchBtn.disabled = true;
  searchBtn.textContent = graphMode ? '🔗 图增强检索中...' : '🔍 检索中...';
  emptyState(resultsBox, graphMode
    ? '正在进行混合初筛 + 拓扑二度联想（贯珠）...'
    : '正在进行四路检索与拓扑探针检测...');

  try {
    const domainParam = domain === 'all' ? undefined : domain;

    if (graphMode) {
      // 贯珠（ADR-0035）：图增强混合检索，返回 primary_results + associated_memories
      const res = await api('/search/graph_expand', {
        method: 'POST',
        body: JSON.stringify({query, top_k: 6, max_hops: maxHops, min_edge_conf: 0.5, domain: domainParam}),
      });
      $('#probingAlertBox').hidden = true;  // 图增强模式不走探针
      renderGraphExpand(resultsBox, res);
      return;
    }

    const payload = {
      query,
      top_k: 6,
      force,
      domain: domainParam,
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
      emptyState(resultsBox, `零召回 ${gateMsg}`);
      return;
    }

    resultsBox.replaceChildren();
    items.forEach((resItem, idx) => {
      const m = resItem.memory || resItem;
      const score = typeof resItem.score === 'number' ? resItem.score : (m.score || 1.0);
      const decay = typeof m.decay_score === 'number' ? m.decay_score.toFixed(2) : '1.00';
      const conf = typeof m.confidence === 'number' ? m.confidence.toFixed(2) : '0.90';
      const imp = typeof m.importance === 'number' ? m.importance.toFixed(2) : '0.80';
      resultsBox.appendChild(renderMemoryCard(m, {
        rank: idx + 1,
        score,
        breakdown: [
          `类型: ${m.memory_type || 'semantic'}`,
          `时效衰减: ${decay}`,
          `置信度: ${conf}`,
          `重要性: ${imp}`,
          `版本: v${m.version || 1}`,
        ],
        actions: [
          {label: '👍 有用', title: '反馈：这条记忆对本查询有帮助', onClick: (mm, btn, card) => sendFeedback(mm, true, query, btn, card)},
          {label: '👎 无用', title: '反馈：这条记忆对本查询无帮助', onClick: (mm, btn, card) => sendFeedback(mm, false, query, btn, card)},
        ],
      }));
    });
  } catch (err) {
    emptyState(resultsBox, `检索异常: ${err.message}`, true);
  } finally {
    searchBtn.disabled = false;
    searchBtn.textContent = graphMode ? '🔗 图增强检索' : '🔍 检索测试';
  }
}

// 贯珠结果：主命中卡片 + 图谱联想列表（hop / relation / edge_confidence 如实展示）
function renderGraphExpand(box, res) {
  const primary = res?.primary_results || [];
  const associated = res?.associated_memories || [];
  box.replaceChildren();

  const h1 = document.createElement('h4');
  h1.textContent = `主命中（${primary.length}）`;
  box.append(h1);
  if (!primary.length) {
    box.append(emptyNode('混合初筛零命中——无种子可展开'));
  } else {
    primary.forEach((item, idx) => {
      const m = item.memory || item;
      const score = typeof item.score === 'number' ? item.score : null;
      box.appendChild(renderMemoryCard(m, {rank: idx + 1, score}));
    });
  }

  const h2 = document.createElement('h4');
  h2.style.marginTop = '16px';
  h2.textContent = `图谱联想（${associated.length}）`;
  box.append(h2);
  if (!associated.length) {
    box.append(emptyNode('无二度联想记忆（种子无出边或边置信度低于 0.5）'));
    return;
  }
  associated.forEach(a => {
    const row = document.createElement('div');
    row.className = 'limbo-item';
    const body = document.createElement('div');
    body.className = 'limbo-item-body';
    const text = document.createElement('div');
    text.className = 'limbo-item-text';
    text.textContent = a.content || '—';
    const meta = document.createElement('div');
    meta.className = 'limbo-item-meta';
    meta.append(
      metaSpan(`${a.domain || 'user'}/${a.lane || 'general'}`),
      metaSpan(`第 ${a.hop} 跳`),
      metaSpan(`关系: ${a.relation || '—'}`),
      metaSpan(`边置信度: ${typeof a.edge_confidence === 'number' ? a.edge_confidence.toFixed(2) : '—'}`),
    );
    const code = document.createElement('code');
    code.textContent = a.memory_id || '';
    meta.append(code);
    body.append(text, meta);
    row.append(body);
    box.append(row);
  });
}

function emptyNode(text) {
  const div = document.createElement('div');
  div.className = 'inspector-empty';
  div.textContent = text;
  return div;
}

function metaSpan(text) {
  const s = document.createElement('span');
  s.textContent = text;
  return s;
}

export function initPlayground() {
  $('#playgroundSearchBtn')?.addEventListener('click', runPlaygroundSearch);
  $('#playgroundQuery')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); runPlaygroundSearch(); }
  });
  // 贯珠开关：按钮文案同步，提示当前检索模式
  $('#playgroundGraphExpand')?.addEventListener('change', e => {
    const btn = $('#playgroundSearchBtn');
    if (btn && !btn.disabled) btn.textContent = e.target.checked ? '🔗 图增强检索' : '🔍 检索测试';
  });
  // 快捷词点击
  document.querySelectorAll('.chip-btn[data-query]').forEach(btn => {
    btn.addEventListener('click', () => {
      const q = btn.dataset.query;
      if (q) {
        $('#playgroundQuery').value = q;
        runPlaygroundSearch();
      }
    });
  });
}
