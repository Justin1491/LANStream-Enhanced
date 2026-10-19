// Run with: node --test tests/explorer.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/explorer.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function explorer() {
  const images = [], checks = [], observers = [], timers = new Map();
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
    append(...children) { this.children.push(...children); }
    replaceChildren(fragment) { this.children = fragment.children; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
    querySelector(tag) { return this.children.find(child => child.tagName === tag); }
  }
  const elements = Object.fromEntries(['explorer', 'explorerItems', 'explorerStatus',
    'explorerQuery', 'explorerSort', 'fileType', 'gridDensity', 'previousPage',
    'nextPage', 'densityControl', 'pageLabel', 'coverage'].map(id => [id, new Element('div')]));
  elements.explorer.dataset = {api:'/api/browse/0/', mode:'browse', root:'0', relative:''};
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
    URLSearchParams, AbortController,
    setTimeout: callback => { const id = timers.size + 1; timers.set(id, callback); return id; },
    clearTimeout: id => timers.delete(id),
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; this.targets = []; observers.push(this); }
      observe(target) { this.targets.push(target); }
      unobserve() {}
      disconnect() {}
    },
    fetch: async (url, options) => {
      if (url.startsWith('/ready/')) {
        return new Promise((resolve, reject) => {
          checks.push({signal:options.signal, respond:ready => resolve({ok:true, json:async () => ({ready})})});
          options.signal?.addEventListener('abort', () => reject(Object.assign(new Error(), {name:'AbortError'})), {once:true});
        });
      }
      const page = Number(new URL(url, 'http://fixture').searchParams.get('page'));
      return {ok:true, json:async () => ({page, pages:3, total:206, state:'ready', items:
        Array.from({length:6}, (_, i) => ({name:`Page ${page} clip ${i}`, kind:'video', url:'/video',
          size:10, thumb_url:`/thumbnail/${page}/${i}`, ready_url:`/ready/${page}/${i}`}))})};
    },
  });
  return {elements, images, checks, observers, timers, window,
    intersect() { const observer = observers.at(-1); observer.callback(observer.targets.map(target => ({target, isIntersecting:true}))); },
  };
}

test('visible thumbnails leave connections available for page navigation', async () => {
  const e = explorer();
  await flush();
  e.intersect();
  assert.equal(e.checks.length, 2, 'only two thumbnail requests may be in flight');
  e.checks.forEach(check => check.respond(true));
  await flush();
  assert.equal(e.images.filter(image => image.src).length, 2);
  assert.equal(e.checks.length, 2, 'image downloads must finish before starting more checks');
  e.images[0].dispatchEvent(new Event('load'));
  await flush();
  assert.equal(e.checks.length, 3);
  e.images[1].dispatchEvent(new Event('error'));
  await flush();
  assert.equal(e.checks.length, 4, 'a failed image must release its slot');
});

test('Next cancels old thumbnails and renders the next page immediately', async () => {
  const e = explorer();
  await flush();
  e.intersect();
  const oldChecks = [...e.checks];
  e.elements.nextPage.onclick();
  await flush();
  assert.equal(e.elements.pageLabel.textContent, 'Page 2 of 3');
  assert.ok(oldChecks.every(check => check.signal?.aborted), 'old requests must be aborted');
  assert.equal(e.checks.length, 2, 'old queued checks must not start');
  e.intersect();
  assert.equal(e.checks.length, 4);
  e.window.dispatchEvent(new Event('pagehide'));
  assert.ok(e.checks.every(check => check.signal.aborted));
});

test('navigation cancels image downloads and scheduled thumbnail retries', async () => {
  const e = explorer();
  await flush();
  e.intersect();
  e.checks[0].respond(true);
  e.checks[1].respond(false);
  await flush();
  assert.ok(e.images[0].src);
  assert.equal(e.timers.size, 1);
  e.elements.nextPage.onclick();
  await flush();
  assert.equal(e.images[0].src, undefined);
  const count = e.checks.length;
  for (const callback of e.timers.values()) callback();
  await flush();
  assert.equal(e.checks.length, count, 'retry timers from the previous page must not make requests');
});
