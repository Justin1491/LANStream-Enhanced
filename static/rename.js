(() => {
  'use strict';
  const $ = id => document.getElementById(id), dialog = $('renameDialog');
  if (!dialog) return;
  let current, key, afterRename, lastFocus, controller, saving = false;
  const pending = new Map();
  const csrf = document.querySelector('meta[name="lanstream-csrf"]').content;
  window.LANStreamRename = (item, done) => {
    if (saving || dialog.open) return;
    current = item; afterRename = done; lastFocus = document.activeElement;
    key = item.root_id ? JSON.stringify([item.root_id, item.rel]) : item.selection_token;
    const dot = item.name.lastIndexOf('.'), extension = dot > 0 ? item.name.slice(dot) : '';
    $('renameCurrent').textContent = item.name;
    $('renameName').value = extension ? item.name.slice(0, dot) : item.name;
    $('renameExtension').textContent = extension;
    const operation = pending.get(key);
    if (operation) $('renameName').value = operation.data.items[0].name;
    $('renameName').disabled = !!operation;
    $('renameSave').textContent = operation ? 'Check rename' : 'Save name';
    $('renameCancel').textContent = operation ? 'Close' : 'Cancel';
    $('renameStatus').textContent = operation?.result ? 'Renamed. Check rename to refresh the results.'
      : operation ? 'The rename may still be running on the server. Check rename to retry safely.' : '';
    dialog.showModal();
    if (operation) $('renameSave').focus(); else { $('renameName').focus(); $('renameName').select(); }
  };
  async function untilAborted(signal, task) {
    if (signal.aborted) throw signal.reason;
    let aborted;
    const stopped = new Promise((_, reject) => {
      aborted = () => reject(signal.reason);
      signal.addEventListener('abort', aborted, {once:true});
    });
    try {
      return await Promise.race([stopped, task()]);
    } finally { signal.removeEventListener('abort', aborted); }
  }
  function request(url, data, signal) {
    return untilAborted(signal, async () => {
      const options = data
        ? {method:'POST', headers:{'Content-Type':'application/json','X-LANStream-CSRF':csrf}, body:JSON.stringify(data)}
        : {cache:'no-store'};
      const response = await fetch(url, {...options, signal});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw Object.assign(Error(result.error || 'Rename unavailable. Close and refresh to check the file.'),
          {status: response.status});
      }
      if (result.state !== 'complete' && !result.job_id) throw Error('Rename status unavailable. Check rename to retry safely.');
      return result;
    });
  }
  function operationId() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  }
  $('renameCancel').onclick = () => { controller?.abort(); dialog.close(); };
  dialog.addEventListener('cancel', () => controller?.abort());
  dialog.addEventListener('close', () => { if (lastFocus?.isConnected) lastFocus.focus(); });
  $('renameForm').onsubmit = async event => {
    event.preventDefault();
    if (saving) return;
    saving = true; $('renameSave').disabled = $('renameName').disabled = true;
    const attempt = controller = new AbortController();
    const signal = attempt.signal, deadline = setTimeout(() => attempt.abort(), 30000);
    let operation = pending.get(key);
    const fresh = !operation;
    try {
      if (fresh) {
        operation = {data:{action:'rename', items:[{selection_token:current.selection_token, name:$('renameName').value}], operation_id:operationId()}};
        pending.set(key, operation);
      }
      $('renameCancel').textContent = 'Close';
      $('renameStatus').textContent = 'Renaming… Closing this dialog does not stop the server operation.';
      if (!operation.result) {
        let job = operation.job_id
          ? await request('/api/cleanup/jobs/' + encodeURIComponent(operation.job_id), null, signal)
          : await request('/api/cleanup/actions', operation.data, signal);
        operation.job_id = job.job_id;
        while (job.state !== 'complete') {
          await new Promise(resolve => setTimeout(resolve, 250));
          job = await request('/api/cleanup/jobs/' + encodeURIComponent(operation.job_id), null, signal);
        }
        const result = job.results?.[0];
        if (!result) throw Error('Rename status unavailable. Check rename to retry safely.');
        if (result.status !== 'renamed') { pending.delete(key); throw Error(result.error || 'Rename failed. Refresh and try again.'); }
        operation.result = result;
      }
      if (afterRename) {
        $('renameStatus').textContent = 'Renamed. Refreshing results…';
        await untilAborted(signal, () => afterRename(operation.result));
        dialog.close();
      } else {
        const url = new URL(location.href);
        url.pathname = url.pathname.slice(0, url.pathname.lastIndexOf('/') + 1) + encodeURIComponent(operation.result.name);
        location.assign(url.href);
      }
      pending.delete(key);
    } catch (error) {
      if (fresh && !operation?.job_id && [400, 401, 403].includes(error?.status)) pending.delete(key);
      $('renameStatus').textContent = operation?.result ? 'Renamed. Refresh the page to see the new name.'
        : signal.aborted ? 'The rename may still be running on the server. Check rename to retry safely, or close and refresh.'
        : error.message;
    }
    finally {
      clearTimeout(deadline); controller = null; saving = false;
      $('renameSave').disabled = $('renameCancel').disabled = false;
      $('renameName').disabled = pending.has(key);
      $('renameSave').textContent = pending.has(key) ? 'Check rename' : 'Save name';
      $('renameCancel').textContent = pending.has(key) ? 'Close' : 'Cancel';
    }
  };
  document.querySelectorAll('[data-rename-file]').forEach(button => {
    button.onclick = () => window.LANStreamRename({name:button.dataset.name, selection_token:button.dataset.token});
  });
})();
