// Run with: node --test tests/player.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/player.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function player(direct = true, nativeHls = false, fetcher = async () => ({ok:true, json:async () => ({state:'ready'})})) {
  const elements = {};
  for (const id of ['playerShell', 'video', 'playerMessage', 'playerControls', 'togglePlay',
    'back30', 'forward30', 'seek', 'currentLabel', 'volume', 'fullscreenTop',
    'playbackDiagnostics', 'playbackReport']) {
    const classes = new Set();
    elements[id] = Object.assign(new EventTarget(), {
      paused: true,
      classList: {
        add: name => classes.add(name), remove: name => classes.delete(name),
        contains: name => classes.has(name),
        toggle: (name, active) => active ? classes.add(name) : classes.delete(name),
      },
      setAttribute(name, value) { this[name] = value; },
      removeAttribute(name) { delete this[name]; },
    });
  }
  const qualityModes = ['normal', 'low'].map(value => Object.assign(new EventTarget(), {value, checked:false}));
  const document = Object.assign(new EventTarget(), {
    getElementById: id => elements[id] || null,
    querySelectorAll: () => qualityModes,
  });
  Object.assign(elements.video, {readyState:1, querySelectorAll:() => [], load() {},
    canPlayType: type => nativeHls && type === 'application/vnd.apple.mpegurl' ? 'maybe' : ''});
  const window = Object.assign(new EventTarget(), {setTimeout, clearTimeout});
  const context = vm.createContext({document, window, fetch:fetcher, AbortController});
  vm.runInContext(script.replace('{{ "true" if native else "false" }}', String(direct))
    .replace('{{ duration }}', '60').replace(/{{.*?}}/g, '"/fixture"'), context);
  return {document, window, qualityModes, ...elements};
}

test('iPhone Safari enters and exits native video fullscreen and updates the button', async () => {
  const p = player();
  let entries = 0, exits = 0;
  p.video.webkitEnterFullscreen = () => {
    entries++;
    p.video.webkitDisplayingFullscreen = true;
    p.video.dispatchEvent(new Event('webkitbeginfullscreen'));
  };
  p.video.webkitExitFullscreen = () => {
    exits++;
    p.video.webkitDisplayingFullscreen = false;
    p.video.dispatchEvent(new Event('webkitendfullscreen'));
  };
  await p.fullscreenTop.onclick();
  assert.equal(entries, 1, 'the button must call Safari video fullscreen');
  assert.equal(p.fullscreenTop['aria-label'], 'Exit fullscreen');
  await p.fullscreenTop.onclick();
  assert.equal(exits, 1);
  assert.equal(p.fullscreenTop['aria-label'], 'Enter fullscreen');
  await p.fullscreenTop.onclick();
  // Safari's Done button exits without clicking the page button.
  p.video.webkitDisplayingFullscreen = false;
  p.video.dispatchEvent(new Event('webkitendfullscreen'));
  assert.equal(p.fullscreenTop.textContent, 'Fullscreen');
  assert.equal(p.playerShell.classList.contains('is-fullscreen'), false);
});

for (const prefix of ['', 'webkit']) {
  test(`${prefix || 'standard'} page fullscreen still takes priority over native video`, async () => {
    const p = player();
    const request = prefix ? 'webkitRequestFullscreen' : 'requestFullscreen';
    const exit = prefix ? 'webkitExitFullscreen' : 'exitFullscreen';
    const state = prefix ? 'webkitFullscreenElement' : 'fullscreenElement';
    const event = prefix ? 'webkitfullscreenchange' : 'fullscreenchange';
    p.video.webkitEnterFullscreen = () => assert.fail('page fullscreen should be preferred');
    p.playerShell[request] = () => {
      p.document[state] = p.playerShell;
      p.document.dispatchEvent(new Event(event));
    };
    p.document[exit] = () => {
      p.document[state] = null;
      p.document.dispatchEvent(new Event(event));
    };
    await p.fullscreenTop.onclick();
    assert.equal(p.document[state], p.playerShell);
    assert.equal(p.fullscreenTop['aria-label'], 'Exit fullscreen');
    await p.fullscreenTop.onclick();
    assert.equal(p.document[state], null);
    assert.equal(p.fullscreenTop['aria-label'], 'Enter fullscreen');
  });

  test(`disabled ${prefix || 'standard'} page fullscreen uses native video`, async () => {
    const p = player();
    p.document[prefix ? 'webkitFullscreenEnabled' : 'fullscreenEnabled'] = false;
    p.playerShell[prefix ? 'webkitRequestFullscreen' : 'requestFullscreen'] = () => assert.fail('API is disabled');
    let entered = false;
    p.video.webkitEnterFullscreen = () => { entered = true; };
    await p.fullscreenTop.onclick();
    assert.equal(entered, true);
  });
}

test('unavailable fullscreen explains the problem', async () => {
  const p = player();
  await p.fullscreenTop.onclick();
  assert.match(p.playerMessage.textContent || '', /fullscreen.*not supported/i);
  assert.equal(p.playerMessage.classList.contains('hidden'), false);
});

for (const api of ['requestFullscreen', 'webkitEnterFullscreen']) {
  test(`${api} failure gives visible guidance`, async () => {
    const p = player();
    if (api === 'requestFullscreen') {
      p.playerShell[api] = () => Promise.reject(new Error('Denied'));
    } else {
      p.video[api] = () => { throw new Error('Metadata not loaded'); };
    }
    await p.fullscreenTop.onclick();
    assert.match(p.playerMessage.textContent || '', /fullscreen.*playback.*try again/i);
    assert.equal(p.playerMessage.classList.contains('hidden'), false);
  });
}

for (const direct of [true, false]) {
  test(`${direct ? 'direct' : 'transcoded'} autoplay denial prompts for Play and recovers on tap`, async () => {
    const p = player(direct);
    p.video.play = () => Promise.reject(Object.assign(new Error('Gesture required'), {name:'NotAllowedError'}));
    p.window.dispatchEvent(new Event('DOMContentLoaded'));
    await new Promise(setImmediate);
    assert.equal(p.playerMessage.textContent, 'Tap Play to start playback.');
    assert.equal(p.playerMessage.classList.contains('hidden'), false);
    p.video.dispatchEvent(new Event('waiting'));
    assert.equal(p.playerMessage.textContent, 'Tap Play to start playback.', 'paused video must not overwrite the prompt with Buffering');

    p.video.play = () => {
      p.video.paused = false;
      p.video.dispatchEvent(new Event('playing'));
      return Promise.resolve();
    };
    p.togglePlay.onclick();
    await new Promise(setImmediate);
    assert.equal(p.playerMessage.classList.contains('hidden'), true);
    assert.equal(p.togglePlay.textContent, 'Pause');
    p.video.dispatchEvent(new Event('waiting'));
    assert.equal(p.playerMessage.textContent, 'Buffering…', 'active playback should still report buffering');
  });
}

test('paused video does not report buffering and media failures retain their error', async () => {
  const p = player();
  p.video.play = () => Promise.reject(Object.assign(new Error('Load replaced'), {name:'AbortError'}));
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  await new Promise(setImmediate);
  p.video.dispatchEvent(new Event('waiting'));
  assert.notEqual(p.playerMessage.textContent, 'Buffering…');
  p.video.error = {code:4};
  p.video.dispatchEvent(new Event('error'));
  p.video.dispatchEvent(new Event('waiting'));
  assert.match(p.playerMessage.textContent, /Playback failed \(browser error 4\)/);
});

test('Normal and Low Bandwidth preserve position when switching and seeking', () => {
  const p = player(false);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  p.video.currentTime = 12;
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
  assert.match(p.video.src, /start=12&audio=&low=1$/);
  p.video.currentTime = 3;
  p.forward30.onclick();
  assert.match(p.video.src, /start=45&audio=&low=1$/);
  const normal = p.qualityModes.find(radio => radio.value === 'normal');
  normal.checked = true;
  normal.dispatchEvent(new Event('change'));
  assert.match(p.video.src, /start=48&audio=$/);
});

test('native HLS uses a fresh stream for seeking and quality changes', async () => {
  const p = player(false, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  await new Promise(setImmediate);
  assert.match(p.video.src, /\?stream=.*-1&start=0&audio=$/);
  p.video.currentTime = 3;
  p.forward30.onclick();
  await new Promise(setImmediate);
  assert.match(p.video.src, /\?stream=.*-2&start=33&audio=$/);
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
  await new Promise(setImmediate);
  assert.match(p.video.src, /\?stream=.*-3&start=36&audio=&low=1$/);
  const normal = p.qualityModes.find(radio => radio.value === 'normal');
  normal.checked = true;
  normal.dispatchEvent(new Event('change'));
  await new Promise(setImmediate);
  assert.match(p.video.src, /\?stream=.*-4&start=39&audio=$/);
});

test('native HLS support preserves direct playback for compatible files', () => {
  const p = player(true, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  assert.equal(p.video.src, '/fixture');
});

test('native HLS resumes from the paused position with a fresh stream', async () => {
  const p = player(false, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  await new Promise(setImmediate);
  p.video.currentTime = 7;
  p.video.dispatchEvent(new Event('pause'));
  p.togglePlay.onclick();
  await new Promise(setImmediate);
  assert.match(p.video.src, /-2&start=7&audio=$/);
  p.video.currentTime = 2;
  p.video.dispatchEvent(new Event('pause'));
  p.video.dispatchEvent(new Event('play'));
  await new Promise(setImmediate);
  assert.match(p.video.src, /-3&start=9&audio=$/);
});

test('switching from paused HLS to direct playback clears its resume position', async () => {
  const p = player(true, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
  await new Promise(setImmediate);
  p.video.currentTime = 7;
  p.video.dispatchEvent(new Event('pause'));
  const normal = p.qualityModes.find(radio => radio.value === 'normal');
  normal.checked = true;
  normal.dispatchEvent(new Event('change'));
  p.video.currentTime = 9;
  p.video.dispatchEvent(new Event('play'));
  assert.equal(p.video.src, '/fixture');
  assert.equal(p.video.currentTime, 9);
});

test('HLS waits through server startup before asking Safari to load the playlist', async () => {
  const requests = [];
  const p = player(false, true, async url => {
    requests.push(url);
    return {ok:true, json:async () => requests.length === 1
      ? {state:'starting', phase:'first_segment', progress:{frames:12}}
      : {state:'ready'}};
  });
  let poll, plays = 0;
  p.window.setTimeout = callback => { poll = callback; return 1; };
  p.video.pause = () => {};
  p.video.play = () => { plays++; return Promise.resolve(); };
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  await new Promise(setImmediate);
  assert.equal(p.video.src, undefined, 'Safari must not receive an unpublished playlist');
  assert.equal(plays, 0);
  assert.match(requests[0], /&prepare=1$/);
  assert.match(p.playbackReport.textContent, /first_segment/);
  poll();
  await new Promise(setImmediate);
  assert.equal(plays, 1);
  assert.match(p.video.src, /-1&start=0&audio=$/);
  assert.equal(requests.length, 2);
});

test('failed HLS startup shows the server failure without loading failed media', async () => {
  const p = player(false, true, async () => ({ok:false, json:async () => ({
    state:'failed', phase:'encoder_exit', backend:'qsv', exit_code:3,
  })}));
  p.video.pause = () => {};
  p.video.play = () => assert.fail('A failed encoder cannot produce playable media');
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  await new Promise(setImmediate);
  assert.equal(p.video.src, undefined);
  assert.match(p.playerMessage.textContent, /server.*encoder.*failed/i);
  assert.match(p.playbackReport.textContent, /"exit_code": 3/);
  assert.equal(p.playbackDiagnostics.hidden, false);
});

for (const stalled of ['request', 'response body']) {
  test(`HLS preparation aborts a stalled ${stalled} at its deadline and Play can retry`, async () => {
    let requests = 0, signal;
    const p = player(false, true, async (url, options) => {
      requests++;
      if (requests > 1) return {ok:true, json:async () => ({state:'ready'})};
      signal = options.signal;
      const pending = new Promise((resolve, reject) => {
        signal?.addEventListener('abort', () => reject(new Error('Aborted')), {once:true});
      });
      return stalled === 'request' ? pending : {ok:true, json:() => pending};
    });
    let expire;
    const cleared = [];
    p.window.setTimeout = (callback, delay) => {
      assert.equal(delay, 90000);
      expire = callback;
      return 42;
    };
    p.window.clearTimeout = timer => cleared.push(timer);
    p.video.pause = () => {};
    p.video.play = () => Promise.resolve();
    p.window.dispatchEvent(new Event('DOMContentLoaded'));
    await new Promise(setImmediate);
    assert.equal(typeof expire, 'function', 'Preparation must arm a deadline before awaiting the network');
    expire();
    await new Promise(setImmediate);
    assert.equal(signal.aborted, true);
    assert.equal(p.video.src, undefined);
    assert.match(p.playerMessage.textContent, /after 90 seconds/);
    assert.deepEqual(cleared, [42]);
    p.togglePlay.onclick();
    await new Promise(setImmediate);
    assert.equal(requests, 2, 'Play must start a new preparation after timeout');
    assert.match(p.video.src, /-2&start=0&audio=$/);
    assert.deepEqual(cleared, [42, 42], 'Successful preparation also clears its timer');
  });
}

test('an expired old HLS preparation cannot interrupt a newer preparation', async () => {
  let release;
  const p = player(false, true, (url, {signal} = {}) => url.includes('-1&')
    ? new Promise((resolve, reject) => signal?.addEventListener('abort', () => reject(new Error('Aborted'))))
    : new Promise(resolve => { release = resolve; }));
  const deadlines = [];
  p.window.setTimeout = callback => { deadlines.push(callback); return deadlines.length; };
  p.window.clearTimeout = () => {};
  p.video.pause = () => {};
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
  const message = p.playerMessage.textContent;
  assert.equal(deadlines.length, 2);
  deadlines[0]();
  await new Promise(setImmediate);
  assert.equal(p.playerMessage.textContent, message);
  release({ok:true, json:async () => ({state:'ready'})});
  await new Promise(setImmediate);
  assert.match(p.video.src, /-2&start=0&audio=&low=1$/);
});

test('late readiness for a replaced generation cannot restart old playback', async () => {
  let release;
  const p = player(false, true, url => url.includes('-1&')
    ? new Promise(resolve => { release = resolve; })
    : Promise.resolve({ok:true, json:async () => ({state:'ready'})}));
  p.video.pause = () => {};
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
  await new Promise(setImmediate);
  assert.match(p.video.src, /-2&start=0&audio=&low=1$/);
  assert.equal(typeof release, 'function');
  release({ok:true, json:async () => ({state:'ready'})});
  await new Promise(setImmediate);
  assert.match(p.video.src, /-2&start=0&audio=&low=1$/);
});

test('an HLS autoplay denial is retried inside the Play gesture without another preparation', async () => {
  let requests=0, plays=0;
  const p=player(false,true,async()=>{requests++;return {ok:true,json:async()=>({state:'ready'})};});
  p.video.pause=()=>{};
  p.video.play=()=>{
    if(++plays===1)return Promise.reject(Object.assign(new Error('Gesture required'),{name:'NotAllowedError'}));
    return Promise.resolve();
  };
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  await new Promise(setImmediate);
  assert.equal(p.playerMessage.textContent,'Tap Play to start playback.');
  p.togglePlay.onclick();
  assert.equal(plays,2,'The click itself must call play, preserving the Safari user gesture');
  await new Promise(setImmediate);
  assert.equal(requests,1,'Do not replace the ready stream to retry autoplay');
});
