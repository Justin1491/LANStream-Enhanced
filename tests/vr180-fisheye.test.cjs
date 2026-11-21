const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/vr180-fisheye.js'), 'utf8');
const identity = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

function setup({ callbacks = true, quality = true, compile = true, link = true } = {}) {
  const calls = [], listeners = new Map(), pending = new Map();
  let next = 0, frames = 1, error = 'NO_ERROR';
  const gl = new Proxy({}, {
    get(_, name) {
      if (name === 'getShaderParameter') return () => compile;
      if (name === 'getProgramParameter') return () => link;
      if (name === 'getParameter') return () => 8192;
      if (name === 'getError') return () => error;
      if (name === 'getUniformLocation') return (_, uniform) => uniform;
      if (name.startsWith('create')) return () => ({});
      if (name.toUpperCase() === name) return name;
      return (...args) => { calls.push([name, ...args]); };
    },
  });
  const video = {
    videoWidth: 4000, videoHeight: 1800, readyState: 2, currentTime: 0, seeking: false,
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name, listener) { if (listeners.get(name) === listener) listeners.delete(name); },
  };
  if (callbacks) {
    video.requestVideoFrameCallback = fn => { pending.set(++next, fn); return next; };
    video.cancelVideoFrameCallback = id => { calls.push(['cancelVideoFrameCallback', id]); pending.delete(id); };
  }
  if (quality) video.getVideoPlaybackQuality = () => ({ totalVideoFrames: frames });
  const context = vm.createContext({});
  vm.runInContext(script, context);
  let renderer, failure;
  try { renderer = context.createVrFisheyeRenderer(gl, video); } catch (error) { failure = error; }
  return { gl, video, calls, renderer, failure, pending, listeners,
    decode(time) {
      video.currentTime = time;
      frames++;
      const entry = pending.entries().next().value;
      if (entry) { pending.delete(entry[0]); entry[1](0, { mediaTime: time }); }
    },
    advanceWithoutCallback(time) { video.currentTime = time; frames++; },
    event(name) { listeners.get(name)?.(); },
    error(value) { error = value; },
  };
}

function uploads(calls) {
  return calls.filter(call => ['texImage2D', 'texSubImage2D'].includes(call[0]));
}

test('decoded video uploads once across eyes and XR refreshes, without mipmaps', () => {
  const { renderer, calls, decode, video } = setup();
  renderer.upload();
  for (let frame = 0; frame < 5; frame++) {
    renderer.upload();
    for (const eye of ['left', 'right']) renderer.draw({ eye, projectionMatrix: identity, transform: { matrix: identity } });
  }
  assert.equal(uploads(calls).length, 1);
  decode(1 / 60);
  renderer.upload();
  renderer.upload();
  assert.deepEqual(uploads(calls).map(call => call[0]), ['texImage2D', 'texSubImage2D']);
  assert.equal(uploads(calls)[1].at(-1), video);
  assert.equal(calls.filter(call => call[0] === 'generateMipmap').length, 0);
  assert.ok(calls.some(call => call[0] === 'pixelStorei' && call[1] === 'UNPACK_FLIP_Y_WEBGL' && call[2] === true));
});

test('same media timestamp is stable, and a seek to the same timestamp still refreshes', () => {
  const fixture = setup();
  fixture.renderer.upload();
  fixture.decode(0);
  fixture.renderer.upload();
  assert.equal(uploads(fixture.calls).length, 1);
  fixture.video.seeking = true;
  fixture.event('seeked');
  fixture.renderer.upload();
  assert.equal(uploads(fixture.calls).length, 1);
  fixture.video.seeking = false;
  fixture.event('seeked');
  fixture.renderer.upload();
  fixture.renderer.upload();
  assert.equal(uploads(fixture.calls).length, 2);
});

test('decoded frame counter keeps moving when video callbacks are throttled', () => {
  const fixture = setup();
  fixture.renderer.upload();
  fixture.advanceWithoutCallback(1 / 60);
  fixture.renderer.upload();
  assert.equal(uploads(fixture.calls).length, 2);
  fixture.renderer.upload();
  assert.equal(uploads(fixture.calls).length, 2);
});

test('frame-counter and currentTime fallbacks avoid unchanged paused uploads', () => {
  for (const quality of [true, false]) {
    const fixture = setup({ callbacks: false, quality });
    fixture.renderer.upload();
    fixture.video.currentTime = 0.001;
    fixture.renderer.upload();
    assert.equal(uploads(fixture.calls).length, quality ? 1 : 2);
    fixture.advanceWithoutCallback(1 / 60);
    fixture.renderer.upload();
    fixture.renderer.upload();
    assert.equal(uploads(fixture.calls).length, quality ? 2 : 3);
  }
});

test('empty media and dimensions beyond GPU limits never upload', () => {
  const fixture = setup();
  fixture.video.readyState = 1;
  fixture.renderer.upload();
  assert.equal(uploads(fixture.calls).length, 0);
  fixture.video.readyState = 2;
  fixture.video.videoWidth = 9000;
  assert.throws(() => fixture.renderer.upload(), /9000.*8192/);
  assert.equal(uploads(fixture.calls).length, 0);
  fixture.video.videoWidth = 4000;
  fixture.video.videoHeight = 9000;
  assert.throws(() => fixture.renderer.upload(), /4000.*9000.*8192/);
  fixture.renderer.close();
  assert.equal(fixture.listeners.size, 0);
  assert.equal(fixture.pending.size, 0);
});

test('resized media reallocates once and upload failure unbinds the source', () => {
  const fixture = setup();
  fixture.renderer.upload();
  fixture.video.videoWidth = 6000;
  fixture.renderer.upload();
  assert.deepEqual(uploads(fixture.calls).map(call => call[0]), ['texImage2D', 'texImage2D']);
  fixture.decode(1);
  fixture.error('OUT_OF_MEMORY');
  assert.throws(() => fixture.renderer.upload(), /Unable to upload/);
  assert.deepEqual(fixture.calls.at(-1), ['bindTexture', 'TEXTURE_2D', null]);
  fixture.renderer.close();
});

test('eye uniforms and projection preserve the view rotation while shader excludes position', () => {
  const fixture = setup();
  fixture.renderer.upload();
  const rotation = new Float32Array(identity);
  rotation[12] = 100;
  rotation[13] = -50;
  const projection = new Float32Array(identity);
  projection[0] = 1.5;
  projection[5] = 2;
  projection[8] = 0.2;
  projection[9] = -0.1;
  for (const eye of ['left', 'right']) fixture.renderer.draw({ eye, projectionMatrix: projection, transform: { matrix: rotation } });
  assert.deepEqual(fixture.calls.filter(call => call[0] === 'uniform1f' && call[1] === 'eye').map(call => call[2]), [0, 1]);
  for (const uniform of fixture.calls.filter(call => call[0] === 'uniformMatrix4fv')) {
    assert.equal(uniform[3], uniform[1] === 'rotation' ? rotation : projection);
  }
  const shaders = fixture.calls.filter(call => call[0] === 'shaderSource').map(call => call[2]);
  assert.match(shaders[0], /mat3\(rotation\) \* vec3/);
  assert.match(shaders[0], /\(screen\.x \+ projection\[2\]\[0\]\) \/ projection\[0\]\[0\]/);
  assert.match(shaders[0], /\(screen\.y \+ projection\[2\]\[1\]\) \/ projection\[1\]\[1\]/);
  assert.match(shaders[1], /atan\(radial, -ray\.z\)/);
  assert.match(shaders[1], /angle \/ 1\.5707963267948966/);
  assert.match(shaders[1], /if \(ray\.z > 0\.0\)/);
  assert.match(shaders[1], /min\(dimensions\.x \* 0\.5, dimensions\.y\) \* 0\.5/);
  assert.match(shaders[1], /vec2\(eye \* 0\.5 \+ 0\.25, 0\.5\) \+ circle \* radius \/ dimensions/);
  assert.equal(fixture.calls.filter(call => call[0] === 'drawArrays').length, 2);
});

test('closing cancels decoder callbacks, removes listeners and deletes GPU objects once', () => {
  const fixture = setup();
  const stale = fixture.pending.values().next().value;
  fixture.renderer.close();
  fixture.renderer.close();
  stale(0, { mediaTime: 4 });
  fixture.renderer.upload();
  fixture.renderer.draw({});
  assert.equal(fixture.listeners.size, 0);
  assert.equal(fixture.pending.size, 0);
  for (const name of ['deleteProgram', 'deleteTexture', 'deleteVertexArray', 'cancelVideoFrameCallback']) {
    assert.equal(fixture.calls.filter(call => call[0] === name).length, 1);
  }
  assert.equal(uploads(fixture.calls).length, 0);
});

test('shader compile and link failure release partial GPU objects', () => {
  for (const options of [{ compile: false }, { link: false }]) {
    const fixture = setup(options);
    assert.ok(fixture.failure);
    assert.equal(fixture.listeners.size, 0);
    assert.equal(fixture.pending.size, 0);
    for (const name of ['deleteProgram', 'deleteTexture', 'deleteVertexArray']) {
      assert.equal(fixture.calls.filter(call => call[0] === name).length, 1);
    }
  }
});
