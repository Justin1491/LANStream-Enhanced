const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../static/activity.js'), 'utf8');

function activity() {
  const document = Object.assign(new EventTarget(), {hidden:false,
    querySelector: () => ({content:'csrf'}), querySelectorAll: () => videos});
  const window = new EventTarget(), videos = [], calls = [], intervals = [];
  let now = 0;
  vm.runInNewContext(script, {document, window, Date:{now:()=>now},
    setInterval: fn => intervals.push(fn), clearInterval: () => {},
    fetch: (url, options) => { calls.push({url, options}); return Promise.resolve({ok:true}); }});
  return {document, window, videos, calls, tick(ms) { now += ms; intervals.forEach(fn => fn()); }};
}

test('reading and scrolling count as use, hidden idle pages stop reporting', () => {
  const a = activity();
  assert.equal(a.calls.length, 1);
  a.tick(20000);
  assert.equal(a.calls.length, 2, 'visible reading remains active');
  a.document.hidden = true;
  a.tick(120000);
  assert.equal(a.calls.length, 2);
  a.document.hidden = false;
  a.document.dispatchEvent(new Event('scroll'));
  assert.equal(a.calls.length, 3);
  assert.equal(a.calls[0].options.headers['X-LANStream-CSRF'], 'csrf');
});

test('buffered video keeps reporting even in a hidden tab', () => {
  const a = activity();
  a.document.hidden = true;
  a.videos.push({paused:false, ended:false});
  a.tick(20000);
  assert.equal(a.calls.length, 2);
  a.videos[0].paused = true;
  a.tick(20000);
  assert.equal(a.calls.length, 2);
});
