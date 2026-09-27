const SESSION_KEY = 'lantai-api-key-session';
const DEVICE_KEY = 'lantai_api_key';

// 401 统一钩子：api() 抛错前广播，由控制台决定提示/打开连接对话框
export const unauthorizedListeners = new Set();
export function onUnauthorized(listener) {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

export function getApiKey() {
  return sessionStorage.getItem(SESSION_KEY) || localStorage.getItem(DEVICE_KEY) || '';
}

export function saveApiKey(value, remember) {
  const key = String(value || '').trim();
  sessionStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(DEVICE_KEY);
  if (!key) return;
  if (remember) localStorage.setItem(DEVICE_KEY, key);
  else sessionStorage.setItem(SESSION_KEY, key);
}

export function clearApiKey() {
  sessionStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(DEVICE_KEY);
}

// FastAPI 422 的 detail 是 [{loc, msg, type}...]；非 JSON 响应体（网关 HTML）也不能只剩 "HTTP 502"
function describeError(status, statusText, data) {
  const detail = data && data.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map(item => {
      const where = Array.isArray(item.loc) ? item.loc.filter(v => v !== 'body').join('.') : '';
      return where ? `${where}: ${item.msg}` : item.msg;
    }).filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object' && detail.msg) return String(detail.msg);
  return statusText || `HTTP ${status}`;
}

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const key = getApiKey();
  if (key) headers.set('X-API-Key', key);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');

  // 默认 30s 超时；调用方可通过 options.timeout=0 关闭或自定义毫秒数
  const timeoutMs = 'timeout' in options ? options.timeout : 30000;
  const controller = new AbortController();
  const timer = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;
  if (options.signal) {
    options.signal.addEventListener('abort', () => controller.abort(), {once: true});
  }

  let response;
  try {
    response = await fetch(path, {...options, headers, signal: controller.signal});
  } catch (err) {
    if (err.name === 'AbortError') {
      throw Object.assign(new Error('请求超时或已中止'), {status: 0, aborted: true});
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }

  let data = null;
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    try { data = await response.json(); } catch (_) { data = {}; }
  } else {
    // 非 JSON（网关 502 HTML 等）：保留片段供诊断
    const text = await response.text().catch(() => '');
    data = {_raw: text.slice(0, 200)};
  }

  if (!response.ok) {
    const error = new Error(describeError(response.status, response.statusText, data));
    error.status = response.status;
    error.data = data;
    if (response.status === 401) unauthorizedListeners.forEach(listener => {
      try { listener(error); } catch (_) {}
    });
    throw error;
  }
  return data;
}
