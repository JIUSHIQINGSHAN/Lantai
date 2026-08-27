const SESSION_KEY = 'lantai-api-key-session';
const DEVICE_KEY = 'lantai_api_key';

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

export async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const key = getApiKey();
  if (key) headers.set('X-API-Key', key);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetch(path, {...options, headers});
  let data = null;
  try { data = await response.json(); } catch (_) { data = {}; }
  if (!response.ok) {
    const error = new Error(String(data.detail || `HTTP ${response.status}`));
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

