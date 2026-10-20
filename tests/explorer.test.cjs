// Run with: node --test tests/explorer.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/explorer.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function explorer(automaticIndexing, resultOverrides = {}) {
  const images = [], checks = [], observers = [], timers = new Map();
  let now = 0, timerSequence = 0;
  class Element extends EventTarget {
    constructor(tag) {
      super();
      this.tagName = tag;
      this.children = [];
      this.dataset = {};
      this.style = {setProperty() {}};
      this.classList = {toggle() {}};
      this.isConnected = true;
    }
    append(...children) {
      for (const child of children) {
        if (child.parent) child.parent.children = child.parent.children.filter(node => node !== child);
        child.parent = this; this.children.push(child);
      }
    }
    replaceChildren(fragment) { this.children = fragment.children; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    querySelector(tag) { return this.children.find(child => child.tagName === tag); }
    dispatchEvent(event) {
      if (this.tagName === 'img' && event.type === 'load') { this.complete = true; this.naturalWidth = 120; }
      return super.dispatchEvent(event);
    }
  }
  const elements = Object.fromEntries(['explorer', 'explorerItems', 'explorerStatus',
    'explorerQuery', 'explorerSort', 'fileType', 'gridDensity', 'previousPage',
    'nextPage', 'densityControl', 'pageLabel', 'coverage'].map(id => [id, new Element('div')]));
  elements.explorer.dataset = {api:'/api/browse/0/', mode:'browse', root:'0', relative:''};
  if (automaticIndexing !== undefined) {
    elements.explorer.dataset.automaticIndexing = String(automaticIndexing);
    for (const id of ['startIndex', 'pauseIndex', 'indexStatus']) elements[id] = new Element('div');
  }
  const location = {pathname:'/browse/0/', search:''};
  const navigate = (_data, _title, url) => {
    const target = new URL(url, 'http://fixture');
    Object.assign(location, {pathname:target.pathname, search:target.search});
  };
  const window = new EventTarget();
  window.scrollTo = () => {};
  vm.runInNewContext(script, {
    document: {
      getElementById: id => elements[id] || null,
      querySelector: selector => selector.startsWith('meta') ? {content:'csrf'} : new Element('div'),
      querySelectorAll: () => [],
      createElement: tag => { const node = new Element(tag); if (tag === 'img') images.push(node); return node; },
      createDocumentFragment: () => new Element('fragment'),
    },
    window, location, history:{pushState:navigate, replaceState:navigate},
    localStorage:{getItem:() => null, setItem() {}}, matchMedia:() => ({matches:false}),
    URLSearchParams, AbortController, Date: class extends Date { static now() { return now; } },
    setTimeout: (callback, delay) => {
      const id = ++timerSequence;
      const run = () => { timers.delete(id); callback(); };
      run.at = now + delay; timers.set(id, run); return id;
    },
    clearTimeout: id => timers.delete(id),
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
      observe(target) { this.targets.push(target); }
      unobserve() {}
      disconnect() {}
    },
    fetch: async (url, options) => {
      if (url.startsWith('/api/index/')) return {ok:true, json:async () => ({crawls:[]})};
      if (url.startsWith('/ready/')) {
        return new Promise((resolve, reject) => {
          checks.push({signal:options.signal, respond:ready => resolve({ok:true, json:async () => ({ready})})});
          options.signal?.addEventListener('abort', () => reject(Object.assign(new Error(), {name:'AbortError'})), {once:true});
        });
      }
      const page = Number(new URL(url, 'http://fixture').searchParams.get('page'));
      return {ok:true, json:async () => ({page, pages:3, total:206, state:'ready', items:
        Array.from({length:6}, (_, i) => ({name:`Page ${page} clip ${i}`, kind:'video', url:'/video',
          size:10, thumb_url:`/thumbnail/${page}/${i}`, ready_url:`/ready/${page}/${i}`})), ...resultOverrides})};
    },
  });
  return {elements, images, checks, observers, timers, window,
    async advance(milliseconds) {
      const end = now + milliseconds;
      while (true) {
        const next = [...timers.values()].filter(run => run.at <= end).sort((a, b) => a.at - b.at)[0];
        if (!next) break;
        now = next.at; next(); await flush();
      }
      now = end; await flush();
    },
    intersect() { const observer = observers.at(-1); observer.callback(observer.targets.map(target => ({target, isIntersecting:true}))); },
  };
}

test('empty indexing status reflects whether automatic indexing is enabled', async () => {
  const disabled = explorer(false), enabled = explorer(true);
  await flush();
  assert.equal(disabled.elements.indexStatus.textContent, 'Automatic indexing is disabled. Use Resume / refresh library index to start.');
  assert.equal(enabled.elements.indexStatus.textContent, 'Automatic library indexing is starting.');
});

test('visible thumbnails leave connections available for page navigation', async () => {
  const e = explorer();
  await flush();
  e.intersect();
  assert.equal(e.images.filter(image => image.src).length, 2);
  assert.equal(e.checks.length, 0, 'cached images must load without a readiness round trip');
  e.images[0].dispatchEvent(new Event('load'));
  await flush();
  assert.equal(e.images.filter(image => image.src).length, 3);
  e.images[1].dispatchEvent(new Event('error'));
  await flush();
  assert.equal(e.checks.length, 1, 'a missing image must fall back to readiness checks');
  e.checks[0].respond(false);
  await flush();
  assert.equal(e.images.filter(image => image.src).length, 3, 'waiting for generation must release its slot');
});

test('Next cancels old thumbnails and renders the next page immediately', async () => {
  const e = explorer();
  await flush();
  e.intersect();
  e.images.slice(0, 2).forEach(image => image.dispatchEvent(new Event('error')));
  await flush();
  const oldChecks = [...e.checks];
  assert.equal(oldChecks.length, 2);
  e.elements.nextPage.onclick();
  await flush();
  assert.equal(e.elements.pageLabel.textContent, 'Page 2 of 3');
  assert.ok(oldChecks.every(check => check.signal?.aborted), 'old requests must be aborted');
  assert.equal(e.checks.length, 2, 'old queued checks must not start');
  e.intersect();
  assert.equal(e.images.slice(6).filter(image => image.src).length, 2);
  e.window.dispatchEvent(new Event('pagehide'));
  assert.ok(e.images.every(image => !image.src));
  assert.ok(e.checks.every(check => check.signal.aborted));
});

test('navigation cancels image downloads and scheduled thumbnail retries', async () => {
  const e = explorer();
  await flush();
  e.intersect();
  e.images.slice(0, 2).forEach(image => image.dispatchEvent(new Event('error')));
  await flush();
  e.checks[0].respond(true);
  e.checks[1].respond(false);
  await flush();
  assert.ok(e.images[0].src);
  assert.equal(e.timers.size, 4); // Download/readiness timeouts plus the pending retry.
  e.elements.nextPage.onclick();
  await flush();
  assert.equal(e.images[0].src, undefined);
  const count = e.checks.length;
  for (const callback of e.timers.values()) callback();
  await flush();
  assert.equal(e.checks.length, count, 'retry timers from the previous page must not make requests');
});

test('missing thumbnails appear after generation and stop polling', async () => {
  const e = explorer();
  await flush();
  e.intersect();
  e.images[0].dispatchEvent(new Event('error'));
  await flush();
  assert.equal(e.images[0].hidden, true, 'failed images must keep the placeholder');
  e.checks[0].respond(false);
  await flush();
  assert.equal(e.timers.size, 3); // Two active downloads and one generation retry.
  e.images[1].dispatchEvent(new Event('load'));
  e.images[2].dispatchEvent(new Event('load'));
  await flush();
  e.images[3].dispatchEvent(new Event('load'));
  e.images[4].dispatchEvent(new Event('load'));
  await flush();
  e.images[5].dispatchEvent(new Event('load'));
  await flush();
  const [id, retry] = [...e.timers][0];
  e.timers.delete(id); retry();
  await flush();
  e.checks[1].respond(true);
  await flush();
  e.images[0].dispatchEvent(new Event('load'));
  await flush();
  assert.equal(e.images[0].hidden, false);
  assert.equal(e.checks.length, 2);
  assert.equal(e.timers.size, 0);
});

test('search refresh keeps loaded images while adding matches, but reloads changed media', async () => {
  const rows = [{name:'First', kind:'image', url:'/image', size:10, mtime:1, thumb_url:'/thumbnail/first', ready_url:'/ready/first'}];
  const result = {items:rows, refreshing:true, version:'first'};
  const e = explorer(undefined, result);
  await flush(); e.intersect();
  e.images[0].dispatchEvent(new Event('load'));
  await flush();
  const first = e.images[0];
  rows.push({...rows[0], name:'Second', thumb_url:'/thumbnail/second', ready_url:'/ready/second'});
  result.version = 'second';
  await e.advance(1500);
  await flush(); e.intersect();
  assert.equal(e.images.length, 2, 'loaded images must be reused when new results arrive');
  assert.equal(e.elements.explorerItems.children[0].children[0].children[0].children[0], first);
  assert.equal(first.hidden, false);
  assert.equal(first.src, '/thumbnail/first?v=first', 'a directory version change must not reload unchanged media');
  rows[0].mtime = 2;
  await e.advance(1500);
  await flush();
  assert.notEqual(e.elements.explorerItems.children[0].children[0].children[0].children[0], first,
    'changed source metadata must invalidate a loaded image');
});

test('background refresh preserves pending downloads and generation checks without exceeding two slots', async () => {
  const rows = [{name:'First', kind:'image', url:'/image', size:10, mtime:1, thumb_url:'/thumbnail/first', ready_url:'/ready/first'}];
  const result = {items:rows, refreshing:true, version:'first'};
  const e = explorer(undefined, result);
  await flush(); e.intersect();
  const first = e.images[0];
  const oldObserver = e.observers.at(-1), oldPreview = oldObserver.targets[0];
  await e.advance(1500); e.intersect();
  assert.equal(e.images.length, 1, 'background refresh must reuse unfinished images');
  assert.ok(first.src, 'an in-flight download must survive refresh');
  assert.doesNotThrow(() => oldObserver.callback([{target:oldPreview, isIntersecting:true}]),
    'late observer entries must tolerate images moved by refresh');
  first.dispatchEvent(new Event('error')); await flush();
  const check = e.checks[0];
  rows.push(...Array.from({length:3}, (_, i) => ({...rows[0], name:`New ${i}`, thumb_url:`/thumbnail/new/${i}`, ready_url:`/ready/new/${i}`})));
  await e.advance(1500); e.intersect();
  assert.equal(check.signal.aborted, false, 'generation checks must survive refresh');
  assert.equal(e.images.filter(image => image.src && !image.complete).length, 1,
    'an active check leaves only one download slot across refreshes');
  check.respond(true); await flush();
  first.dispatchEvent(new Event('load')); await flush();
  assert.equal(first.hidden, false);
  e.elements.nextPage.onclick(); await flush();
  assert.ok(e.images.slice(1).every(image => !image.src), 'navigation must still cancel unfinished downloads');
  e.window.dispatchEvent(new Event('pagehide'));
});

test('a ready thumbnail retries a transient download failure until it loads', async () => {
  const e = explorer(undefined, {items:[{name:'First', kind:'image', url:'/image', size:10, thumb_url:'/thumbnail/first', ready_url:'/ready/first'}]});
  await flush(); e.intersect();
  e.images[0].dispatchEvent(new Event('error')); await flush();
  e.checks[0].respond(true); await flush();
  e.images[0].dispatchEvent(new Event('error')); await flush();
  await e.advance(1500);
  assert.equal(e.checks.length, 2, 'a failed download must retry even when generation is complete');
  e.checks[1].respond(true); await flush();
  e.images[0].dispatchEvent(new Event('load')); await flush();
  assert.equal(e.images[0].hidden, false);
  assert.equal(e.timers.size, 0);
});

test('stalled image downloads and readiness checks release thumbnail slots', async () => {
  const e = explorer();
  await flush(); e.intersect();
  await e.advance(10000);
  assert.ok(e.images.slice(0, 2).every(image => !image.src), 'timed out downloads must be cancelled');
  assert.equal(e.checks.length, 2);
  await e.advance(10000);
  assert.ok(e.checks.every(check => check.signal.aborted), 'stalled readiness requests must be cancelled');
  assert.ok(e.images[2].src && e.images[3].src, 'remaining thumbnails must get a turn');
  e.elements.nextPage.onclick(); await flush();
  assert.equal(e.elements.pageLabel.textContent, 'Page 2 of 3');
  e.window.dispatchEvent(new Event('pagehide'));
  assert.equal(e.timers.size, 0, 'navigation must clear request timeouts and retries');
});

test('thumbnail retries stop at the deadline even when every request stalls', async () => {
  const e = explorer();
  await flush(); e.intersect();
  await e.advance(240000);
  const checks = e.checks.length;
  assert.ok(checks > 0);
  assert.ok(e.images.every(image => !image.src));
  assert.ok(e.checks.every(check => check.signal.aborted));
  assert.equal(e.timers.size, 0);
  await e.advance(120000);
  assert.equal(e.checks.length, checks);
});

test('thumbnails waiting behind slow downloads get their own retry budget', async () => {
  const e = explorer(undefined, {items:Array.from({length:12}, (_, i) => ({
    name:`Clip ${i}`, kind:'video', url:'/video', size:10,
    thumb_url:`/thumbnail/${i}`, ready_url:`/ready/${i}`,
  }))});
  await flush(); e.intersect();
  await e.advance(100000);
  assert.ok(e.images[10].src && e.images[11].src,
    'queue wait time must not expire thumbnails before their first download');
  e.window.dispatchEvent(new Event('pagehide'));
  assert.equal(e.timers.size, 0);
});
