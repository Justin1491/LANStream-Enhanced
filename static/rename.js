(() => {
  'use strict';
  const $ = id => document.getElementById(id), dialog = $('renameDialog');
  if (!dialog) return;
  let current, afterRename, lastFocus, saving = false;
  const csrf = document.querySelector('meta[name="lanstream-csrf"]').content;
  window.LANStreamRename = (item, done) => {
    if (saving || dialog.open) return;
    current = item; afterRename = done; lastFocus = document.activeElement;
    const dot = item.name.lastIndexOf('.'), extension = dot > 0 ? item.name.slice(dot) : '';
    $('renameCurrent').textContent = item.name;
    $('renameName').value = extension ? item.name.slice(0, dot) : item.name;
    $('renameExtension').textContent = extension;
    $('renameStatus').textContent = '';
    dialog.showModal(); $('renameName').focus(); $('renameName').select();
  };
  async function request(url, data) {
    const response = await fetch(url, data ? {method:'POST', headers:{'Content-Type':'application/json','X-LANStream-CSRF':csrf}, body:JSON.stringify(data)} : {cache:'no-store'});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(result.error || 'Rename unavailable. Refresh and try again.');
    return result;
  }
  function operationId() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  }
  $('renameCancel').onclick = () => dialog.close();
  dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
  dialog.addEventListener('close', () => { if (lastFocus?.isConnected) lastFocus.focus(); });
  $('renameForm').onsubmit = async event => {
    event.preventDefault();
    if (saving) return;
    saving = true; $('renameSave').disabled = $('renameCancel').disabled = $('renameName').disabled = true;
    $('renameStatus').textContent = 'Renaming…';
    try {
      let job = await request('/api/cleanup/actions', {action:'rename', items:[{selection_token:current.selection_token, name:$('renameName').value}], operation_id:operationId()});
      while (job.state !== 'complete') {
        await new Promise(resolve => setTimeout(resolve, 250));
        job = await request('/api/cleanup/jobs/' + encodeURIComponent(job.job_id));
      }
      const result = job.results[0];
      if (result.status !== 'renamed') throw Error(result.error || 'Rename failed. Refresh and try again.');
      if (afterRename) { await afterRename(result); dialog.close(); }
      else {
        const url = new URL(location.href);
        url.pathname = url.pathname.slice(0, url.pathname.lastIndexOf('/') + 1) + encodeURIComponent(result.name);
        location.assign(url.href);
      }
    } catch (error) { $('renameStatus').textContent = error.message; }
    finally { saving = false; $('renameSave').disabled = $('renameCancel').disabled = $('renameName').disabled = false; }
  };
  document.querySelectorAll('[data-rename-file]').forEach(button => {
    button.onclick = () => window.LANStreamRename({name:button.dataset.name, selection_token:button.dataset.token});
  });
})();
