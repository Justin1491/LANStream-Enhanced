const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const filename = path.join(__dirname, '../static/thumbnail-cache.js');
const script = fs.existsSync(filename) ? fs.readFileSync(filename, 'utf8') : '';

function fixture({scope='alice', session=new Map(), local=new Map(), fetcher} = {}) {
  const storage = map => ({getItem:key => map.get(key) ?? null, setItem:(key,value) => map.set(key,String(value)),
    removeItem:key => map.delete(key), key:i => [...map.keys()][i], get length() { return map.size; }});
  const window = new EventTarget(), calls = [], root = {style:{}}, logout = new EventTarget();
  let reloads = 0;
  const location = {reload:() => reloads++};
  vm.runInNewContext(script, {window, location, document:{documentElement:root,
    querySelector:selector => selector.includes('meta') ? {content:scope} : logout},
    sessionStorage:storage(session), localStorage:storage(local), Uint8Array, URL,
    btoa, DOMException, fetch:async (url, options) => {
      calls.push({url,options});
      return fetcher ? fetcher(url,options) : {ok:true, headers:{get:name => ({'Content-Type':'image/jpeg',
        'X-LANStream-Thumbnail-Cache':'tab-session','X-LANStream-Thumbnail-Scope':scope})[name]},
        arrayBuffer:async () => new Uint8Array([255,216,255]).buffer};
    }});
  assert.ok(window.LANStreamThumbnailCache, 'temporary thumbnail cache must be available');
  return {cache:window.LANStreamThumbnailCache, window, calls, session, local, root, logout, get reloads() { return reloads; }};
}

test('thumbnail bytes are reused across folder navigation and a fresh tab starts empty', async () => {
  const session = new Map(), first = fixture({session});
  const image = await first.cache.load('/thumb/0/a.mp4?v=1');
  assert.equal(image, 'data:image/jpeg;base64,/9j/');
  assert.equal(await first.cache.load('/thumb/0/a.mp4?v=1'), image);
  assert.equal(first.calls.length, 1);
  assert.equal(first.calls[0].options.cache, 'no-store');
  const next = fixture({session});
  assert.equal(await next.cache.load('/thumb/0/a.mp4?v=1'), image);
  assert.equal(next.calls.length, 0);
  await next.cache.load('/thumb/0/a.mp4?v=2');
  assert.equal(next.calls.length, 1, 'changed files need fresh thumbnails');
  const newTab = fixture();
  await newTab.cache.load('/thumb/0/a.mp4?v=1');
  assert.equal(newTab.calls.length, 1);
});

test('logout, changed access scope and another tab invalidate private thumbnails', async () => {
  const session = new Map(), local = new Map(), first = fixture({session,local});
  await first.cache.load('/thumb/0/private.mp4?v=1');
  first.logout.dispatchEvent(new Event('click'));
  assert.equal([...session.keys()].filter(key => key.startsWith('lanstream-thumbnail:')).length, 0);
  const bob = fixture({scope:'bob',session,local});
  await bob.cache.load('/thumb/0/private.mp4?v=1');
  assert.equal(bob.calls.length, 1);
  const changed = new Event('storage');
  Object.assign(changed, {key:'lanstream-thumbnail-scope',newValue:'another-session'});
  bob.window.dispatchEvent(changed);
  assert.equal(bob.reloads, 1);
  assert.equal(bob.root.style.visibility, 'hidden');
  assert.equal([...session.keys()].filter(key => key.startsWith('lanstream-thumbnail:')).length, 0);
});

test('late downloads cannot repopulate the cache after a scope change or navigation', async () => {
  let finish;
  const e = fixture({fetcher:() => new Promise(resolve => finish=resolve)});
  const pending = e.cache.load('/thumb/0/private.mp4');
  e.cache.sync('bob');
  finish({ok:true, headers:{get:name => ({'Content-Type':'image/jpeg','X-LANStream-Thumbnail-Cache':'tab-session',
    'X-LANStream-Thumbnail-Scope':'alice'})[name]}, arrayBuffer:async () => new Uint8Array([1]).buffer});
  await assert.rejects(pending);
  assert.equal([...e.session.keys()].filter(key => key.startsWith('lanstream-thumbnail:')).length, 0);
});

test('errors are not cached and unavailable browser storage does not break loading', async () => {
  const bad = fixture({fetcher:async () => ({ok:false,status:404})});
  await assert.rejects(bad.cache.load('/thumb/0/missing.mp4'));
  await assert.rejects(bad.cache.load('/thumb/0/missing.mp4'));
  assert.equal(bad.calls.length, 2);
  const broken = new Map(); broken.set = () => { throw Error('storage disabled'); };
  const good = fixture({session:broken,local:broken});
  assert.equal(await good.cache.load('/thumb/0/a.mp4'), 'data:image/jpeg;base64,/9j/');
});

test('cache stays bounded and evicted thumbnails can be downloaded again', async () => {
  const e = fixture({fetcher:async () => ({ok:true,headers:{get:name => ({'Content-Type':'image/jpeg',
    'X-LANStream-Thumbnail-Cache':'tab-session','X-LANStream-Thumbnail-Scope':'alice'})[name]},
    arrayBuffer:async () => new Uint8Array(450000).buffer})});
  for (let i=0;i<5;i++) await e.cache.load('/thumb/0/' + i);
  const bytes = [...e.session].filter(([key]) => key.startsWith('lanstream-thumbnail:')).reduce((total,[,value]) => total + value.length,0);
  assert.ok(bytes > 0 && bytes <= 2 * 1024 * 1024);
  await e.cache.load('/thumb/0/0');
  assert.equal(e.calls.length, 6);
});

test('restored pages reload authorization before revealing old content', () => {
  const e = fixture();
  e.window.dispatchEvent(new Event('pagehide'));
  assert.equal(e.root.style.visibility, 'hidden');
  const restored = new Event('pageshow'); restored.persisted=true;
  e.window.dispatchEvent(restored);
  assert.equal(e.reloads, 1);
});

test('a download finishing after logout cannot restore private session bytes', async () => {
  let finish;
  const e=fixture({fetcher:()=>new Promise(resolve=>finish=resolve)});
  const pending=e.cache.load('/thumb/0/private.mp4');
  e.logout.dispatchEvent(new Event('click'));
  finish({ok:true,headers:{get:name=>({'Content-Type':'image/jpeg','X-LANStream-Thumbnail-Cache':'tab-session',
    'X-LANStream-Thumbnail-Scope':'alice'})[name]},arrayBuffer:async()=>new Uint8Array([1]).buffer});
  await assert.rejects(pending);
  assert.equal([...e.session.keys()].filter(key=>key.startsWith('lanstream-thumbnail:')).length,0);
});
