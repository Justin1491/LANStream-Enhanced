// Run with: node --test tests/player.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/player.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function player(direct = true, nativeHls = false) {
  const elements = {};
  for (const id of ['playerShell', 'video', 'playerMessage', 'playerControls', 'togglePlay',
    'back30', 'forward30', 'seek', 'currentLabel', 'volume', 'fullscreenTop']) {
    const classes = new Set();
    elements[id] = Object.assign(new EventTarget(), {
      paused: true,
      classList: {
        add: name => classes.add(name), remove: name => classes.delete(name),
        contains: name => classes.has(name),
        toggle: (name, active) => active ? classes.add(name) : classes.delete(name),
      },
      setAttribute(name, value) { this[name] = value; },
    });
  }
  const qualityModes = ['normal', 'low', 'hevc'].map(value => Object.assign(new EventTarget(), {value, checked:false}));
  const document = Object.assign(new EventTarget(), {
    getElementById: id => elements[id] || null,
    querySelectorAll: () => qualityModes,
  });
  Object.assign(elements.video, {readyState:1, querySelectorAll:() => [], load() {},
    canPlayType: type => nativeHls && type === 'application/vnd.apple.mpegurl' ? 'maybe' : ''});
  const window = Object.assign(new EventTarget(), {setTimeout, clearTimeout});
  const context = vm.createContext({document, window});
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

test('HEVC selection survives seeking and switches back to low bandwidth', () => {
  const p = player(false);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  p.video.currentTime = 12;
  const hevc = p.qualityModes.find(radio => radio.value === 'hevc');
  hevc.checked = true;
  hevc.dispatchEvent(new Event('change'));
  assert.match(p.video.src, /start=12&audio=&hevc=1$/);
  p.video.currentTime = 3;
  p.forward30.onclick();
  assert.match(p.video.src, /start=45&audio=&hevc=1$/);
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
  assert.match(p.video.src, /&low=1$/);
  assert.doesNotMatch(p.video.src, /hevc=1/);
});

test('native HLS uses a fresh stream for seeking and quality changes', () => {
  const p = player(false, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  assert.match(p.video.src, /\?stream=.*-1&start=0&audio=$/);
  p.video.currentTime = 3;
  p.forward30.onclick();
  assert.match(p.video.src, /\?stream=.*-2&start=33&audio=$/);
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
  assert.match(p.video.src, /\?stream=.*-3&start=36&audio=&low=1$/);
  const hevc = p.qualityModes.find(radio => radio.value === 'hevc');
  hevc.checked = true;
  hevc.dispatchEvent(new Event('change'));
  assert.match(p.video.src, /\?stream=.*-4&start=39&audio=&hevc=1$/);
});

test('native HLS support preserves direct playback for compatible files', () => {
  const p = player(true, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  assert.equal(p.video.src, '/fixture');
});

test('native HLS resumes from the paused position with a fresh stream', () => {
  const p = player(false, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  p.video.currentTime = 7;
  p.video.dispatchEvent(new Event('pause'));
  p.togglePlay.onclick();
  assert.match(p.video.src, /-2&start=7&audio=$/);
  p.video.currentTime = 2;
  p.video.dispatchEvent(new Event('pause'));
  p.video.dispatchEvent(new Event('play'));
  assert.match(p.video.src, /-3&start=9&audio=$/);
});

test('switching from paused HLS to direct playback clears its resume position', () => {
  const p = player(true, true);
  p.video.play = () => Promise.resolve();
  p.window.dispatchEvent(new Event('DOMContentLoaded'));
  const low = p.qualityModes.find(radio => radio.value === 'low');
  low.checked = true;
  low.dispatchEvent(new Event('change'));
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
