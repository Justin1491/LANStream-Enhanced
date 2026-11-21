const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/vr180.js'), 'utf8');

function player({ supported = true, rejectSession = false, mode = 'equirect', mediaLayers = true } = {}) {
  const video = Object.assign(new EventTarget(), {
    dataset: { frameRate: '60000/1001', src: '/media/sample.mp4' },
    duration: 100, currentTime: 20, controls: false, paused: true,
    hasAttribute(name) { return name === 'src' && !!this.src; },
    async play() { this.paused = false; },
    pause() { this.paused = true; },
  });
  const projection = Object.assign(new EventTarget(), { value: mode });
  const stage = { hidden: true };
  const enter = Object.assign(new EventTarget(), { hidden: true });
  const hands = { checked: true };
  const handsChoice = { hidden: true };
  const status = { textContent: 'Fullscreen hint' };
  const space = {};
  const right = { targetRayMode: 'tracked-pointer', handedness: 'right', targetRaySpace: {} };
  const left = { targetRayMode: 'tracked-pointer', handedness: 'left', targetRaySpace: {} };
  const hand = { profiles: ['generic-hand-select'], targetRaySpace: {} };
  const overlayLayer = {};
  const labels = [], icons = [], draws = [], uploads = [];
  let rate, nextFrame, time = 100, closed = false, lastUsed = 100;
  const active = Object.assign(new EventTarget(), {
    inputSources: [left, right, hand], maxRenderLayers: 2,
    supportedFrameRates: new Float32Array([72, 90, 120]), frameRate: 90,
    enabledFeatures: ['layers', 'hand-tracking'],
    async updateTargetFrameRate(value) { rate = value; },
    async requestReferenceSpace() { return space; },
    updateRenderState(state) { this.renderState = state; },
    requestAnimationFrame(callback) { nextFrame = callback; },
    async end() { this.dispatchEvent(new Event('end')); },
  });
  const paint = {
    clearRect() { labels.length = icons.length = 0; },
    fillText(text) { labels.push(text); },
    fill(path) { if (path) icons.push(path); },
    stroke(path) { if (path) icons.push(path); },
    beginPath() {}, arc() {}, roundRect() {}, moveTo() {}, lineTo() {},
    scale() {}, translate() {}, rotate() {}, save() {}, restore() {},
    createLinearGradient() { return { addColorStop() {} }; },
  };
  vm.runInNewContext(script, {
    navigator: { xr: {
      isSessionSupported: async () => supported,
      requestSession: async (_, options) => {
        assert.deepEqual(Array.from(options.requiredFeatures), ['layers']);
        assert.deepEqual(Array.from(options.optionalFeatures), ['hand-tracking']);
        if (rejectSession) throw new Error('layers declined');
        return active;
      },
    } },
    document: {
      getElementById: id => ({ video, vrProjection: projection, vrVideoStage: stage, enterVr: enter, vrStatus: status, vrHands: hands, vrHandChoice: handsChoice }[id]),
      createElement: () => ({ getContext: kind => kind === '2d' ? paint : {} }),
    },
    performance: { now: () => time },
    XRMediaBinding: mediaLayers ? class {
      createEquirectLayer(element, init) {
        assert.equal(element, video);
        return init;
      }
    } : undefined,
    XRWebGLBinding: class {},
    Path2D: class { constructor(path) { this.path = path; } },
    createVrControllerLayer: (session, binding, gl, space, fisheyeVideo) => {
      assert.equal(fisheyeVideo, mode === 'fisheye' ? video : undefined);
      return {
        layer: overlayLayer,
        update: now => now - lastUsed < 3000,
        markActive: now => { lastUsed = now; },
        uploadPanel(canvas) { uploads.push([canvas.width, canvas.height]); },
        draw(_, options) { draws.push(options); },
        close() { closed = true; },
      };
    },
  });

  // Rotate and translate the viewer origin. Construct independent world-space input rays through HUD coordinates.
  const c = Math.cos(0.35), s = Math.sin(0.35), tx = 1.1, ty = 1.4, tz = -0.3;
  const panelMatrix = [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, tx, ty, tz, 1];
  const inverse = [c, 0, s, 0, 0, 1, 0, 0, -s, 0, c, 0, -c * tx + s * tz, -ty, -s * tx - c * tz, 1];
  const aim = new Map([[right.targetRaySpace, [282, 86]], [left.targetRaySpace, [80, 86]], [hand.targetRaySpace, [181, 86]]]);
  const currentRays = new Map();
  const zDirection = Math.sqrt(0.71);
  const distance = 1.4 / zDirection;
  let viewerTracked = true;
  const xrFrame = {
    getViewerPose: () => viewerTracked ? ({ transform: { matrix: panelMatrix, inverse: { matrix: inverse } } }) : null,
    getPose(targetSpace, referenceSpace) {
      assert.equal(referenceSpace, space);
      if (!aim.has(targetSpace)) return null;
      const [x, y] = aim.get(targetSpace);
      const localX = (x / 640 - 0.5) * 0.71 + distance * 0.5;
      const localY = -0.30 + (0.5 - y / 128) * 0.142 + distance * 0.2;
      const matrix = [];
      matrix[8] = c * 0.5 + s * zDirection;
      matrix[9] = 0.2;
      matrix[10] = -s * 0.5 + c * zDirection;
      matrix[12] = tx + c * localX + s * -0.25;
      matrix[13] = ty + localY;
      matrix[14] = tz - s * localX + c * -0.25;
      currentRays.set(targetSpace, matrix);
      return { transform: { matrix } };
    },
  };

  return { video, stage, enter, hands, handsChoice, status, active, overlayLayer, panelMatrix, distance,
    labels, icons, draws, uploads, currentRays, left, right, hand, aim,
    get rate() { return rate; }, get closed() { return closed; },
    ready: () => new Promise(resolve => setImmediate(resolve)),
    select(type, x, y = 86, source = right) {
      aim.set(source.targetRaySpace, [x, y]);
      active.dispatchEvent(Object.assign(new Event(type), { inputSource: source, frame: xrFrame }));
    },
    trackViewer(value) { viewerTracked = value; },
    frame(now) { time = now; nextFrame(now, xrFrame); },
  };
}

test('VR180 issues one overlay with an icon panel, a shared ray and hit, and a release-to-seek timeline', async () => {
  const fixture = player();
  const { video, stage, enter, hands, handsChoice, active, overlayLayer, labels, icons,
    draws, uploads, left, right, hand, aim, select, frame } = fixture;
  await fixture.ready();
  assert.equal(enter.hidden, false);
  assert.equal(handsChoice.hidden, false);
  assert.equal(hands.checked, true);
  hands.checked = false; // Start with controller-only input, then enable hands from the panel.
  assert.equal(video.src, undefined, 'Quest native playback has no source before XR entry');
  assert.equal(stage.hidden, true);
  enter.dispatchEvent(new Event('click'));
  await fixture.ready();
  assert.equal(fixture.rate, 120);
  assert.equal(active.renderState.layers[0].layout, 'stereo-left-right');
  assert.equal(video.src, '/media/sample.mp4');
  assert.equal(video.controls, false);
  assert.equal(stage.hidden, true);

  frame(100);
  assert.equal(active.renderState.layers.length, 2);
  assert.equal(active.renderState.layers[1], overlayLayer);
  assert.equal(draws[0].panel.matrix, fixture.panelMatrix);
  assert.equal(icons.length, 4);
  assert.deepEqual(labels, ['00:20', '01:40', '15', '15', 'Hands off']);
  assert.deepEqual(uploads, [[1280, 256]]);
  assert.equal(draws[0].beam.matrix, fixture.currentRays.get(right.targetRaySpace));
  assert.ok(Math.abs(draws[0].beam.distance - fixture.distance) < 1e-9);
  assert.ok(Math.abs(draws[0].cursor.x - (282 / 640 - 0.5) * 0.71) < 1e-9);
  assert.ok(Math.abs(draws[0].cursor.y - (0.5 - 86 / 128) * 0.142) < 1e-9);

  aim.set(right.targetRaySpace, [290, 90]);
  frame(110);
  assert.equal(uploads.length, 1, 'moving within a control reuses the HUD texture');
  assert.ok(draws[1].cursor.x > draws[0].cursor.x);
  select('selectstart', 282, 86, hand);
  assert.equal(video.currentTime, 20, 'a muted hand cannot activate a control');
  select('selectstart', 282);
  assert.equal(video.currentTime, 35);
  select('selectstart', 80, 86, left);
  frame(120);
  assert.equal(draws.at(-1).beam.matrix, fixture.currentRays.get(left.targetRaySpace));
  assert.equal(video.currentTime, 20);
  select('selectstart', 181);
  assert.equal(video.paused, true);
  const low = 97 + (544 - 97) * 0.25;
  const high = 97 + (544 - 97) * 0.75;
  select('selectstart', low, 31);
  aim.set(right.targetRaySpace, [high, 31]);
  frame(200);
  assert.equal(video.currentTime, 20, 'dragging previews without a new media range');
  assert.ok(Math.abs(draws.at(-1).cursor.x - (high / 640 - 0.5) * 0.71) < 1e-9);
  select('select', high, 31);
  select('selectend', high, 31);
  assert.ok(Math.abs(video.currentTime - 75) < 1e-9);
  const seekPosition = video.currentTime;
  select('selectstart', low, 31);
  select('selectend', low, 31);
  assert.equal(video.currentTime, seekPosition, 'cancelling a scrub leaves playback position alone');

  frame(3200);
  assert.equal(draws.at(-1).showControllers, false);
  assert.ok(draws.at(-1).panel);
  assert.equal(draws.at(-1).beam, undefined);
  frame(5200);
  assert.equal(active.renderState.layers.length, 1);
  select('selectstart', 181, 86, hand);
  frame(5300);
  assert.equal(active.renderState.layers.length, 1, 'a muted hand cannot reopen the panel');
  select('selectstart', 537);
  frame(5400);
  assert.equal(active.renderState.layers[1], overlayLayer);
  select('selectstart', 537);
  frame(5500);
  assert.equal(hands.checked, true);
  assert.ok(draws.at(-1).beam, 'resting hands do not take over the pointer');
  select('selectstart', 181, 86, hand);
  frame(5600);
  assert.equal(video.paused, true, 'hand buttons wait for select');
  select('select', 181, 86, hand);
  assert.equal(video.paused, false);
  assert.deepEqual(draws.at(-1).beam.matrix, fixture.currentRays.get(hand.targetRaySpace));
  assert.ok(draws.at(-1).cursor, 'hand beam and dot reach the same control');
  select('selectend', 181, 86, hand);
  frame(5700);
  assert.ok(draws.at(-1).beam);
  select('selectstart', 537);
  assert.equal(hands.checked, false);
  select('selectstart', 402);
  assert.equal(stage.hidden, true);
  assert.equal(video.controls, false);
  assert.equal(enter.disabled, false);
  assert.equal(fixture.closed, true);
});

test('holding empty panel space moves the grabbed point, preserves controls, and remembers placement', async () => {
  const { active, enter, frame, select, ready, video, draws, aim, right, left, panelMatrix } = player();
  await ready();
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  select('selectstart', 350, 86);
  select('selectstart', 282, 86, left);
  assert.equal(video.currentTime, 20);
  aim.set(right.targetRaySpace, [500, 56]);
  frame(8000);
  const moved = draws.at(-1).panel.matrix;
  assert.ok(Math.abs(moved[12] - panelMatrix[12]) > 0.1);
  select('select', 500, 56);
  select('selectend', 500, 56);
  // Play now lies 150px right and 30px up from its original position.
  select('selectstart', 331, 56);
  assert.equal(video.paused, true);
  assert.equal(video.currentTime, 20);
  frame(15000);
  assert.equal(active.renderState.layers.length, 1);
  select('selectstart', 400, 86);
  frame(15100);
  assert.equal(draws.at(-1).panel.matrix, moved);
});

test('opted-in Quest hands aim before pinching, control playback, drag the panel and release a scrub', async () => {
  const { active, enter, frame, select, ready, video, hands, draws, aim, hand, panelMatrix } = player();
  hand.hand = new Map();
  hand.profiles = ['meta-hand-tracking'];
  hand.targetRayMode = 'tracked-pointer';
  active.inputSources = [hand];
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  assert.ok(draws.at(-1).cursor, 'hand target is visible before the first pinch');
  assert.ok(draws.at(-1).beam && draws.at(-1).handPointer);
  select('selectstart', 181, 86, hand);
  assert.equal(video.paused, false, 'starting a pinch does not press play');
  select('select', 181, 86, hand);
  select('selectend', 181, 86, hand);
  assert.equal(video.paused, true);
  frame(8100);
  assert.equal(active.renderState.layers.length, 1, 'resting hands let the panel hide');
  select('selectstart', 181, 86, hand);
  select('select', 181, 86, hand);
  select('selectend', 181, 86, hand);
  frame(8110);
  assert.equal(active.renderState.layers.length, 1, 'a quick hidden pinch does not reopen controls');
  select('selectstart', 181, 86, hand);
  frame(8810);
  select('select', 181, 86, hand);
  select('selectend', 181, 86, hand);
  frame(8820);
  assert.ok(draws.at(-1).panel, 'a held pinch reopens the panel');
  assert.equal(video.paused, true);
  select('selectstart', 97, 31, hand);
  select('select', 320, 31, hand);
  select('selectend', 320, 31, hand);
  assert.ok(Math.abs(video.currentTime - 50) < 1);
  select('selectstart', 350, 86, hand);
  aim.set(hand.targetRaySpace, [500, 56]);
  frame(8850);
  assert.ok(Math.abs(draws.at(-1).panel.matrix[12] - panelMatrix[12]) > 0.1);
  active.inputSources = [];
  active.dispatchEvent(Object.assign(new Event('inputsourceschange'), { removed: [hand] }));
  frame(15000);
  assert.ok(draws.at(-1).panel, 'a hand-opened panel stays visible after tracking is removed');
});

test('a steady opted-in pinch toggles once over buttons, timeline or blank space and stays open', async () => {
  for (const [x, y] of [[181, 86], [282, 86], [402, 86], [537, 86], [320, 31], [350, 86]]) {
    const { active, enter, frame, select, ready, hands, hand, draws, video, panelMatrix } = player();
    active.inputSources = [hand];
    await ready();
    hands.checked = true;
    enter.dispatchEvent(new Event('click'));
    await ready();
    frame(100);
    select('selectstart', x, y, hand);
    frame(799);
    assert.ok(draws.at(-1).panel, '699ms does not toggle');
    frame(800);
    assert.equal(draws.at(-1).panel, undefined, '700ms hides the panel');
    frame(10000);
    assert.equal(active.renderState.layers.length, 1, 'holding longer does not reopen');
    select('select', x, y, hand);
    select('selectend', x, y, hand);
    assert.equal(video.paused, false);
    assert.equal(video.currentTime, 20);
    assert.equal(hands.checked, true);
    assert.equal(enter.disabled, true, 'holding exit does not end the session');
    select('selectstart', 900, 200, hand);
    frame(10700);
    frame(10800);
    assert.ok(draws.at(-1).panel, 'a hold outside the panel opens controls');
    select('select', 900, 200, hand);
    select('selectend', 900, 200, hand);
    frame(30000);
    assert.ok(draws.at(-1).panel, 'hand-opened controls have no idle timeout');
    assert.equal(draws.at(-1).panel.matrix, panelMatrix, 'stationary blank-space hold does not move the panel');
  }
});

test('tracking loss, focus and a second held hand cannot complete an old toggle or click', async () => {
  const { active, enter, frame, select, ready, hands, hand, aim, video, draws } = player();
  const other = { profiles: ['generic-hand-select'], targetRaySpace: {} };
  active.inputSources = [hand, other];
  aim.set(other.targetRaySpace, [181, 86]);
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  select('selectstart', 181, 86, hand);
  frame(200);
  select('selectstart', 181, 86, other);
  frame(800);
  frame(1000);
  assert.equal(draws.at(-1).panel, undefined, 'second held hand does not undo first toggle');
  select('select', 181, 86, other);
  select('selectend', 181, 86, other);
  select('selectend', 181, 86, hand);
  select('selectstart', 181, 86, hand);
  aim.delete(hand.targetRaySpace);
  frame(1100);
  aim.set(hand.targetRaySpace, [181, 86]);
  frame(3000);
  select('select', 181, 86, hand);
  select('selectend', 181, 86, hand);
  assert.equal(draws.at(-1).panel, undefined, 'lost tracking cancels the pending hold');
  select('selectstart', 181, 86, hand);
  active.visibilityState = 'visible-blurred';
  active.dispatchEvent(new Event('visibilitychange'));
  active.visibilityState = 'visible';
  frame(5000);
  select('select', 181, 86, hand);
  select('selectend', 181, 86, hand);
  assert.equal(active.renderState.layers.length, 1, 'focus loss cancels the pending hold');
  assert.equal(video.paused, false, 'stale selects never press play');
});

test('off-panel movement still toggles, active hand drags are protected, and controller input restores timeout', async () => {
  const { active, enter, frame, select, ready, hands, hand, right, aim, draws, panelMatrix, video } = player();
  const other = { profiles: ['generic-hand-select'], targetRaySpace: {} };
  active.inputSources = [hand, other, right];
  aim.set(other.targetRaySpace, [900, 200]);
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  select('selectstart', 350, 86, hand);
  aim.set(hand.targetRaySpace, [500, 56]);
  frame(150);
  select('selectstart', 900, 200, other);
  frame(850);
  assert.ok(draws.at(-1).panel, 'a second hand hold does not cancel an active drag');
  assert.ok(Math.abs(draws.at(-1).panel.matrix[12] - panelMatrix[12]) > 0.1);
  select('selectend', 900, 200, other);
  select('selectend', 500, 56, hand);
  frame(6000);
  select('selectstart', 900, 200, hand);
  aim.set(hand.targetRaySpace, [1000, 220]);
  frame(6700);
  frame(6800);
  select('selectend', 1000, 220, hand);
  frame(15000);
  assert.ok(draws.at(-1).panel, 'off-panel jitter does not prevent a persistent menu');
  select('selectstart', 1100, 220, right);
  frame(20100);
  assert.equal(active.renderState.layers.length, 1, 'controller use restores the normal five-second timeout');
  assert.equal(video.currentTime, 20);
});

test('a hold near timeout hides once, and quick blank pinches do not pin placement', async () => {
  const { active, enter, frame, select, ready, hands, hand, draws, panelMatrix } = player();
  active.inputSources = [hand];
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  select('selectstart', 350, 86, hand);
  select('select', 350, 86, hand);
  select('selectend', 350, 86, hand);
  frame(4900);
  select('selectstart', 181, 86, hand);
  frame(5200);
  assert.ok(draws.at(-1).panel, 'auto-hide waits for the pending pinch');
  frame(5600);
  assert.equal(active.renderState.layers.length, 1, 'near-timeout hold hides instead of reopening');
  select('selectend', 181, 86, hand);
  panelMatrix[12] += 0.3;
  select('selectstart', 900, 200, hand);
  frame(6300);
  frame(6400);
  assert.equal(draws.at(-1).panel.matrix, panelMatrix, 'quick blank pinch did not mark a dragged placement');
});

test('a hand can open controls while waiting for the first viewer pose', async () => {
  const { active, enter, frame, select, ready, hands, hand, draws, trackViewer } = player();
  active.inputSources = [hand];
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  trackViewer(false);
  frame(100);
  select('selectstart', 900, 200, hand);
  frame(800);
  assert.equal(draws.at(-1).panel, undefined, 'no panel or inverse is used before viewer placement');
  select('selectend', 900, 200, hand);
  trackViewer(true);
  frame(900);
  assert.ok(draws.at(-1).panel);
  frame(9000);
  assert.ok(draws.at(-1).panel, 'deferred hand-opened controls stay open');
});

test('a concurrent controller trigger preserves a hand scrub through release', async () => {
  const { active, enter, frame, select, ready, hands, hand, right, aim, video } = player();
  active.inputSources = [hand, right];
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  select('selectstart', 97, 31, hand);
  aim.set(hand.targetRaySpace, [320, 31]);
  frame(200);
  select('selectstart', 181, 86, right);
  select('select', 420, 31, hand);
  select('selectend', 420, 31, hand);
  assert.ok(video.currentTime > 65, 'controller trigger did not invalidate the active hand scrub');
});

test('a late hand source can use the pre-entry opt-in even if the feature list omits hand tracking', async () => {
  const { active, enter, frame, select, ready, hands, right, hand, labels, video } = player();
  active.inputSources = [right];
  active.enabledFeatures = ['layers'];
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  assert.ok(labels.includes('Hands N/A'));
  active.inputSources = [hand];
  active.dispatchEvent(Object.assign(new Event('inputsourceschange'), { removed: [right] }));
  frame(120);
  assert.ok(labels.includes('Hands on'));
  select('selectstart', 181, 86, hand);
  assert.equal(video.paused, false, 'late hand also waits for select');
  select('select', 181, 86, hand);
  assert.equal(video.paused, true);
});

test('an opted-in hand ray follows the aim off the HUD and disappears with the HUD', async () => {
  const { active, enter, frame, ready, hands, hand, aim, draws, currentRays } = player({ mode: 'fisheye' });
  active.inputSources = [hand];
  await ready();
  hands.checked = true;
  enter.dispatchEvent(new Event('click'));
  await ready();
  aim.set(hand.targetRaySpace, [870, 200]);
  frame(100);
  const { beam, cursor, handPointer } = draws.at(-1);
  assert.equal(handPointer, true);
  assert.equal(cursor, undefined, 'no panel-local hit');
  assert.equal(beam.matrix, currentRays.get(hand.targetRaySpace));
  assert.equal(beam.distance, 1.65);
  active.visibilityState = 'visible-blurred';
  active.dispatchEvent(new Event('visibilitychange'));
  frame(300);
  assert.equal(draws.at(-1).beam, undefined);
  assert.equal(draws.at(-1).handPointer, undefined);
});

test('Quest system focus hides the LANStream overlay and cancels a pending scrub', async () => {
  const { active, enter, frame, select, ready, video, draws } = player();
  await ready();
  enter.dispatchEvent(new Event('click'));
  await ready();
  frame(100);
  select('selectstart', 190, 31);
  active.visibilityState = 'visible-blurred';
  active.dispatchEvent(new Event('visibilitychange'));
  frame(200);
  assert.equal(active.renderState.layers.length, 1);
  select('select', 520, 31);
  assert.equal(video.currentTime, 20);
  active.visibilityState = 'visible';
  active.dispatchEvent(new Event('visibilitychange'));
  frame(300);
  assert.equal(draws.at(-1).panel, undefined);
  select('selectstart', 190, 31);
  frame(400);
  assert.ok(draws.at(-1).panel);
});

test('native fullscreen is exposed alone when XR is unavailable or session startup fails', async () => {
  for (const options of [{ supported: false }, { rejectSession: true }]) {
    const fixture = player(options);
    await fixture.ready();
    if (options.rejectSession) {
      assert.equal(fixture.stage.hidden, true);
      fixture.enter.dispatchEvent(new Event('click'));
      await fixture.ready();
    }
    assert.equal(fixture.enter.hidden, true);
    assert.equal(fixture.handsChoice.hidden, true);
    assert.equal(fixture.stage.hidden, false);
    assert.equal(fixture.video.controls, true);
    assert.equal(fixture.video.src, '/media/sample.mp4');
    assert.match(fixture.status.textContent, /Fullscreen hint/);
  }
});

test('fisheye retains the stereo movie layer through HUD timeout, scrubbing and system focus', async () => {
  const { ready, enter, active, overlayLayer, frame, select, video, draws } = player({ mode: 'fisheye', mediaLayers: false });
  await ready();
  assert.equal(enter.hidden, false);
  enter.dispatchEvent(new Event('click'));
  await ready();
  assert.equal(active.renderState.layers.length, 1);
  assert.equal(active.renderState.layers[0], overlayLayer);
  frame(100);
  assert.ok(draws.at(-1).panel);
  select('selectstart', 320, 31);
  select('select', 320, 31);
  select('selectend', 320, 31);
  assert.ok(Math.abs(video.currentTime - 50) < 1);
  frame(8100);
  assert.equal(draws.at(-1).panel, undefined);
  assert.equal(draws.at(-1).showControllers, false);
  const drawsBeforeFocus = draws.length;
  active.visibilityState = 'visible-blurred';
  active.dispatchEvent(new Event('visibilitychange'));
  frame(8200);
  assert.equal(draws.length, drawsBeforeFocus + 1);
  assert.equal(draws.at(-1).showControllers, false);
  assert.equal(active.renderState.layers[0], overlayLayer);
});

test('unsupported immersive fisheye explains requirements and keeps the flat fallback hidden', async () => {
  const { ready, enter, stage, status, video } = player({ mode: 'fisheye', supported: false });
  await ready();
  assert.equal(enter.hidden, true);
  assert.equal(stage.hidden, true);
  assert.equal(video.src, undefined);
  assert.match(status.textContent, /fisheye.*HTTPS.*WebXR/i);
});
