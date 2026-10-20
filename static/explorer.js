(() => {
  'use strict';
  const explorer = document.getElementById('explorer');
  if (!explorer) return;
  const items = document.getElementById('explorerItems'), status = document.getElementById('explorerStatus');
  const query = document.getElementById('explorerQuery'), sort = document.getElementById('explorerSort');
  const fileType = document.getElementById('fileType');
  const scope = document.getElementById('searchScope'), direction = document.getElementById('sortDirection');
  const density = document.getElementById('gridDensity'), previous = document.getElementById('previousPage'), next = document.getElementById('nextPage');
  const csrf = document.querySelector('meta[name="lanstream-csrf"]').content;
  let controller, polling, debounce, sequence = 0, observer, stopped = false, state = {};
  let thumbnailImages = new Map(), thumbnailWork;
  function resetThumbnails() {
    if (thumbnailWork) thumbnailWork.controller.abort();
    thumbnailWork = {controller: new AbortController(), queue: [], active: 0, started: new WeakSet()};
    thumbnailImages = new Map([...thumbnailImages].filter(([, image]) => image.complete && image.naturalWidth));
  }
  function restore() {
    const params = new URLSearchParams(location.search);
    state = {page: Number(params.get('page')) || 1, q: params.get('q') || '', sort: params.get('sort') || 'name', direction: params.get('direction') || 'asc', scope: params.get('scope') || 'all', root: params.get('root') || explorer.dataset.root || '0', relative: params.get('relative') || explorer.dataset.relative, file_type: params.get('file_type') || 'all'};
    fileType.value = state.file_type;
    if (!fileType.value) fileType.value = state.file_type = 'all';
    if (query) query.value = state.q;
    if (sort) sort.value = state.sort;
    if (scope) scope.value = state.scope;
    if (direction) direction.textContent = state.direction === 'desc' ? 'Descending' : 'Ascending';
  }
  function preference(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } }
  function save(key, value) { try { localStorage.setItem(key, value); } catch (_) {} }
  function view(mode) {
    if (!['list', 'grid', 'details'].includes(mode)) mode = 'list';
    items.className = 'explorer-items ' + mode;
    document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-pressed', button.dataset.view === mode));
    document.querySelector('.details-heading').classList.toggle('visible', mode === 'details');
    document.getElementById('densityControl').hidden = mode !== 'grid'; save('lanstream-view', mode);
  }
  document.querySelectorAll('[data-view]').forEach(button => button.onclick = () => view(button.dataset.view));
  density.value = preference('lanstream-density', '180');
  function gridSize() { items.style.setProperty('--grid-size', density.value + 'px'); save('lanstream-density', density.value); }
  density.oninput = gridSize; gridSize();
  view(preference('lanstream-view', matchMedia('(max-width:700px)').matches ? 'list' : 'grid'));
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function size(bytes) {
    if (bytes === null || bytes === undefined) return '—';
    if (bytes < 1024) return bytes + ' B';
    const unit = Math.min(3, Math.floor(Math.log(bytes) / Math.log(1024)));
    return (bytes / 1024 ** unit).toFixed(1) + ' ' + ['B', 'KB', 'MB', 'GB'][unit];
  }
  async function write(url, data) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-LANStream-CSRF': csrf}, body: JSON.stringify(data)});
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw Error(result.error || (response.status === 401 ? 'Sign in again to continue.' : 'Action failed. The item may be missing or access may have changed.'));
    }
    return response.json();
  }
  function render(result) {
    if (state.page !== result.page) {
      resetThumbnails();
      state.page = result.page;
      history.replaceState(null, '', location.pathname + '?' + new URLSearchParams(state));
    }
    if (observer) observer.disconnect();
    const work = thumbnailWork, signal = work.controller.signal, thumbnails = work.queue;
    function drainThumbnails() {
      // Leave connections free for paging while slow shares serve thumbnails.
      while (!signal.aborted && work.active < 2 && thumbnails.length) {
        work.active++;
        thumbnails.shift()().finally(() => { work.active--; drainThumbnails(); });
      }
    }
    observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      observer.unobserve(entry.target);
      const image = entry.target.querySelector('img'); let deadline, attempts = 0, retry;
      if (!image || work.started.has(image)) return;
      work.started.add(image);
      signal.addEventListener('abort', () => clearTimeout(retry), {once: true});
      const loadImage = () => new Promise(resolve => {
        if (stopped || signal.aborted || !image.isConnected || Date.now() >= deadline) { resolve(false); return; }
        let timeout;
        const done = event => {
          clearTimeout(timeout);
          image.removeEventListener('load', done); image.removeEventListener('error', done);
          signal.removeEventListener('abort', cancel);
          const loaded = event?.type === 'load';
          image.hidden = !loaded;
          if (!loaded) image.removeAttribute('src');
          resolve(loaded);
        };
        const cancel = () => done();
        image.addEventListener('load', done, {once: true}); image.addEventListener('error', done, {once: true});
        signal.addEventListener('abort', cancel, {once: true});
        timeout = setTimeout(cancel, Math.min(10000, deadline - Date.now()));
        image.src = image.dataset.src + '?v=' + encodeURIComponent(image.dataset.version);
      });
      const check = async () => {
        if (stopped || signal.aborted || !image.isConnected || Date.now() >= deadline) return;
        const requestController = new AbortController(), cancel = () => requestController.abort();
        signal.addEventListener('abort', cancel, {once: true});
        const timeout = setTimeout(cancel, Math.min(10000, deadline - Date.now()));
        try {
          const response = await fetch(image.dataset.ready, {cache: 'no-store', signal: requestController.signal});
          if (!response.ok) return;
          const ready = await response.json();
          if (signal.aborted || !image.isConnected) return;
          if (ready.ready && await loadImage()) return;
          if (ready.failed) return;
        } catch (_) { /* Revisit to retry unavailable thumbnails. */ }
        finally { clearTimeout(timeout); signal.removeEventListener('abort', cancel); }
        if (!signal.aborted && Date.now() < deadline) retry = setTimeout(() => { thumbnails.push(check); drainThumbnails(); }, Math.min(1500, 250 * 2 ** Math.floor(++attempts / 4)));
      };
      // Cached images need one request; only missing images need generation polling.
      thumbnails.push(async () => { deadline = Date.now() + 90000; if (!await loadImage()) await check(); }); drainThumbnails();
    }), {rootMargin: '160px 0px'});
    const fragment = document.createDocumentFragment(), nextImages = new Map();
    for (const item of result.items) {
      const row = element('article', 'explorer-item'), link = element('a', 'item-main'); link.href = item.url;
      const preview = element('span', 'item-preview', item.kind.toUpperCase());
      if (item.kind === 'folder') { preview.textContent = ''; preview.append(element('span', 'folder-icon')); }
      if (item.thumb_url) {
        const key = JSON.stringify([item.thumb_url, item.mtime, item.size]);
        let image = thumbnailImages.get(key);
        if (!image) {
          image = element('img'); image.alt = ''; image.hidden = true;
          image.dataset.src = item.thumb_url; image.dataset.ready = item.ready_url;
          image.dataset.version = result.version || 'cached';
        }
        if (!image.complete || !image.naturalWidth) observer.observe(preview);
        preview.append(image); nextImages.set(key, image);
      }
      const label = element('span', 'item-label'); label.append(element('span', 'item-name', item.name));
      if (explorer.dataset.mode !== 'browse') label.append(element('span', 'item-location', item.location));
      link.append(preview, label, element('span', 'item-size', item.kind === 'folder' ? '—' : size(item.size)), element('span', 'item-date', item.mtime ? new Date(item.mtime * 1000).toLocaleString() : '—'), element('span', 'item-kind', item.kind));
      row.append(link); const actions = element('div', 'item-actions');
      if (['folder', 'video'].includes(item.kind)) {
        const favorite = element('button', 'favorite-button', item.favorite ? '★' : '☆'); favorite.type = 'button'; favorite.setAttribute('aria-label', 'Favorite ' + item.name); favorite.setAttribute('aria-pressed', item.favorite);
        favorite.onclick = async () => {
          favorite.disabled = true;
          try { await write('/api/favorites', {root_index: item.root_index, rel: item.rel, kind: item.kind, enabled: !item.favorite}); item.favorite = !item.favorite; favorite.textContent = item.favorite ? '★' : '☆'; favorite.setAttribute('aria-pressed', item.favorite); }
          catch (error) { status.textContent = error.message; } finally { favorite.disabled = false; }
        }; actions.append(favorite);
      }
      if (explorer.dataset.mode !== 'browse') { const folder = element('a', 'item-folder-link', 'Open folder'); folder.href = item.folder_url; actions.append(folder); }
      row.append(actions); fragment.append(row);
    }
    items.replaceChildren(fragment); thumbnailImages = nextImages;
    const scanning = result.state === 'loading';
    status.textContent = scanning ? 'Reading this folder for the first time… SMB enumeration may take a while.' : result.error ? result.error + (result.version ? ' Showing the last cached snapshot.' : '') : result.state === 'stale' ? result.total.toLocaleString() + ' items · cached snapshot, refreshing in the background.' : result.total ? result.total.toLocaleString() + ' items' : 'No matching folders or supported files.';
    if (result.updated_at && !scanning) status.textContent += ' · Updated ' + new Date(result.updated_at * 1000).toLocaleString();
    previous.disabled = state.page <= 1; next.disabled = state.page >= result.pages;
    document.getElementById('pageLabel').textContent = 'Page ' + state.page + ' of ' + Math.max(1, result.pages);
    document.getElementById('coverage').textContent = result.coverage ? result.coverage.map(root => root.folders + ' cached folders / ' + root.items.toLocaleString() + ' items').join(' · ') + ' · Partial coverage until recursive indexing completes; offline results may be stale.' : '';
    if (scanning || result.refreshing) polling = setTimeout(() => load(false, true), 1500);
  }
  async function load(historyUpdate = true, background = false) {
    clearTimeout(polling); if (controller) controller.abort(); controller = new AbortController(); const current = ++sequence;
    if (!background) resetThumbnails();
    const params = new URLSearchParams(state); if (historyUpdate) history.pushState(null, '', location.pathname + '?' + params);
    status.textContent = 'Loading cached results…';
    try { const response = await fetch(explorer.dataset.api + '?' + params, {signal: controller.signal, cache: 'no-store'});
      if (!response.ok) throw Error(response.status === 401 ? 'Sign in again to continue.' : 'Unable to load this folder. Check access and try again.');
      const result = await response.json(); if (current === sequence) render(result);
    } catch (error) { if (error.name !== 'AbortError' && current === sequence) status.textContent = error.message; }
  }
  function changed() { state.page = 1; load(); }
  if (query) query.oninput = () => { clearTimeout(debounce); state.q = query.value; debounce = setTimeout(changed, 250); };
  if (sort) sort.onchange = () => { state.sort = sort.value; changed(); };
  fileType.onchange = () => { state.file_type = fileType.value; changed(); };
  if (scope) scope.onchange = () => { state.scope = scope.value; changed(); };
  if (direction) direction.onclick = () => { state.direction = state.direction === 'asc' ? 'desc' : 'asc'; direction.textContent = state.direction === 'desc' ? 'Descending' : 'Ascending'; changed(); };
  previous.onclick = () => { state.page--; load(); window.scrollTo(0, 0); }; next.onclick = () => { state.page++; load(); window.scrollTo(0, 0); };
  const refresh = document.getElementById('refreshFolder');
  if (refresh) refresh.onclick = async () => { refresh.disabled = true;
    try { await write(explorer.dataset.api.replace('/api/browse/', '/api/refresh/'), {}); await load(false); }
    catch (error) { status.textContent = error.message; } finally { refresh.disabled = false; }
  };
  let indexPoll;
  async function indexStatus(action) {
    const url = '/api/index/' + explorer.dataset.root + '?relative=' + encodeURIComponent(explorer.dataset.relative);
    try { const response = action ? await write(url, {action}) : await (await fetch(url)).json(); const crawl = response.crawls[0];
      if (stopped) return;
      document.getElementById('indexStatus').textContent = crawl ? (crawl.paused ? 'Paused' : crawl.running ? 'Indexing library' : crawl.automatic ? 'Watching for changes' : 'Complete') + ': ' + crawl.completed + ' folders read, ' + crawl.pending + ' queued, ' + crawl.errors + ' errors.' : explorer.dataset.automaticIndexing === 'true' ? 'Automatic library indexing is starting.' : 'Automatic indexing is disabled. Use Resume / refresh library index to start.';
    } catch (_) { if (!stopped) document.getElementById('indexStatus').textContent = 'Indexing status unavailable. Retrying…'; }
    if (!stopped) indexPoll = setTimeout(() => indexStatus(), 2000);
  }
  if (document.getElementById('startIndex')) {
    document.getElementById('startIndex').onclick = () => { clearTimeout(indexPoll); indexStatus('start'); };
    document.getElementById('pauseIndex').onclick = () => { clearTimeout(indexPoll); indexStatus('pause'); }; indexStatus();
  }
  let thumbnailPoll;
  const prepare = document.getElementById('prepareThumbnails'), stop = document.getElementById('stopThumbnails');
  async function preparationStatus(action) {
    clearTimeout(thumbnailPoll);
    const label = document.getElementById('thumbnailStatus');
    const url = '/api/prepare-thumbnails/' + explorer.dataset.root + '?relative=' + encodeURIComponent(explorer.dataset.relative);
    if (action) prepare.disabled = stop.disabled = true;
    try {
      let response;
      if (action) response = await write(url, {action});
      else {
        const result = await fetch(url, {cache: 'no-store'});
        if (!result.ok) throw Error('Thumbnail preparation status unavailable. Retrying…');
        response = await result.json();
      }
      if (stopped) return;
      const job = response.preparation;
      prepare.disabled = !!(job && job.running); stop.hidden = !(job && job.running); stop.disabled = !!(job && job.stopping);
      label.textContent = !job ? '' : job.error || (job.stopping ? (job.running ? 'Stopping preparation' : 'Preparation stopped') : job.running ? (job.paused ? 'Preparation paused during playback' : 'Preparing thumbnails') : 'Preparation complete') + ': ' + job.ready.toLocaleString() + ' ready, ' + job.failed.toLocaleString() + ' failed, ' + job.checked.toLocaleString() + ' checked.';
      if (job && job.running) thumbnailPoll = setTimeout(() => preparationStatus(), 1000);
    } catch (error) {
      if (stopped) return;
      label.textContent = error.message || 'Thumbnail preparation status unavailable. Retrying…';
      prepare.disabled = false; stop.disabled = false;
      thumbnailPoll = setTimeout(() => preparationStatus(), 2000);
    }
  }
  if (prepare) {
    prepare.onclick = () => preparationStatus('start'); stop.onclick = () => preparationStatus('stop'); preparationStatus();
  }
  window.addEventListener('popstate', () => { restore(); load(false); });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(polling); clearTimeout(indexPoll); clearTimeout(thumbnailPoll); clearTimeout(debounce); if (controller) controller.abort(); if (thumbnailWork) thumbnailWork.controller.abort(); if (observer) observer.disconnect(); });
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; restore(); load(false); if (prepare) preparationStatus(); if (document.getElementById('startIndex')) indexStatus(); } });
  restore(); load(false);
})();
