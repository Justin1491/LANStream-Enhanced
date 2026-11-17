const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup() {
  const nodes = {};
  const element = () => ({textContent: '', children: [], listeners: {}, checked: false, disabled: false,
    replaceChildren() { this.children = []; this.textContent = ''; },
    appendChild(child) { this.children.push(child); },
    addEventListener(event, fn) { this.listeners[event] = fn; }});
  const document = {hidden: false, listeners: {}, getElementById(id) { return nodes[id] ||= element(); },
    createElement: element, querySelector() { return {content: 'csrf-value'}; },
    addEventListener(event, fn) { this.listeners[event] = fn; }};
  const calls = [];
  const data = {playback: {autoplay: true}, thumbnails: {enabled: true, state: 'waiting'}, scheduler: {pending: 2},
    indexing: [{label: 'Photos', running: true, completed: 4}],
    storage: {scanned: true, cache: {index: 1024}, system: {Runtime: 5}},
    updates: {current: null, latest: 'a'.repeat(40), checked: true}};
  let postError = false;
  const fetch = async (url, options) => {
    calls.push({url, options});
    if (options.method === 'POST' && postError) return {ok: false, json: async () => ({error: 'Denied'})};
    return {ok: true, json: async () => data};
  };
  let poll;
  const context = {document, fetch, AbortController, setTimeout, clearTimeout,
    setInterval(fn) { poll = fn; }, console};
  vm.runInNewContext(fs.readFileSync('static/settings.js', 'utf8'), context);
  return {nodes, calls, data, document, poll: () => poll(), failPost: () => { postError = true; }};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('renders status and polls only when visible', async () => {
  const page = setup(); await flush();
  assert.equal(page.nodes.automaticThumbnails.checked, true);
  assert.match(page.nodes.thumbnailStatus.textContent, /Waiting for an idle opportunity/);
  assert.match(page.nodes.thumbnailReason.textContent, /60 seconds without users/);
  assert.match(page.nodes.indexingStatus.children[0].children[0].textContent, /Photos: Indexing/);
  assert.match(page.nodes.storageDetails.children[1].children[0].textContent, /1.0 KiB/);
  assert.equal(page.nodes.storageTotals.children[0].children[0].textContent, 'Cache');
  assert.equal(page.nodes.storageTotals.children[0].children[1].textContent, '1.0 KiB');
  assert.equal(page.nodes.storageTotals.children[1].children[0].textContent, 'Application');
  assert.match(page.nodes.updateStatus.textContent, /Comparison unavailable/);
  page.document.hidden = true; page.poll(); await flush();
  assert.equal(page.calls.length, 1);
  page.document.hidden = false; page.poll(); await flush();
  assert.equal(page.calls.length, 2);
});

test('renders and saves autoplay through the existing CSRF-protected settings API', async () => {
  const page = setup(); await flush();
  assert.equal(page.nodes.autoplay.checked, true);
  page.data.playback.autoplay = false;
  await page.nodes.autoplay.listeners.change({target: {checked: false}});
  const post = page.calls.find(call => call.options.method === 'POST');
  assert.deepEqual(JSON.parse(post.options.body), {action:'enable_autoplay', enabled:false});
  assert.equal(post.options.headers['X-LANStream-CSRF'], 'csrf-value');
  assert.equal(page.nodes.autoplay.checked, false);
});

test('keeps preparation and request failures separate and uses only known waiting conditions', async () => {
  const page = setup(); await flush();
  page.data.thumbnails = {enabled: true, state: 'waiting', checked: 12, ready: 9, failed: 1};
  page.data.scheduler = {paused: true, pending: 2, active: 1, failed: 6};
  page.poll(); await flush();
  assert.match(page.nodes.thumbnailReason.textContent, /paused or playback is active/);
  assert.equal(page.nodes.automaticCounts.textContent, 'Automatic preparation this pass: 12 checked · 9 ready · 1 failed');
  assert.equal(page.nodes.schedulerStatus.textContent, 'Thumbnail requests: 2 pending · 1 active · 6 failed · paused or playback active');
  page.data.thumbnails = {enabled: false, state: 'disabled'};
  page.poll(); await flush();
  assert.equal(page.nodes.thumbnailStatus.textContent, 'Automatic preparation is off');
  page.data.thumbnails = {enabled: true, state: 'error', error: 'Could not read the library.'};
  page.poll(); await flush();
  assert.match(page.nodes.thumbnailReason.textContent, /Could not read the library/);
});

test('preserves expanded technical details while polling and retains storage warnings', async () => {
  const page = setup(); await flush();
  const disclosure = page.nodes.technicalStorage;
  disclosure.open = true;
  page.data.storage.partial = true;
  page.poll(); await flush();
  assert.equal(page.nodes.technicalStorage, disclosure);
  assert.equal(disclosure.open, true);
  assert.equal(disclosure.hidden, false);
  assert.equal(page.nodes.storageDetails.children.length, 4);
  assert.equal(page.nodes.storageTotals.children.length, 2);
  assert.match(page.nodes.storageStatus.textContent, /incomplete/);
  page.data.storage.running = true;
  page.poll(); await flush();
  assert.equal(page.nodes.scanStorage.disabled, true);
  assert.equal(page.nodes.storageStatus.textContent, 'Measuring storage…');
  page.data.storage.running = false;
  page.data.storage.error = 'Measurement failed.';
  page.poll(); await flush();
  assert.equal(page.nodes.storageStatus.textContent, 'Measurement failed.');
  const template = fs.readFileSync('templates/settings.html', 'utf8');
  assert.match(template, /<details><summary>Thumbnail request details<\/summary>/);
  assert.match(template, /<details id="technicalStorage" hidden><summary>Technical storage details<\/summary>/);
});

test('sends CSRF-protected actions and displays failures before refreshing', async () => {
  const page = setup(); await flush();
  page.failPost();
  await page.nodes.automaticThumbnails.listeners.change({target: {checked: false}});
  const post = page.calls.find(call => call.options.method === 'POST');
  assert.equal(post.options.headers['X-LANStream-CSRF'], 'csrf-value');
  assert.deepEqual(JSON.parse(post.options.body), {action: 'enable_thumbnails', enabled: false});
  assert.equal(page.nodes.settingsMessage.textContent, 'Denied');
  assert.equal(page.nodes.automaticThumbnails.checked, true);
  assert.equal(page.calls.length, 3);
});
