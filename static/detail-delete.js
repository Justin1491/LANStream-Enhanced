(() => {
  'use strict';
  const $ = id => document.getElementById(id), button = $('deleteFile'), dialog = $('deleteDialog');
  if (!button || !dialog) return;
  const csrf = document.querySelector('meta[name="lanstream-csrf"]').content;
  let busy = false, confirmation;
  function operationId() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0,8),hex.slice(8,12),hex.slice(12,16),hex.slice(16,20),hex.slice(20)].join('-');
  }
  function controls(working) {
    busy = working; button.disabled = $('deleteCancel').disabled = working;
    $('deleteConfirm').disabled = working || !confirmation;
  }
  async function request(url, data) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(url, data ? {method:'POST',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-LANStream-CSRF':csrf},body:JSON.stringify(data)} : {cache:'no-store',signal:controller.signal});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw Error(result.error || 'Action unavailable. Refresh and try again.');
      return result;
    } finally { clearTimeout(timer); }
  }
  async function submit(action, items) {
    let job = await request('/api/cleanup/actions', {action,items,operation_id:operationId()});
    if (action === 'move') try { sessionStorage.setItem('lanstream-cleanup-job',job.job_id); } catch (_) {}
    while (job.state !== 'complete') {
      await new Promise(resolve => setTimeout(resolve, 500));
      job = await request('/api/cleanup/jobs/' + encodeURIComponent(job.job_id));
    }
    if (action === 'move') try { sessionStorage.removeItem('lanstream-cleanup-job'); } catch (_) {}
    return job.results[0];
  }
  button.onclick = async () => {
    if (busy || dialog.open) return;
    confirmation = null; controls(true); $('deleteStatus').textContent = 'Checking file…';
    try {
      const result = await submit('preview_move',[{selection_token:button.dataset.token}]);
      if (result.status !== 'ready') throw Error(result.error || 'File is not ready. Refresh and try again.');
      confirmation = result.confirmation_token;
      $('deleteName').textContent = button.dataset.name;
      $('deleteStatus').textContent = $('deleteDialogStatus').textContent = '';
      controls(false);
      dialog.showModal(); $('deleteCancel').focus();
    } catch (error) { $('deleteStatus').textContent = error.message; }
    finally { controls(false); }
  };
  $('deleteCancel').onclick = () => { if (!busy) dialog.close(); };
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { confirmation = null; button.focus(); });
  $('deleteConfirm').onclick = async () => {
    if (busy || !confirmation || !dialog.open) return;
    const token = confirmation; confirmation = null; controls(true);
    $('deleteDialogStatus').textContent = 'Moving to Trash… A slow share may take a while.';
    try {
      const result = await submit('move',[{confirmation_token:token}]);
      if (result.status !== 'moved') throw Error(result.error || 'Could not move the file to Trash.');
      location.assign(button.dataset.folder);
    } catch (error) {
      $('deleteDialogStatus').textContent = error.message + ' Check Library Cleanup before retrying. Close this dialog and refresh to check the file again.';
    } finally { controls(false); }
  };
})();
