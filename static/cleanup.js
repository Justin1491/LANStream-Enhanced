(() => {
  'use strict';
  const $ = id => document.getElementById(id), root = $('cleanup');
  if (!root) return;
  const csrf = document.querySelector('meta[name="lanstream-csrf"]').content;
  const params = new URLSearchParams(location.search);
  let state = {q:params.get('q') || '', scope:params.get('scope') || 'all', root:params.get('root') || '', relative:params.get('relative') || '', sort:params.get('sort') || 'name', direction:params.get('direction') || 'asc', file_type:params.get('file_type') || 'all', matching_sizes:params.get('matching_sizes') || '0', page:Number(params.get('page')) || 1, tab:params.get('tab') === 'trash' ? 'trash' : 'library'};
  let items = [], selected = new Map(), sequence = 0, controller, debounce, durationPoll, stopped = false, working = false, loading = false, writes = root.dataset.writes === 'true';
  let thumbController, observer, objectURLs = [], pendingConfirmation, lastFocus, pickerRelative = '', pickerPage = 1, durationRunning = false;
  function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
  function bytes(value) { if (value == null) return '—'; if (value < 1024) return value + ' B'; const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024))); return (value / 1024 ** unit).toFixed(2) + ' ' + ['B','KiB','MiB','GiB','TiB'][unit]; }
  function duration(value) { if (value == null || !Number.isFinite(value)) return '—'; const total = Math.floor(value); return [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60].map(n => String(n).padStart(2, '0')).join(':'); }
  function key(item) { return item.item_id || item.root_id + ':' + item.rel; }
  function savePreference(value) { try { localStorage.setItem('lanstream-cleanup-thumbnails', String(value)); } catch (_) {} }
  try { $('cleanupThumbnails').checked = localStorage.getItem('lanstream-cleanup-thumbnails') === 'true'; } catch (_) {}
  const options = [...$('cleanupRoot').options];
  if (state.root && !state.root.startsWith('root_')) state.root = options.find(option => option.dataset.index === state.root)?.value || '';
  if (state.tab === 'library' && state.matching_sizes === '1') state.sort = 'size';
  $('cleanupQuery').value = state.q; $('cleanupRoot').value = state.root; $('cleanupScope').value = state.scope;
  $('cleanupType').value = state.file_type; $('matchingSizes').checked = state.matching_sizes === '1';
  function scopeParams() { return new URLSearchParams({scope:state.scope, root:state.root, relative:state.relative}); }
  async function request(url, data, signal) {
    const response = await fetch(url, data === undefined ? {cache:'no-store',signal} : {method:'POST',headers:{'Content-Type':'application/json','X-LANStream-CSRF':csrf},body:JSON.stringify(data),signal});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(result.error || (response.status === 401 ? 'Sign in again to continue.' : 'Action unavailable. Check access and refresh the results.'));
    return result;
  }
  function cancelThumbnails() {
    if (thumbController) thumbController.abort();
    if (observer) observer.disconnect();
    objectURLs.forEach(url => URL.revokeObjectURL(url)); objectURLs = [];
    thumbController = new AbortController();
  }
  function selectionChanged() {
    const count = selected.size, total = [...selected.values()].reduce((sum, item) => sum + item.size, 0);
    $('cleanupSelected').parentElement?.classList.toggle('has-selection', count > 0);
    $('cleanupSelected').textContent = count + ' file' + (count === 1 ? '' : 's') + ' selected' + (count ? ' · ' + bytes(total) : '');
    $('moveCleanup').disabled = $('restoreCleanup').disabled = $('purgeCleanup').disabled = !count || !writes || working || loading;
    $('selectCleanupPage').disabled = !writes || working || loading;
    document.querySelectorAll('#cleanupRows input[type=checkbox]').forEach(input => { input.disabled = !writes || working || loading; });
    $('selectCleanupPage').checked = !!items.length && count === items.length;
    $('selectCleanupPage').indeterminate = count > 0 && count < items.length;
  }
  function clearSelection() { selected.clear(); selectionChanged(); }
  function controls() {
    const trash = state.tab === 'trash';
    $('libraryTab').setAttribute('aria-pressed', !trash); $('trashTab').setAttribute('aria-pressed', trash);
    for (const id of ['matchingLabel','thumbnailsLabel','cleanupTypeLabel','cleanupTypeHeading','durationControls']) $(id).hidden = trash;
    $('trashRecovery').hidden = !trash; $('trashStateHeading').hidden = !trash;
    $('moveCleanup').hidden = trash; $('restoreCleanup').hidden = $('purgeCleanup').hidden = !trash;
    $('previewHeading').hidden = trash || !$('cleanupThumbnails').checked;
    $('cleanupDateSort').dataset.sort = trash ? 'trashed_at' : 'date'; $('cleanupDateSort').textContent = trash ? 'Trashed' : 'Modified';
    $('cleanupHint').textContent = trash ? 'Restore returns files to their original folders. Existing files are never overwritten.' : 'Same size, duration, or thumbnail is a comparison clue, not proof of identical contents.';
    $('chooseFolder').disabled = !state.root || working;
    $('cleanupPrepareThumbnails').disabled = !state.root || state.scope !== 'folder';
    $('recoverTrash').disabled = !writes || !state.root || working;
    $('cleanupPath').textContent = state.root ? ($('cleanupRoot').options[$('cleanupRoot').selectedIndex]?.textContent || 'Library') + (state.relative ? ' / ' + state.relative : '') : 'All accessible libraries';
    document.querySelectorAll('[data-sort]').forEach(button => {
      const active = button.dataset.sort === state.sort;
      button.disabled = !trash && state.matching_sizes === '1' && button.dataset.sort !== 'size';
      button.parentElement.setAttribute('aria-sort', active ? state.direction === 'desc' ? 'descending' : 'ascending' : 'none');
      button.setAttribute('aria-label', button.textContent + (active ? state.direction === 'desc' ? ', descending' : ', ascending' : ', sort ascending'));
    });
    selectionChanged();
  }
  function render(result) {
    items = result.items; selected.clear(); writes = result.writes_enabled === true; state.page = result.page;
    cancelThumbnails(); controls();
    const fragment = document.createDocumentFragment(), signal = thumbController.signal, queue = [];
    let active = 0, lastSize;
    async function drain() {
      while (!signal.aborted && active < 2 && queue.length) {
        const {item, cell} = queue.shift(); active++;
        (async () => {
          try {
            const response = await fetch(item.thumb_url, {signal,cache:'no-store'});
            if (!response.ok) return;
            const blob = await response.blob(); if (signal.aborted) return;
            const url = URL.createObjectURL(blob); objectURLs.push(url);
            const image = element('img', undefined, 'cleanup-preview'); image.alt = 'Thumbnail of ' + item.name; image.src = url; cell.replaceChildren(image);
          } catch (_) { /* Missing cached thumbnails remain placeholders. */ }
          finally { active--; drain(); }
        })();
      }
    }
    observer = new IntersectionObserver(entries => entries.forEach(entry => { if (entry.isIntersecting) { observer.unobserve(entry.target); queue.push({item:entry.target.cleanupItem,cell:entry.target}); drain(); } }), {rootMargin:'100px'});
    const trash = state.tab === 'trash', thumbnails = !trash && $('cleanupThumbnails').checked;
    for (const item of items) {
      if (!trash && state.matching_sizes === '1' && lastSize !== item.size) {
        const group = element('tr', undefined, 'size-group'), cell = element('td', bytes(item.size) + ' · ' + item.size.toLocaleString() + ' bytes · ' + item.size_count + ' matching paths');
        cell.colSpan = thumbnails ? 8 : 7; group.append(cell); fragment.append(group); lastSize = item.size;
      }
      const row = element('tr'), pick = element('input'); pick.type = 'checkbox'; pick.checked = selected.has(key(item)); pick.disabled = working || !writes;
      pick.setAttribute('aria-label', 'Select ' + item.name);
      pick.onchange = () => { if (loading || working) return; if (pick.checked) selected.set(key(item), item); else selected.delete(key(item)); row.classList.toggle('selected', pick.checked); selectionChanged(); };
      const choose = element('td'); choose.append(pick); row.append(choose);
      if (thumbnails) { const cell = element('td'); cell.append(element('span', 'No cached image', 'cleanup-placeholder')); if (item.thumb_url) { cell.cleanupItem = item; observer.observe(cell); } row.append(cell); }
      const nameCell = element('td');
      if (trash) nameCell.textContent = item.name;
      else { const link = element('a', item.name); link.href = item.url; nameCell.append(link); }
      row.append(nameCell);
      const locationCell = element('td');
      if (!trash) { const link = element('a', item.location, 'cleanup-location'); link.href = item.folder_url; locationCell.append(link); }
      else locationCell.textContent = item.location;
      row.append(locationCell);
      const sizeCell = element('td', bytes(item.size), 'cleanup-number'), exact = element('span', item.size.toLocaleString() + ' bytes', 'cleanup-bytes'); exact.tabIndex = 0; sizeCell.append(exact); row.append(sizeCell);
      const length = element('td', duration(item.duration), 'cleanup-number'); if (item.duration == null) length.title = item.metadata_status === 'failed' ? 'Unable to read duration; retry preparation.' : item.kind === 'video' ? 'Duration not prepared.' : 'Duration unavailable.'; row.append(length);
      const date = trash ? item.trashed_at : item.mtime; row.append(element('td', date ? new Date(date * 1000).toLocaleString() : '—', 'cleanup-number'));
      const status = {trashed:'In Trash',attention:'Needs attention',move_intent:'Move pending',restore_intent:'Restore pending',purge_intent:'Deletion pending'}[item.state] || 'Needs attention';
      row.append(element('td', trash ? status + (item.error ? ': ' + item.error : '') : item.kind)); fragment.append(row);
    }
    if (!items.length) { const row = element('tr'), cell = element('td', 'No matching indexed files. Try another filename or scope.'); cell.colSpan = thumbnails ? 8 : 7; row.append(cell); fragment.append(row); }
    $('cleanupRows').replaceChildren(fragment);
    $('cleanupStatus').textContent = result.total.toLocaleString() + ' files' + (result.error ? ' · ' + result.error : '');
    $('cleanupCoverage').textContent = trash ? 'Trash records are retained while storage is offline; actions recheck availability.' : (result.coverage || []).map(x => x.folders + ' indexed folders').join(' · ') + ' · Results may be incomplete until indexing finishes, or stale while a library is offline.';
    $('cleanupPage').textContent = 'Page ' + result.page + ' of ' + Math.max(1, result.pages);
    $('cleanupPrevious').disabled = result.page <= 1; $('cleanupNext').disabled = result.page >= result.pages;
  }
  async function load() {
    loading = true; clearSelection(); controls(); if (controller) controller.abort(); controller = new AbortController(); const current = ++sequence;
    history.replaceState(null, '', location.pathname + '?' + new URLSearchParams(state));
    $('cleanupStatus').textContent = 'Loading indexed results…';
    try { const result = await request('/api/cleanup/' + (state.tab === 'trash' ? 'trash' : 'files') + '?' + new URLSearchParams(state), undefined, controller.signal); if (current === sequence && !stopped) { loading = false; render(result); } }
    catch (error) { if (error.name !== 'AbortError' && current === sequence) { loading = false; items = []; $('cleanupRows').replaceChildren(); clearSelection(); $('cleanupStatus').textContent = error.message; } }
  }
  function changed() { state.page = 1; load(); }
  $('cleanupQuery').oninput = () => { clearTimeout(debounce); state.q = $('cleanupQuery').value; clearSelection(); debounce = setTimeout(changed, 250); };
  $('cleanupRoot').onchange = () => { state.root = $('cleanupRoot').value; state.relative = ''; state.scope = state.root ? 'library' : 'all'; $('cleanupScope').value = state.scope; changed(); };
  $('cleanupScope').onchange = () => { state.scope = $('cleanupScope').value; if (!state.root) { state.scope = 'all'; $('cleanupScope').value = 'all'; } changed(); };
  $('cleanupType').onchange = () => { state.file_type = $('cleanupType').value; changed(); };
  $('matchingSizes').onchange = () => { state.matching_sizes = $('matchingSizes').checked ? '1' : '0'; if ($('matchingSizes').checked) { state.sort = 'size'; state.direction = 'asc'; } changed(); };
  $('cleanupThumbnails').onchange = () => { savePreference($('cleanupThumbnails').checked); cancelThumbnails(); load(); };
  document.querySelectorAll('[data-sort]').forEach(button => { button.onclick = () => { state.direction = state.sort === button.dataset.sort && state.direction === 'asc' ? 'desc' : 'asc'; state.sort = button.dataset.sort; changed(); }; });
  $('selectCleanupPage').onchange = () => { selected = $('selectCleanupPage').checked && writes && !working && !loading ? new Map(items.map(item => [key(item), item])) : new Map(); document.querySelectorAll('#cleanupRows input[type=checkbox]').forEach(input => { input.checked = !!selected.size; input.closest('tr').classList.toggle('selected', input.checked); }); selectionChanged(); };
  function tab(value) { state.tab = value; state.sort = value === 'library' && state.matching_sizes === '1' ? 'size' : 'name'; state.direction = 'asc'; changed(); }
  $('libraryTab').onclick = () => tab('library'); $('trashTab').onclick = () => tab('trash');
  $('cleanupPrevious').onclick = () => { state.page--; load(); }; $('cleanupNext').onclick = () => { state.page++; load(); }; $('refreshCleanup').onclick = load;
  function showResults(results) { $('cleanupFailures').replaceChildren(); for (const item of results) if (item.status === 'error' || item.error) { const reference = Number.isInteger(item.request_index) ? 'Selection ' + (item.request_index + 1) : 'Selected file'; $('cleanupFailures').append(element('li', (item.name || item.rel || reference) + ': ' + (item.error || 'Unable to complete action.'))); } }
  async function watch(job) {
    try { sessionStorage.setItem('lanstream-cleanup-job', job.job_id); } catch (_) {}
    while (job.state !== 'complete') {
      $('cleanupActionStatus').textContent = 'Operation ' + job.state + ' · ' + job.completed + ' of ' + job.total + ' files. A slow share may leave this pending; avoid repeating the action.';
      await new Promise(resolve => setTimeout(resolve, 500)); if (stopped) throw Error('Operation continues on the server. Reopen Cleanup to check it.');
      job = await request('/api/cleanup/jobs/' + job.job_id);
    }
    try { sessionStorage.removeItem('lanstream-cleanup-job'); } catch (_) {}
    return job;
  }
  async function submit(action, rows) { return watch(await request('/api/cleanup/actions', {action,items:rows,operation_id:crypto.randomUUID()})); }
  function openConfirmation(title, description, rows, callback, danger = false) {
    lastFocus = document.activeElement; pendingConfirmation = callback;
    $('cleanupConfirmTitle').textContent = title; $('cleanupConfirmDescription').textContent = description;
    $('cleanupConfirmItems').replaceChildren();
    rows.forEach((item,index) => { const li = element('li', (index + 1) + '. ' + (item.name || item.rel || 'Selected file')); li.append(element('span', (item.location || item.rel || '') + ' · ' + (item.size || 0).toLocaleString() + ' bytes', 'cleanup-location')); $('cleanupConfirmItems').append(li); });
    $('cleanupConfirmTotal').textContent = rows.length ? rows.length + ' files · ' + bytes(rows.reduce((sum,item) => sum + item.size,0)) : '';
    $('cleanupConfirmAction').textContent = danger ? 'Delete permanently' : 'Confirm'; $('cleanupConfirmAction').className = danger ? 'cleanup-danger' : '';
    $('cleanupConfirm').showModal(); $('cleanupCancel').focus();
  }
  function closeConfirmation() { $('cleanupConfirm').close(); pendingConfirmation = null; if (lastFocus) lastFocus.focus(); }
  $('cleanupCancel').onclick = closeConfirmation;
  $('cleanupConfirm').addEventListener('cancel', () => { pendingConfirmation = null; if (lastFocus) lastFocus.focus(); });
  $('cleanupConfirmAction').onclick = async () => { const action = pendingConfirmation; closeConfirmation(); if (action) await action(); };
  async function preview(action) {
    if (!selected.size || !writes || working) return;
    const selectedRows = [...selected.values()]; working = true; controls();
    try {
      const job = await submit('preview_' + action, selectedRows.map(item => action === 'move' ? {selection_token:item.selection_token} : {item_id:item.item_id}));
      showResults(job.results); const ready = job.results.filter(item => item.status === 'ready');
      if (!ready.length) { $('cleanupActionStatus').textContent = 'No selected files are ready. Review the errors and refresh the results.'; return; }
      const rows = ready.map(item => ({...selectedRows.find(row => row.rel === item.rel && row.root_id === item.root_id),...item}));
      openConfirmation(action === 'move' ? 'Move selected files to Trash?' : action === 'restore' ? 'Restore selected files?' : 'Permanently delete selected files?', action === 'move' ? 'Files can be restored later. Disk space is retained until permanent deletion.' : action === 'restore' ? 'Files return to their original existing folders. Existing destinations are never overwritten.' : 'This cannot be undone. Only the files listed below will be permanently removed.', rows, async () => {
        working = true; controls();
        try { const completed = await submit(action, ready.map(item => ({confirmation_token:item.confirmation_token}))); showResults(completed.results); $('cleanupActionStatus').textContent = 'Operation complete · ' + completed.results.filter(item => item.status !== 'error').length + ' succeeded · ' + completed.results.filter(item => item.status === 'error').length + ' failed.'; await load(); }
        catch (error) { $('cleanupActionStatus').textContent = error.message; }
        finally { working = false; controls(); }
      }, action === 'purge');
    } catch (error) { $('cleanupActionStatus').textContent = error.message; }
    finally { working = false; controls(); }
  }
  $('moveCleanup').onclick = () => preview('move'); $('restoreCleanup').onclick = () => preview('restore'); $('purgeCleanup').onclick = () => preview('purge');
  async function durationStatus(action) {
    clearTimeout(durationPoll);
    try {
      const result = await request('/api/cleanup/durations?' + scopeParams(), action ? {action} : undefined), job = result.job;
      if (stopped) return;
      const finished = durationRunning && !job?.running; durationRunning = !!job?.running;
      $('prepareDurations').disabled = $('retryDurations').disabled = !!job?.running;
      $('pauseDurations').hidden = !job?.running || job.paused; $('resumeDurations').hidden = !job?.running || !job.paused; $('stopDurations').hidden = !job?.running;
      $('durationStatus').textContent = job ? (job.error || (job.running ? job.stopping ? 'Stopping after the current file' : job.paused ? 'Preparation paused' : 'Preparing durations' : 'Preparation finished') + ': ' + job.ready + ' saved, ' + job.failed + ' failed, ' + job.changed + ' changed, ' + job.checked + ' checked.') : 'No duration preparation started in this session.';
      if (job?.running) durationPoll = setTimeout(() => durationStatus(), 1000);
      else if (action || finished) await load();
    } catch (error) { if (!stopped) $('durationStatus').textContent = error.message; }
  }
  function prepare(action) { openConfirmation(action === 'retry' ? 'Retry failed durations?' : 'Prepare video durations?', 'Scope: ' + $('cleanupPath').textContent + ' (' + $('cleanupScope').value + '). Search text and matching-size filters do not limit preparation. One worker pauses between files during playback.', [], () => durationStatus(action)); }
  $('prepareDurations').onclick = () => prepare('start'); $('retryDurations').onclick = () => prepare('retry');
  $('pauseDurations').onclick = () => durationStatus('pause'); $('resumeDurations').onclick = () => durationStatus('resume'); $('stopDurations').onclick = () => durationStatus('stop');
  $('cleanupPrepareThumbnails').onclick = async () => { const index = [...$('cleanupRoot').options].find(option => option.value === state.root)?.dataset.index; if (index == null || state.scope !== 'folder') return; openConfirmation('Prepare thumbnails in this folder?', $('cleanupPath').textContent + ' only, without subfolders. Preparation yields during playback.', [], async () => { try { await request('/api/prepare-thumbnails/' + index + '?relative=' + encodeURIComponent(state.relative), {action:'start'}); $('cleanupActionStatus').textContent = 'Thumbnail preparation started for this folder. Refresh results to see cached images.'; } catch (error) { $('cleanupActionStatus').textContent = error.message; } }); };
  $('recoverTrash').onclick = () => { if (!state.root || !writes) return; openConfirmation('Recover Trash records?', 'Inspect the selected library’s reserved Trash directory and recover valid records. No files will be permanently deleted.', [], async () => { working = true; controls(); try { const job = await submit('recover', [{root_id:state.root,rel:''}]); showResults(job.results); $('cleanupActionStatus').textContent = 'Trash recovery completed. Review any items that need attention.'; await load(); } catch (error) { $('cleanupActionStatus').textContent = error.message; } finally { working = false; controls(); } }); };
  async function loadFolders() {
    $('folderPickerPath').textContent = pickerRelative || 'Library root'; $('folderPickerUp').disabled = !pickerRelative;
    try { const result = await request('/api/cleanup/folders?' + new URLSearchParams({scope:'folder',root:state.root,relative:pickerRelative,page:pickerPage})); $('folderPickerFolders').replaceChildren(); result.items.forEach(item => { const li = element('li'), button = element('button', item.name); button.type = 'button'; button.onclick = () => { pickerRelative = item.rel; pickerPage = 1; loadFolders(); }; li.append(button); $('folderPickerFolders').append(li); }); $('folderPickerStatus').textContent = result.total + ' indexed folders. Newly indexed folders appear after refresh.'; $('folderPickerPrevious').disabled = result.page <= 1; $('folderPickerNext').disabled = result.page >= result.pages; }
    catch (error) { $('folderPickerStatus').textContent = error.message; }
  }
  $('chooseFolder').onclick = () => { if (!state.root) return; pickerRelative = state.relative; pickerPage = 1; $('cleanupFolderDialog').showModal(); loadFolders(); };
  $('folderPickerUp').onclick = () => { pickerRelative = pickerRelative.split('/').slice(0,-1).join('/'); pickerPage = 1; loadFolders(); };
  $('folderPickerChoose').onclick = () => { state.relative = pickerRelative; state.scope = 'folder'; $('cleanupScope').value = 'folder'; $('cleanupFolderDialog').close(); changed(); };
  $('folderPickerClose').onclick = () => $('cleanupFolderDialog').close(); $('folderPickerPrevious').onclick = () => { pickerPage--; loadFolders(); }; $('folderPickerNext').onclick = () => { pickerPage++; loadFolders(); };
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(debounce); clearTimeout(durationPoll); if (controller) controller.abort(); cancelThumbnails(); });
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; load(); durationStatus(); } });
  load(); durationStatus();
  try { const job = sessionStorage.getItem('lanstream-cleanup-job'); if (job) { working = true; request('/api/cleanup/jobs/' + job).then(watch).then(result => { showResults(result.results); return load(); }).catch(error => { $('cleanupActionStatus').textContent = error.message; }).finally(() => { working = false; controls(); }); } } catch (_) {}
})();
