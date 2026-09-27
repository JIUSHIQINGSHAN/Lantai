// 档案星图工作区（Vault）：从 app.js 拆出的独立视图模块（模式同 terminal.js / monitor.js）

import {api} from './api.js';
import {$, emptyState, formatDate, node, renderMemoryCard, showToast} from './dom.js';

// 同筛选条件不重复全库检索（切视图/轮询不再反复 force 搜索）
const vaultCache = {key: null, items: null};

export async function loadVault() {
  const q = $('#vaultSearchInput')?.value?.trim() || '';
  const domain = $('#vaultDomainFilter')?.value || '';
  const list = $('#vaultList');
  if (!list) return;
  const cacheKey = `${q}|${domain}`;
  if (vaultCache.items && vaultCache.key === cacheKey) {
    renderVaultItems(list, vaultCache.items);
    return;
  }
  emptyState(list, '正在加载档案库记忆...');

  try {
    const res = await api('/search', {
      method: 'POST',
      // 空查询的默认浏览不强制绕过缓存；用户显式输入关键词时才 force
      body: JSON.stringify({query: q || '记忆', top_k: 20, force: Boolean(q), domain: domain || undefined}),
    });
    const items = res.results || res.memories || [];
    vaultCache.key = cacheKey;
    vaultCache.items = items;
    renderVaultItems(list, items);
  } catch (err) {
    emptyState(list, `读取档案失败: ${err.message}`, true);
  }
}

function renderVaultItems(list, items) {
  if (!items.length) {
    emptyState(list, '档案库暂无匹配记录');
    return;
  }

  list.replaceChildren();
  items.forEach((resItem, idx) => {
    const m = resItem.memory || resItem;
    list.appendChild(renderMemoryCard(m, {
      rank: idx + 1,
      breakdown: [
        `时效衰减: ${(m.decay_score ?? 1.0).toFixed(2)}`,
        `置信度: ${(m.confidence ?? 0.9).toFixed(2)}`,
        `重要性: ${(m.importance ?? 0.8).toFixed(2)}`,
        `创建: ${formatDate(m.created_at, true)}`,
      ],
    }));
  });
}

// 记忆被修改/回滚后调用：下次进入档案页强制重新检索
export function invalidateVaultCache() {
  vaultCache.key = null;
  vaultCache.items = null;
}

// ===== 故纸堆（笔削可逆侧：已归档 / 已撤回 / 已巩固） =====
const LIMBO_STATUS_LABELS = {archived: '已归档', retracted: '已撤回', consolidated: '已巩固'};

export async function loadLimbo() {
  const list = $('#limboList');
  if (!list) return;
  const status = $('#limboStatusFilter')?.value || 'archived';
  emptyState(list, `正在读取${LIMBO_STATUS_LABELS[status] || status}记忆...`);
  try {
    const res = await api(`/memories?status=${encodeURIComponent(status)}&limit=50`);
    // 空列表是合法返回，不能用 `||` 级联（[] 会跳到 items 分支）
    const items = ('memories' in res ? res.memories : res.items) || [];
    renderLimboItems(list, items, status);
  } catch (err) {
    emptyState(list, `读取失败: ${err.message}`, true);
  }
}

function renderLimboItems(list, items, status) {
  if (!items.length) {
    emptyState(list, `故纸堆里没有${LIMBO_STATUS_LABELS[status] || status}的记忆`);
    return;
  }
  list.replaceChildren();
  items.forEach(m => {
    const row = node('div', 'limbo-item');
    const body = node('div', 'limbo-item-body');
    body.append(node('div', 'limbo-item-text', m.content || '—'));
    const meta = node('div', 'limbo-item-meta');
    meta.append(
      node('span', '', `${m.domain || 'user'}/${m.lane || 'general'}`),
      node('span', '', `v${m.version || 1}`),
      node('span', '', formatDate(m.updated_at, true)),
    );
    const code = node('code', '', m.id || '');
    meta.append(code);
    body.append(meta);
    const restoreBtn = node('button', 'secondary-button', restoreLabel(status));
    restoreBtn.title = restoreTitle(status);
    restoreBtn.addEventListener('click', () => restoreLimbo(m.id, status, restoreBtn));
    row.append(body, restoreBtn);
    list.append(row);
  });
}

function restoreLabel(status) {
  if (status === 'archived') return '♻️ 恢复归档';
  if (status === 'retracted') return '↩️ 撤销撤回';
  return '🌱 起复';
}

function restoreTitle(status) {
  if (status === 'archived') return 'unarchive：记忆回到常规检索';
  if (status === 'retracted') return 'unretract：仅管理员可用，恢复被撤回的主张';
  return 'revive-consolidated：碎片恢复 active（原因必填留痕）';
}

async function restoreLimbo(memoryId, status, btn) {
  if (!memoryId) return;
  let path, payload;
  if (status === 'archived') {
    if (!window.confirm('恢复归档后该记忆立即回到常规检索。确认？')) return;
    path = `/terminal/memory/${memoryId}/unarchive`;
    payload = {};
  } else if (status === 'retracted') {
    if (!window.confirm('撤销撤回将恢复该主张的全部检索面（需管理员权限）。确认？')) return;
    path = `/terminal/memory/${memoryId}/unretract`;
    payload = undefined;  // 路由不收 body
  } else {
    const reason = prompt('起复原因（必填，恢复须留痕）：', '');
    if (reason === null) return;
    if (!reason.trim()) { showToast('起复必须填写原因'); return; }
    path = `/terminal/memory/${memoryId}/revive-consolidated`;
    payload = {reason: reason.trim()};
  }
  btn.disabled = true;
  try {
    const options = {method: 'POST'};
    if (payload !== undefined) options.body = JSON.stringify(payload);
    const res = await api(path, options);
    const note = res.already_active || res.already_revoked ? '（幂等：已是目标状态）' : '';
    showToast(`✅ 已恢复${note}`);
    invalidateVaultCache();
    await loadLimbo();
  } catch (err) {
    btn.disabled = false;
    showToast(err.status === 403 ? '恢复失败：撤销撤回需管理员权限' : `恢复失败: ${err.message}`);
  }
}

// ===== 目录树（/tree：整树只读 + 新增节点） =====
export async function loadTree() {
  const box = $('#treeView');
  if (!box) return;
  box.textContent = '正在读取目录树...';
  try {
    const data = await api('/tree');
    renderTree(box, data);
  } catch (err) {
    // 目录树在 FEATURE_WIKI 开关下（默认关）：404 如实降级，不伪装成空树
    box.textContent = err.status === 404
      ? '目录树功能未启用（服务端 FEATURE_WIKI 已关闭）'
      : `读取失败: ${err.message}`;
  }
}

function renderTree(box, data) {
  const nodes = data?.nodes || [];
  if (!nodes.length) {
    box.textContent = '目录树为空——用下方表单新建根节点（如「生活」「工作」）';
    return;
  }
  // id → children 映射，从根递归渲染（后端按 depth 排序，递归才能保证层级正确）
  const byId = new Map(nodes.map(n => [n.id, n]));
  const children = new Map();
  nodes.forEach(n => {
    const parent = n.parent_id && byId.has(n.parent_id) ? n.parent_id : null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(n);
  });
  const root = node('div', 'tree-root');
  const walk = (parentId, depth) => {
    (children.get(parentId) || []).forEach(n => {
      const row = node('div', 'tree-node');
      row.style.paddingLeft = `${depth * 16}px`;
      const label = node('span', 'tree-node-name', n.name || n.node_path);
      row.append(label);
      const att = n.attachments || {};
      const meta = node('span', 'tree-node-meta');
      meta.append(
        node('code', '', n.node_path || ''),
        node('span', '', `直挂 ${att.direct ?? 0} · 子树 ${att.subtree ?? 0}`),
      );
      if (n.description) meta.append(node('span', 'tree-node-desc', n.description));
      row.append(meta);
      root.append(row);
      walk(n.id, depth + 1);
    });
  };
  // 根节点（parent_id 为空或指向不存在的父）
  walk(null, 0);
  box.replaceChildren(root);
}

async function addTreeNode(e) {
  e.preventDefault();
  const name = $('#treeNodeName').value.trim();
  if (!name) return;
  const parent_path = $('#treeNodeParent').value.trim() || '/';
  const description = $('#treeNodeDesc').value.trim();
  try {
    await api('/tree/nodes', {
      method: 'POST',
      body: JSON.stringify({name, parent_path, description}),
    });
    showToast(`🌳 节点「${name}」已创建`);
    $('#treeNodeName').value = '';
    $('#treeNodeDesc').value = '';
    await loadTree();
  } catch (err) {
    // 重名/父缺失/非法名是 422；404 = 功能未启用——均如实转达，宁 miss 不脏写
    showToast(err.status === 404 ? '目录树功能未启用（FEATURE_WIKI 已关闭）' : `新增节点失败: ${err.message}`);
  }
}

// ===== 导入与检查点 =====
async function runImport(e) {
  e.preventDefault();
  const text = $('#importText').value;
  if (!text.trim()) { showToast('JSONL 文本为空'); return; }
  if (!window.confirm('导入将 verbatim 直存全部合法行（保留原始时间戳）。确认导入？')) return;
  const box = $('#importResult');
  try {
    // 批量导入可能较慢：单次超时放宽到 2 分钟
    const res = await api('/import/jsonl', {method: 'POST', body: JSON.stringify({text}), timeout: 120000});
    box.hidden = false;
    box.textContent = JSON.stringify(res, null, 2);
    const summary = `导入完成：${res.imported ?? 0} 条入库，${res.duplicates ?? 0} 条重复，${(res.errors || []).length} 条失败`;
    showToast(summary);
    invalidateVaultCache();
  } catch (err) {
    box.hidden = false;
    box.textContent = `导入失败: ${err.message}`;
    showToast(`导入失败: ${err.message}`);
  }
}

async function loadLatestCheckpoint() {
  const box = $('#checkpointView');
  if (!box) return;
  box.textContent = '正在读取...';
  try {
    const cp = await api('/checkpoint/latest');
    if (!cp) {
      box.textContent = '暂无会话检查点';
      return;
    }
    box.textContent = JSON.stringify(cp, null, 2);
  } catch (err) {
    box.textContent = `读取失败: ${err.message}`;
  }
}

export function initVault() {
  $('#vaultSearchBtn')?.addEventListener('click', () => loadVault());
  $('#vaultSearchInput')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); loadVault(); }
  });
  $('#vaultDomainFilter')?.addEventListener('change', () => loadVault());
  $('#limboRefreshBtn')?.addEventListener('click', () => loadLimbo());
  $('#limboStatusFilter')?.addEventListener('change', () => loadLimbo());
  $('#treeRefreshBtn')?.addEventListener('click', () => loadTree());
  $('#treeNodeForm')?.addEventListener('submit', addTreeNode);
  $('#importForm')?.addEventListener('submit', runImport);
  $('#checkpointRefreshBtn')?.addEventListener('click', loadLatestCheckpoint);
}
