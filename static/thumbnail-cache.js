(() => {
  'use strict';
  const prefix = 'lanstream-thumbnail:', scopeKey = 'lanstream-thumbnail-scope', limit = 2 * 1024 * 1024;
  const entries = new Map();
  let scope, used = 0, closed = false;
  const readScope = () => { try { return sessionStorage.getItem(scopeKey); } catch (_) { return null; } };
  function remove(key) {
    used -= entries.get(key)?.length || 0; entries.delete(key);
    try { sessionStorage.removeItem(prefix + key); } catch (_) {}
  }
  function clear() {
    entries.clear(); used = 0;
    try {
      for (let i = sessionStorage.length - 1; i >= 0; i--) {
        const key = sessionStorage.key(i);
        if (key.startsWith(prefix)) sessionStorage.removeItem(key);
      }
    } catch (_) {}
  }
  function sync(next) {
    if (next === scope) return false;
    clear(); scope = next;
    try { sessionStorage.setItem(scopeKey, scope); localStorage.setItem(scopeKey, scope); } catch (_) {}
    return true;
  }
  function invalidate() {
    clear(); closed = true;
    document.documentElement.style.visibility = 'hidden';
    location.reload();
  }
  const initial = document.querySelector('meta[name="lanstream-thumbnail-scope"]')?.content || '';
  if (initial && readScope() === initial) {
    scope = initial;
    try {
      for (let i = 0; i < sessionStorage.length; i++) {
        const key = sessionStorage.key(i);
        if (!key.startsWith(prefix)) continue;
        const value = sessionStorage.getItem(key);
        if (value?.startsWith('data:image/jpeg;base64,') && used + value.length <= limit) {
          entries.set(key.slice(prefix.length), value); used += value.length;
        }
      }
    } catch (_) {}
    try { localStorage.setItem(scopeKey, scope); } catch (_) {}
  } else sync(initial);
  window.LANStreamThumbnailCache = {
    sync, forget:remove,
    async load(url, signal) {
      if (closed || signal?.aborted) throw new DOMException('Thumbnail cancelled', 'AbortError');
      if (entries.has(url)) {
        const value = entries.get(url); entries.delete(url); entries.set(url, value); return value;
      }
      const current = scope;
      const response = await fetch(url, {signal, cache:'no-store'});
      if (closed || signal?.aborted || scope !== current) throw new DOMException('Thumbnail cancelled', 'AbortError');
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) invalidate();
        throw Error('Thumbnail unavailable');
      }
      if (response.headers.get('X-LANStream-Thumbnail-Scope') !== current) {
        invalidate(); throw Error('Thumbnail access changed');
      }
      if (!response.headers.get('Content-Type')?.startsWith('image/jpeg')) throw Error('Thumbnail unavailable');
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (closed || signal?.aborted || scope !== current) throw new DOMException('Thumbnail cancelled', 'AbortError');
      let binary = '';
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      const value = 'data:image/jpeg;base64,' + btoa(binary);
      if (scope && response.headers.get('X-LANStream-Thumbnail-Cache') === 'tab-session' && value.length <= limit) {
        remove(url);
        while (used + value.length > limit) remove(entries.keys().next().value);
        entries.set(url, value); used += value.length;
        try { sessionStorage.setItem(prefix + url, value); } catch (_) {}
      }
      return value;
    },
  };
  document.querySelector('a.logout')?.addEventListener('click', () => { clear(); closed = true; });
  window.addEventListener('storage', event => { if (event.key === scopeKey && event.newValue !== scope) invalidate(); });
  window.addEventListener('pagehide', () => { closed = true; document.documentElement.style.visibility = 'hidden'; });
  window.addEventListener('pageshow', event => { if (event.persisted) invalidate(); });
})();
