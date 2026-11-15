(() => {
  const $ = id => document.getElementById(id);
  const csrf = document.querySelector('meta[name="lanstream-csrf"]').content;
  let busy = false, refreshing = false;
  const set = (id, text) => { $(id).textContent = text; };
  const bytes = n => {
    if (!Number.isFinite(n)) return 'Unknown';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let unit = 0;
    while (n >= 1024 && unit < units.length - 1) { n /= 1024; unit++; }
    return `${unit ? n.toFixed(1) : n} ${units[unit]}`;
  };
  function render(data) {
    const t = data.thumbnails || {}, s = data.scheduler || {}, storage = data.storage || {}, update = data.updates || {};
    if (!busy) $('automaticThumbnails').checked = !!t.enabled;
    const states = {
      disabled: ['Automatic preparation is off', 'Enable it to prepare thumbnails when LANStream is idle.'],
      waiting: ['Waiting for an idle opportunity', s.paused ? 'Thumbnail work is paused or playback is active.' : 'Preparation starts after 60 seconds without users, when playback and other thumbnail work allow.'],
      preparing: ['Preparing thumbnails', 'Browsing and playback take priority.'],
      complete: ['Automatic preparation pass complete', 'Indexed libraries will be checked again while LANStream is idle.'],
      error: ['Automatic preparation needs attention', 'The preparation pass could not finish.']
    };
    const [state, reason] = states[t.state] || ['Automatic preparation status unavailable', 'Waiting for a status update.'];
    set('thumbnailStatus', state);
    set('thumbnailReason', t.error ? `${reason} ${t.error}` : reason);
    set('automaticCounts', `Automatic preparation this pass: ${t.checked || 0} checked · ${t.ready || 0} ready · ${t.failed || 0} failed`);
    set('schedulerStatus', `Thumbnail requests: ${s.pending || 0} pending · ${s.active || 0} active · ${s.failed || 0} failed${s.paused ? ' · paused or playback active' : ''}`);
    const index = $('indexingStatus'); index.replaceChildren();
    if (!data.indexing || !data.indexing.length) index.textContent = 'No indexing activity for accessible libraries.';
    else {
      const list = document.createElement('ul');
      data.indexing.forEach((item, i) => {
        const li = document.createElement('li');
        li.textContent = `${item.label || 'Library ' + (i + 1)}: ${item.paused ? 'Paused' : item.running ? 'Indexing' : 'Idle'} · ${item.completed || 0} folders indexed · ${item.pending || 0} pending · ${item.errors || 0} errors`;
        list.appendChild(li);
      });
      index.appendChild(list);
    }
    $('scanStorage').disabled = !!storage.running;
    set('storageStatus', storage.running ? 'Measuring storage…' : storage.error || (!storage.scanned ? 'Storage has not been measured yet.' : storage.partial ? 'Measurement is incomplete; some files could not be counted or the scan reached its limit.' : 'Measurement complete.'));
    const details = $('storageDetails'); details.replaceChildren();
    const totals = $('storageTotals'); totals.replaceChildren();
    $('technicalStorage').hidden = !storage.scanned;
    if (storage.scanned) for (const [title, entries] of [['Cache', storage.cache], ['Application', storage.system]]) {
      const group = document.createElement('div');
      const label = document.createElement('dt'); label.textContent = title; group.appendChild(label);
      const value = document.createElement('dd'); value.textContent = bytes(Object.values(entries || {}).reduce((a, b) => a + b, 0)); group.appendChild(value);
      totals.appendChild(group);
      const heading = document.createElement('h3'); heading.textContent = title; details.appendChild(heading);
      const list = document.createElement('ul');
      for (const [name, value] of Object.entries(entries || {})) { const li = document.createElement('li'); li.textContent = `${name}: ${bytes(value)}`; list.appendChild(li); }
      details.appendChild(list);
    }
    $('checkUpdates').disabled = !!update.running;
    const current = update.current ? update.current.slice(0, 12) : 'Unknown';
    let text = `Installed revision: ${current}. `;
    if (update.running) text += 'Checking…';
    else if (update.error) text += update.error;
    else if (!update.checked) text += 'Not checked yet.';
    else if (update.latest) {
      text += `Latest source revision: ${update.latest.slice(0, 12)}. `;
      text += update.current ? (update.latest.startsWith(update.current) || update.current.startsWith(update.latest) ? 'Revisions match.' : 'Revisions differ.') : 'Comparison unavailable.';
    }
    set('updateStatus', text);
  }
  async function request(options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/api/settings', {...options, signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Request failed.');
      return data;
    } finally { clearTimeout(timer); }
  }
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try { render(await request({cache: 'no-store'})); }
    catch (_) { set('settingsMessage', 'Could not load settings.'); }
    finally { refreshing = false; }
  }
  async function action(data) {
    if (busy) return;
    busy = true;
    try {
      await request({method: 'POST', headers: {'Content-Type': 'application/json', 'X-LANStream-CSRF': csrf}, body: JSON.stringify(data)});
      set('settingsMessage', '');
    } catch (error) { set('settingsMessage', error.name === 'AbortError' ? 'Request timed out.' : error.message); }
    finally { busy = false; await refresh(); }
  }
  $('automaticThumbnails').addEventListener('change', e => action({action: 'enable_thumbnails', enabled: e.target.checked}));
  $('scanStorage').addEventListener('click', () => action({action: 'scan_storage'}));
  $('checkUpdates').addEventListener('click', () => action({action: 'check_updates'}));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  setInterval(() => { if (!document.hidden) refresh(); }, 3000);
  refresh();
})();
