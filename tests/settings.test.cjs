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
  const calls = [], pending = [];
  const data = {thumbnails: {enabled: true, state: 'idle'}, scheduler: {pending: 2},
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
  return {nodes, calls, document, poll: () => poll(), failPost: () => { postError = true; }};
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('renders status and polls only when visible', async () => {
  const page = setup(); await flush();
  assert.equal(page.nodes.automaticThumbnails.checked, true);
  assert.match(page.nodes.thumbnailStatus.textContent, /idle/);
  assert.match(page.nodes.indexingStatus.children[0].children[0].textContent, /Photos: Indexing/);
  assert.match(page.nodes.storageDetails.children[1].children[0].textContent, /1.0 KiB/);
  assert.match(page.nodes.updateStatus.textContent, /Comparison unavailable/);
  page.document.hidden = true; page.poll(); await flush();
  assert.equal(page.calls.length, 1);
  page.document.hidden = false; page.poll(); await flush();
  assert.equal(page.calls.length, 2);
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
