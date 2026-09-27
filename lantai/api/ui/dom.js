// 共享 DOM 工具：所有视图模块从这里取，杜绝 innerHTML 拼接（XSS 防线）

export const $ = selector => document.querySelector(selector);

export function node(tag, className = '', text = '') {
  const value = document.createElement(tag);
  if (className) value.className = className;
  if (text !== '') value.textContent = text;
  return value;
}

// 空态/加载态/错误态：一律走 textContent，杜绝 API/错误文本注入
export function emptyState(container, text, isError = false) {
  const div = node('div', 'inspector-empty', text);
  if (isError) div.style.color = 'var(--bad)';
  container.replaceChildren(div);
}

// 记忆结果卡（Vault 与演练场共用）：全部 DOM 构建，不拼 innerHTML（XSS 防线）
// opts.actions: [{label, title, onClick(card)}] —— 追加在卡尾的轻量操作（如 👍/👎 反馈）
export function renderMemoryCard(m, {rank = 0, score = null, breakdown = [], actions = []} = {}) {
  const card = node('div', 'result-item');
  const meta = node('div', 'meta-row');
  const left = node('span');
  left.append(document.createTextNode(`#${rank} [${m.domain || 'user'}/${m.lane || 'general'}] `));
  const code = node('code', '', m.id || '—');
  code.style.fontSize = '11px';
  code.style.color = 'var(--muted)';
  left.append(code);
  const hasScore = typeof score === 'number';
  const right = node('span', hasScore ? 'score-tag' : '',
    hasScore ? `得分: ${score.toFixed(4)}` : `v${m.version || 1} · ${m.memory_type || 'semantic'}`);
  meta.append(left, right);
  const body = node('div', 'text-body', m.content || m.key || '—');
  const breakdownBox = node('div', 'score-breakdown');
  breakdown.forEach(text => breakdownBox.append(node('span', '', text)));
  card.append(meta, body, breakdownBox);
  if (actions.length) {
    const bar = node('div', 'card-actions');
    actions.forEach(({label, title, onClick}) => {
      const btn = node('button', 'chip-btn', label);
      if (title) btn.title = title;
      btn.addEventListener('click', event => {
        event.stopPropagation();
        onClick(m, btn, card);
      });
      bar.append(btn);
    });
    card.append(bar);
  }
  return card;
}

export function formatDate(value, short = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', short
    ? {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}
    : {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}
  ).format(date);
}

// toast 撤销后的刷新动作由宿主注册（app.js 注入 loadQueue，dom.js 不反向依赖）
let afterUndo = null;
export function setAfterUndo(fn) { afterUndo = fn; }

const toast = document.getElementById('toast');
let toastTimer = null;

export function showToast(message, undo) {
  if (!toast) return;
  clearTimeout(toastTimer);
  toast.replaceChildren(document.createTextNode(message));
  if (undo) {
    const button = node('button', '', '撤销');
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await undo();
        showToast('已撤销');
        if (afterUndo) await afterUndo();
      } catch (error) {
        showToast(`撤销失败：${error.message}`);
      }
    });
    toast.append(button);
  }
  toast.hidden = false;
  toastTimer = setTimeout(() => { toast.hidden = true; }, undo ? 9000 : 4200);
}
