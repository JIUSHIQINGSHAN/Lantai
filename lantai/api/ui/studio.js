// 认知进化工作室（Studio）：器识人格 / 会话札记 / 沉潜夜梦 —— 从 app.js 拆出
// 新增：多人格切换（/persona/list + /persona/{id}/activate）

import {api} from './api.js';
import {$, showToast} from './dom.js';

export function setStudioTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.studioTab === tab));
  document.querySelectorAll('.studio-tab-panel').forEach(p => p.classList.remove('active'));
  const target = $(`#panel${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
  if (target) target.classList.add('active');
  // 懒加载：切到对应 tab 才拉数据
  if (tab === 'recall') loadRecallReport();
  if (tab === 'prompts') loadPromptsPanel();
}

// ===== 器识人格 =====
let personaList = [];

export async function loadPersona() {
  try {
    const [data, list] = await Promise.all([
      api('/persona'),
      api('/persona/list').catch(() => []),
    ]);
    if (data) fillPersonaForm(data);
    personaList = Array.isArray(list) ? list : [];
    renderPersonaSwitcher(data?.name);
  } catch (err) {
    showToast(`读取人格失败: ${err.message}`);
  }
}

function fillPersonaForm(data) {
  $('#personaName').value = data.name || 'default';
  $('#personaStyle').value = data.linguistic_style || '';
  $('#personaGuidelines').value = data.guidelines || '';
  $('#personaFacts').value = data.epistemic_facts || '';
}

function renderPersonaSwitcher(activeName) {
  const select = $('#personaSwitcher');
  if (!select) return;
  select.replaceChildren();
  if (!personaList.length) {
    select.append(new Option('仅当前人格', ''));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  personaList.forEach(p => {
    const opt = new Option(`${p.name}${p.is_active ? '（当前）' : ''}`, p.id);
    select.append(opt);
  });
  const active = personaList.find(p => p.is_active) || personaList.find(p => p.name === activeName);
  if (active) select.value = active.id;
}

async function switchPersona() {
  const select = $('#personaSwitcher');
  const id = select?.value;
  if (!id) return;
  const target = personaList.find(p => p.id === id);
  if (target) fillPersonaForm(target);  // 预览该人格配置到表单（不激活）
}

async function activatePersona() {
  const select = $('#personaSwitcher');
  const id = select?.value;
  if (!id) return;
  try {
    const data = await api(`/persona/${encodeURIComponent(id)}/activate`, {method: 'POST'});
    fillPersonaForm(data);
    showToast(`👑 已激活人格「${data.name}」，下一次召回与对话即刻生效`);
    await loadPersona();
  } catch (err) {
    showToast(`激活人格失败: ${err.message}`);
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
    await loadPersona();  // 刷新切换器（可能出现新名字）
  } catch (err) {
    showToast(`更新人格失败: ${err.message}`);
  }
}

// ===== 会话札记 =====
export async function loadScratchpad(sessionId) {
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

// ===== 沉潜夜梦 =====
export async function loadConsolidationReport() {
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

export async function triggerConsolidation() {
  // 写操作且不可撤销：确认后再执行（夜梦沉淀会修剪/合并记忆）
  if (!window.confirm('夜梦沉淀会聚类合并并修剪衰减记忆，执行后不可撤销。确认立即沉淀？')) return;
  const btn = $('#triggerConsolidateBtn');
  const quickBtn = $('#quickConsolidateBtn');
  if (btn) { btn.disabled = true; btn.textContent = '🌙 正在沉淀聚类与修剪...'; }
  if (quickBtn) { quickBtn.disabled = true; quickBtn.textContent = '🌙 沉淀中...'; }
  try {
    // 沉淀聚类是 LLM 长任务：单次超时放宽到 5 分钟（api.js 默认 30s 会误报「请求超时」）
    const res = await api('/evolution/consolidate', {method: 'POST', timeout: 300000});
    showToast(`沉潜完成：提纯 ${res.new_memories || 0} 条主记忆，修剪 ${res.pruned_count || 0} 条衰减噪音`);
    await loadConsolidationReport();
  } catch (err) {
    showToast(`沉淀执行失败: ${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🌙 立即触发夜梦沉淀'; }
    if (quickBtn) { quickBtn.disabled = false; quickBtn.textContent = '🌙 立即夜梦沉淀'; }
  }
}

// ===== 召回报告 =====
export async function loadRecallReport() {
  try {
    const r = await api('/retrieval/recall-report');
    $('#rrWindow').textContent = String(r.window_days ?? '—');
    $('#rrReal').textContent = String(r.real ?? '—');
    $('#rrZero').textContent = String(r.zero ?? '—');
    const rate = r.zero_recall_rate;
    $('#rrRate').textContent = (rate === undefined || rate === null) ? '—' : `${(Number(rate) * 100).toFixed(1)}%`;
    renderBreakdown($('#rrByLane'), r.by_lane);
    renderBreakdown($('#rrByIntent'), r.by_intent);
    $('#rrRaw').textContent = JSON.stringify(r, null, 2);
  } catch (err) {
    $('#rrRaw').textContent = `读取召回报告异常: ${err.message}`;
  }
}

function renderBreakdown(box, data) {
  if (!box) return;
  const entries = Object.entries(data || {});
  if (!entries.length) { box.textContent = '窗口内暂无数据'; return; }
  box.replaceChildren(...entries.map(([k, v]) => {
    const row = document.createElement('div');
    row.className = 'digest-row';
    const key = document.createElement('span');
    key.className = 'digest-key';
    key.textContent = k;
    const val = document.createElement('b');
    const total = v?.total ?? 0, zero = v?.zero ?? 0;
    val.textContent = `${zero}/${total} 零召回`;
    row.append(key, val);
    return row;
  }));
}

// ===== 提示词与核心记忆 =====
let promptsLoaded = false;
let promptList = [];
let coreBlocks = [];

async function loadPromptsPanel() {
  if (promptsLoaded) return;  // 面板数据不常变，每会话拉一次即可
  promptsLoaded = true;
  try {
    const [prompts, core] = await Promise.all([
      api('/prompts'),
      api('/core-memory'),
    ]);
    promptList = Array.isArray(prompts) ? prompts : [];
    renderPromptSelect();
    coreBlocks = core?.blocks || [];
    fillCoreBlock($('#coreBlockSelect')?.value || 'identity');
  } catch (err) {
    promptsLoaded = false;  // 失败允许重试
    showToast(`读取提示词面板失败: ${err.message}`);
  }
}

function renderPromptSelect() {
  const select = $('#promptSelect');
  if (!select) return;
  select.replaceChildren();
  if (!promptList.length) {
    select.append(new Option('（暂无覆写，全部为代码默认）', ''));
    return;
  }
  select.append(new Option('—— 选择一条覆写载入编辑 ——', ''));
  promptList.forEach(p => select.append(new Option(`${p.id}${p.description ? '：' + p.description : ''}`, p.id)));
}

async function loadPromptIntoForm(promptId) {
  if (!promptId) return;
  try {
    const data = await api(`/prompts/${encodeURIComponent(promptId)}`);
    $('#promptId').value = promptId;
    $('#promptTemplate').value = data.template || '';
    const hit = promptList.find(p => p.id === promptId);
    $('#promptDesc').value = hit?.description || '';
  } catch (err) {
    showToast(`读取提示词失败: ${err.message}`);
  }
}

async function savePrompt(e) {
  e.preventDefault();
  const id = $('#promptId').value.trim();
  if (!id) return;
  const template = $('#promptTemplate').value;
  const description = $('#promptDesc').value.trim();
  // 后端是 upsert 且无删除端点：空模板会存成「空覆写」遮蔽默认模板，宁 miss 不脏写
  if (!template.trim()) {
    showToast('模板为空不会保存（后端无删除覆写端点，空覆写会遮蔽默认模板）');
    return;
  }
  try {
    await api(`/prompts/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify({template, description}),
    });
    showToast(`🧩 提示词「${id}」覆写已保存`);
    promptsLoaded = false;  // 列表已变，下次重拉
    await loadPromptsPanel();
  } catch (err) {
    showToast(`保存提示词失败: ${err.message}`);
  }
}

function fillCoreBlock(block) {
  const hit = coreBlocks.find(b => b.block === block);
  $('#coreBlockContent').value = hit?.content || '';
  $('#coreBlockMeta').textContent = hit
    ? `v${hit.version} · 更新于 ${(hit.updated_at || '').replace('T', ' ').slice(0, 16)}`
    : '该块尚未写入内容';
}

async function saveCoreBlock(e) {
  e.preventDefault();
  const block = $('#coreBlockSelect').value;
  const content = $('#coreBlockContent').value;
  try {
    const params = new URLSearchParams({block, content});
    await api(`/core-memory?${params}`, {method: 'PUT'});
    showToast(`🧠 核心记忆块「${block}」已保存`);
    promptsLoaded = false;
    await loadPromptsPanel();
  } catch (err) {
    showToast(`保存核心记忆失败: ${err.message}`);
  }
}

// ===== 绑定 =====
export function initStudio() {
  $('#personaForm')?.addEventListener('submit', savePersona);
  $('#personaSwitcher')?.addEventListener('change', switchPersona);
  $('#personaActivateBtn')?.addEventListener('click', activatePersona);
  $('#scratchpadForm')?.addEventListener('submit', saveScratchpad);
  $('#scratchpadSession')?.addEventListener('change', e => loadScratchpad(e.target.value.trim() || 'default'));
  $('#scratchpadContent')?.addEventListener('input', e => {
    $('#scratchpadCount').textContent = `${e.target.value.length} / 1000 字符`;
  });
  $('#triggerConsolidateBtn')?.addEventListener('click', triggerConsolidation);
  $('#rrRefreshBtn')?.addEventListener('click', loadRecallReport);
  $('#promptSelect')?.addEventListener('change', e => loadPromptIntoForm(e.target.value));
  $('#promptForm')?.addEventListener('submit', savePrompt);
  $('#promptLoadDefaultBtn')?.addEventListener('click', async () => {
    const id = $('#promptId').value.trim();
    if (!id) { showToast('先填写提示词 ID'); return; }
    try {
      const data = await api(`/prompts/${encodeURIComponent(id)}`);
      $('#promptTemplate').value = data.template || '';
      showToast('已载入当前生效模板（代码默认或既有覆写），编辑后保存才落库');
    } catch (err) {
      showToast(`读取默认模板失败: ${err.message}`);
    }
  });
  $('#coreBlockSelect')?.addEventListener('change', e => fillCoreBlock(e.target.value));
  $('#coreMemoryForm')?.addEventListener('submit', saveCoreBlock);
  document.querySelectorAll('[data-studio-tab]').forEach(btn => {
    btn.addEventListener('click', () => setStudioTab(btn.dataset.studioTab));
  });
}
