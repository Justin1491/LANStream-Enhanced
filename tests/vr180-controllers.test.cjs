const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/vr180-controllers.js'), 'utf8');
const identity = new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

function setup(globals = {}, profiles = ['oculus-touch-v3'], fisheyeVideo) {
  const calls = [];
  const gl = new Proxy({}, {
    get(_, name) {
      if (name === 'getShaderParameter' || name === 'getProgramParameter') return () => true;
      if (name === 'getAttribLocation') return (_, attribute) => ({ position: 0, normal: 1, texcoord: 2 })[attribute];
      if (name === 'getUniformLocation') return (_, uniform) => uniform;
      if (name.startsWith('create')) return () => ({});
      if (name.toUpperCase() === name) return name;
      return (...args) => calls.push([name, ...args]);
    },
  });
  const layer = { destroy() { calls.push(['destroy']); } };
  const binding = {
    nativeProjectionScaleFactor: 1.25,
    createProjectionLayer(init) { calls.push(['createProjectionLayer', init]); return layer; },
    getViewSubImage(_, view) {
      return {
        colorTexture: {}, depthStencilTexture: {},
        viewport: { x: view.eye === 'left' ? 0 : 400, y: 0, width: 400, height: 400 },
      };
    },
  };
  const localSpace = {}, gripSpace = {}, targetRaySpace = {};
  const source = { targetRayMode: 'tracked-pointer', handedness: 'left', gripSpace, targetRaySpace, profiles };
  const session = { inputSources: [source] };
  const context = vm.createContext({ console: { warn() {} }, ...globals });
  vm.runInContext(script, context);
  const controls = context.createVrControllerLayer(session, binding, gl, localSpace, fisheyeVideo);
  let x = 0, angle = 0, tracked = true;
  const frame = {
    getPose(_, relativeTo) {
      assert.equal(relativeTo, localSpace);
      return tracked ? { transform: {
        position: { x, y: 0, z: -1 },
        orientation: { x: 0, y: Math.sin(angle / 2), z: 0, w: Math.cos(angle / 2) },
        matrix: identity,
      } } : null;
    },
    getViewerPose(relativeTo) {
      assert.equal(relativeTo, localSpace);
      return { views: ['left', 'right'].map(eye => ({
        eye, projectionMatrix: identity, transform: { inverse: { matrix: identity } },
      })) };
    },
  };
  return { controls, calls, layer, frame, session, source,
    move(value) { x = value; }, rotate(value) { angle = value; }, tracking(value) { tracked = value; } };
}

test('controller visibility expires after three seconds and ignores tracking jitter', () => {
  const { controls, frame, move, rotate } = setup();
  assert.equal(controls.update(100, frame), true);
  move(0.004);
  rotate(Math.PI / 180);
  assert.equal(controls.update(3099, frame), true);
  assert.equal(controls.update(3100, frame), false);
  move(0.016);
  assert.equal(controls.update(3200, frame), true);
  assert.equal(controls.update(6200, frame), false);
  rotate(5 * Math.PI / 180);
  assert.equal(controls.update(6300, frame), true);
  assert.equal(controls.update(9300, frame), false);
  controls.markActive(9400);
  assert.equal(controls.update(12399, frame), true);
  assert.equal(controls.update(12400, frame), false);
});

function modelLoader() {
  const requests = [], bitmaps = [], bitmap = { closed: false, close() { this.closed = true; } };
  return { requests, bitmap, bitmaps, globals: {
    AbortController,
    async createImageBitmap() {
      const result = bitmaps.length ? { closed: false, close() { this.closed = true; } } : bitmap;
      bitmaps.push(result);
      return result;
    },
    async fetch(url, options) {
      requests.push({ url, options });
      const file = fs.readFileSync(path.join(__dirname, '../static/vr180-models', url.split('/vr180-models/')[1]));
      return { ok: true, async blob() { return file; },
        async arrayBuffer() { return file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength); } };
    },
  } };
}

test('official Quest 2 models load locally and use indexed geometry for both grip poses', async () => {
  const loader = modelLoader();
  const { controls, calls, frame, session, source } = setup(loader.globals);
  source.profiles = ['oculus-touch-v3', 'oculus-touch-v2'];
  session.inputSources.push({ ...source, handedness: 'right', gripSpace: {}, targetRaySpace: {} });
  assert.equal(await controls.ready, true);
  assert.equal(loader.bitmap.closed, true);
  assert.deepEqual(loader.requests.map(request => request.url), [
    '/static/vr180-models/left.bin', '/static/vr180-models/right.bin', '/static/vr180-models/color.png',
  ]);
  assert.ok(loader.requests.every(request => request.options.credentials === 'same-origin'));
  const uploads = calls.filter(call => call[0] === 'bufferData').length;
  controls.update(100, frame);
  const before = calls.length;
  controls.draw(frame);
  const drawing = calls.slice(before);
  assert.equal(drawing.filter(call => call[0] === 'drawElements').length, 4);
  assert.equal(drawing.filter(call => call[0] === 'drawArrays').length, 0);
  assert.equal(drawing.filter(call => call[0] === 'uniform1i' && call[1] === 'textured' && call[2] === 1).length, 4);
  assert.equal(calls.filter(call => call[0] === 'bufferData').length, uploads, 'No mesh upload in a render frame.');
  controls.close();
  controls.close();
  assert.equal(calls.filter(call => call[0] === 'deleteBuffer').length, 6);
  assert.equal(calls.filter(call => call[0] === 'deleteVertexArray').length, 4);
  assert.equal(calls.filter(call => call[0] === 'deleteTexture').length, 3);
  assert.ok(loader.requests[0].options.signal.aborted);
});

for (const profile of ['meta-quest-touch-plus', 'meta-quest-touch-plus-v2']) {
  test(`${profile} loads ringless hand-specific official models without selecting Quest 2 fallback`, async () => {
    const loader = modelLoader();
    const { controls, calls, frame, session, source } = setup(loader.globals, [profile, 'oculus-touch-v3']);
    session.inputSources.push({ ...source, handedness: 'right', gripSpace: {}, targetRaySpace: {} });
    controls.update(100, frame);
    const beforeLoading = calls.length;
    controls.draw(frame);
    assert.equal(calls.slice(beforeLoading).filter(call => call[0].startsWith('draw')).length, 0,
      'No procedural blocks flash while a recognized model is loading.');
    assert.equal(await controls.ready, true);
    assert.equal(loader.bitmaps.length, 2);
    assert.ok(loader.bitmaps.every(bitmap => bitmap.closed));
    assert.deepEqual(loader.requests.map(request => request.url), ['left.bin', 'right.bin', 'left.png', 'right.png']
      .map(name => `/static/vr180-models/${profile}/${name}`));
    const before = calls.length;
    controls.draw(frame);
    const drawing = calls.slice(before);
    assert.equal(drawing.filter(call => call[0] === 'drawElements').length, 4);
    assert.equal(drawing.filter(call => call[0] === 'drawArrays').length, 0);
    const textures = drawing.filter(call => call[0] === 'bindTexture').map(call => call[2]).filter(Boolean);
    assert.notEqual(textures[1], textures[2], 'Left and right keep their own official texture atlases.');
    controls.close();
    assert.equal(calls.filter(call => call[0] === 'deleteTexture').length, 4);
  });
}

test('a controller connected after session start loads its exact profile once', async () => {
  const loader = modelLoader();
  const { controls, frame, source } = setup(loader.globals, []);
  // Introduce Touch Plus after the session has already started.
  await controls.ready;
  const initial = loader.requests.length;
  source.profiles = ['meta-quest-touch-plus-v2', 'oculus-touch-v3'];
  controls.update(100, frame);
  controls.update(200, frame);
  assert.equal(await controls.ready, true);
  assert.equal(loader.requests.length - initial, 4);
  controls.close();
});

test('unsupported controller models and missing grip poses retain procedural fallback', async () => {
  const { controls, calls, frame, source } = setup(modelLoader().globals);
  assert.equal(await controls.ready, true);
  for (const profiles of [undefined, ['oculus-touch-v2'], ['meta-quest-touch-pro', 'oculus-touch-v3']]) {
    source.profiles = profiles;
    controls.update(100, frame);
    const before = calls.length;
    controls.draw(frame);
    assert.equal(calls.slice(before).filter(call => call[0] === 'drawElements').length, 0);
    assert.equal(calls.slice(before).filter(call => call[0] === 'drawArrays').length, 4);
  }
  source.profiles = ['oculus-touch-v3'];
  source.gripSpace = null;
  controls.update(100, frame);
  const before = calls.length;
  controls.draw(frame);
  assert.equal(calls.slice(before).filter(call => call[0] === 'drawElements').length, 0);
  controls.close();
});

test('one supplied beam uses its exact hardware pose and positive hit distance in both eyes', () => {
  const { controls, calls, frame } = setup();
  const matrix = new Float32Array([0, 0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0.2, 0.5, -0.1, 1]);
  controls.update(100, frame);
  for (const distance of [1.65, 0.02]) {
    const before = calls.length;
    controls.draw(frame, { showControllers: false, beam: { matrix, distance } });
    const drawing = calls.slice(before);
    assert.equal(drawing.filter(call => call[0] === 'drawArrays').length, 2, 'Exactly one beam per eye.');
    const sizes = drawing.filter(call => call[0] === 'uniform3fv' && call[1] === 'size');
    const offsets = drawing.filter(call => call[0] === 'uniform3fv' && call[1] === 'offset');
    const poses = drawing.filter(call => call[0] === 'uniformMatrix4fv' && call[1] === 'pose');
    for (let eye = 0; eye < 2; eye++) {
      const size = sizes[eye][2], offset = offsets[eye][2];
      assert.equal(poses[eye][3], matrix);
      assert.ok(Math.abs(offset[2] - size[2] / 2 + distance) < 1e-9);
      assert.ok(offset[2] + size[2] / 2 <= 0);
    }
  }
  for (const distance of [0, -1, NaN, Infinity, undefined]) {
    const before = calls.length;
    controls.draw(frame, { showControllers: false, beam: { matrix, distance } });
    assert.equal(calls.slice(before).filter(call => call[0] === 'drawArrays').length, 0);
  }
});

test('panel uploads upright premultiplied pixels and renders without tracked controllers', () => {
  const { controls, calls, frame, session } = setup();
  session.inputSources = [];
  assert.equal(controls.update(100, frame), false);
  const canvas = { width: 960, height: 300 };
  const beforeUpload = calls.length;
  controls.uploadPanel(canvas);
  const uploaded = calls.slice(beforeUpload);
  assert.deepEqual(uploaded.filter(call => call[0] === 'pixelStorei'), [
    ['pixelStorei', 'UNPACK_FLIP_Y_WEBGL', true],
    ['pixelStorei', 'UNPACK_PREMULTIPLY_ALPHA_WEBGL', true],
  ]);
  assert.equal(uploaded.find(call => call[0] === 'texImage2D').at(-1), canvas);
  const vertices = calls.filter(call => call[0] === 'bufferData')[1][2];
  for (let i = 0; i < vertices.length; i += 8) {
    assert.equal(vertices[i + 7], vertices[i + 1] + 0.5, 'UV increases upward on the panel.');
  }
  const panel = { matrix: identity, width: 1.15, height: 0.4, y: -0.3, z: -1.5 };
  const beforeDraw = calls.length;
  controls.draw(frame, { panel, showControllers: false });
  const drawing = calls.slice(beforeDraw);
  assert.equal(drawing.filter(call => call[0] === 'drawArrays' && call[3] === 6).length, 2);
  assert.equal(drawing.filter(call => call[0] === 'texImage2D').length, 0, 'Drawing does not upload unchanged HUD pixels.');
  for (const pose of drawing.filter(call => call[0] === 'uniformMatrix4fv' && call[1] === 'pose')) {
    assert.equal(pose[3], panel.matrix);
  }
  for (const offset of drawing.filter(call => call[0] === 'uniform3fv' && call[1] === 'offset')) {
    assert.deepEqual(Array.from(offset[2]), [0, panel.y, panel.z]);
  }
  const shader = calls.find(call => call[0] === 'shaderSource' && call[2].includes('out vec4 fragment'))[2];
  assert.match(shader, /texel\.rgb \* color\.rgb/);
  assert.doesNotMatch(shader, /texel\.rgb[^,;]*texel\.a/, 'Premultiplied panel RGB is not multiplied by texture alpha twice.');
});

test('cursor uses the panel transform without texture uploads or an implicit beam', () => {
  const { controls, calls, frame } = setup();
  const panel = { matrix: identity, width: 1.15, height: 0.4, y: -0.3, z: -1.5 };
  const before = calls.length;
  controls.draw(frame, { panel, cursor: { x: 0.2, y: 0.1 }, showControllers: false });
  const drawing = calls.slice(before);
  assert.equal(drawing.filter(call => call[0] === 'drawArrays' && call[3] === 6).length, 4);
  assert.equal(drawing.filter(call => call[0] === 'drawArrays' && call[3] === 36).length, 0);
  assert.equal(drawing.filter(call => call[0] === 'uniform1i' && call[1] === 'spot' && call[2] === 1).length, 2);
  const offset = drawing.filter(call => call[0] === 'uniform3fv' && call[1] === 'offset').at(-1)[2];
  assert.deepEqual(Array.from(offset), [0.2, panel.y + 0.1, panel.z + 0.0003]);
  assert.equal(drawing.filter(call => call[0] === 'texImage2D').length, 0);
});

test('off-panel hand dot is centered on the stereo beam endpoint', () => {
  const { controls, calls, frame } = setup();
  const matrix = new Float32Array(identity);
  matrix[12] = 0.35;
  const panel = { matrix: identity, width: 0.71, height: 0.142, y: -0.30, z: -1.65 };
  const before = calls.length;
  controls.draw(frame, { panel, showControllers: false, beam: { matrix, distance: 1.65 }, handPointer: true });
  const drawing = calls.slice(before);
  const offsets = drawing.filter(call => call[0] === 'uniform3fv' && call[1] === 'offset');
  const poses = drawing.filter(call => call[0] === 'uniformMatrix4fv' && call[1] === 'pose');
  for (const index of [2, 5]) {
    assert.equal(poses[index][3], matrix);
    assert.deepEqual(Array.from(offsets[index][2]), [0, 0, -1.65]);
  }
});

test('inactive overlay clears both eyes and does not retain the previous beam', () => {
  const { controls, calls, frame } = setup();
  controls.update(100, frame);
  controls.draw(frame, { showControllers: false, beam: { matrix: identity, distance: 1 } });
  const before = calls.length;
  controls.draw(frame, { showControllers: false });
  const drawing = calls.slice(before);
  assert.equal(drawing.filter(call => call[0] === 'clear').length, 2);
  assert.equal(drawing.filter(call => call[0].startsWith('draw')).length, 0);
  assert.ok(drawing.some(call => call[0] === 'bindTexture' && call[2]), 'Sampler always has a complete fallback texture.');
});

test('optional fisheye uploads once and draws behind controls in each eye, then closes once', () => {
  const video = {}, movies = [];
  let calls;
  const fixture = setup({ createVrFisheyeRenderer(gl, source) {
    assert.equal(source, video);
    return {
      upload() { movies.push(['upload', calls.length]); },
      draw(view) { movies.push([view.eye, calls.length]); },
      close() { movies.push(['close']); },
    };
  } }, [], video);
  calls = fixture.calls;
  const panel = { matrix: identity, width: 1, height: 0.2, y: -0.3, z: -1.5 };
  fixture.controls.draw(fixture.frame, { panel, showControllers: false });
  assert.deepEqual(movies.map(call => call[0]), ['upload', 'left', 'right']);
  for (const [, index] of movies.slice(1)) {
    assert.equal(calls[index - 1][0], 'clear', 'Movie starts immediately after clearing its eye.');
    assert.equal(calls[index][0], 'useProgram', 'The control program is restored after the movie.');
    assert.ok(calls.slice(index).some(call => call[0] === 'drawArrays' && call[3] === 6));
  }
  fixture.controls.close();
  fixture.controls.close();
  assert.equal(movies.filter(call => call[0] === 'close').length, 1);
});

test('failed or malformed assets keep fallback usable and release partial GPU resources', async () => {
  for (const corrupt of [false, true]) {
    const loader = modelLoader();
    const fetch = loader.globals.fetch;
    loader.globals.fetch = async (url, options) => {
      if (!corrupt) return { ok: false };
      if (url.endsWith('right.bin')) return { ok: true, arrayBuffer: async () => new ArrayBuffer(12) };
      return fetch(url, options);
    };
    const { controls, calls, frame, source } = setup(loader.globals);
    source.profiles = ['oculus-touch-v3'];
    assert.equal(await controls.ready, false);
    assert.equal(calls.filter(call => call[0] === 'deleteBuffer').length, corrupt ? 2 : 0);
    assert.equal(loader.bitmap.closed, corrupt);
    controls.update(100, frame);
    const before = calls.length;
    controls.draw(frame);
    assert.equal(calls.slice(before).filter(call => call[0] === 'drawElements').length, 0);
    assert.equal(calls.slice(before).filter(call => call[0] === 'drawArrays').length, 4);
    controls.close();
  }
});

test('failure decoding the second Touch Plus atlas closes the first and leaves a usable fallback', async () => {
  const loader = modelLoader();
  let decoded = 0;
  loader.globals.createImageBitmap = async () => {
    if (decoded++) throw new Error('Invalid second PNG.');
    return loader.bitmap;
  };
  const { controls, calls, frame } = setup(loader.globals, ['meta-quest-touch-plus-v2', 'oculus-touch-v3']);
  assert.equal(await controls.ready, false);
  assert.equal(loader.bitmap.closed, true);
  assert.equal(calls.filter(call => call[0] === 'deleteBuffer').length, 0);
  controls.update(100, frame);
  const before = calls.length;
  controls.draw(frame);
  assert.equal(calls.slice(before).filter(call => call[0] === 'drawArrays').length, 4);
  controls.close();
});

test('ending the session during model load closes the bitmap without allocating GPU resources', async () => {
  const loader = modelLoader();
  let resolveBitmap;
  loader.globals.createImageBitmap = () => new Promise(resolve => { resolveBitmap = resolve; });
  const { controls, calls } = setup(loader.globals);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof resolveBitmap, 'function');
  controls.close();
  const after = calls.length;
  resolveBitmap(loader.bitmap);
  assert.equal(await controls.ready, false);
  assert.equal(loader.bitmap.closed, true);
  assert.equal(calls.length, after);
});

test('bundled meshes reproduce exactly from pinned official GLBs and retain physical scale', () => {
  const { bake } = require('../static/vr180-models/bake.cjs');
  const modelDirectory = path.join(__dirname, '../static/vr180-models');
  for (const profile of ['oculus-touch-v3', 'meta-quest-touch-plus', 'meta-quest-touch-plus-v2']) {
    const directory = profile === 'oculus-touch-v3' ? modelDirectory : path.join(modelDirectory, profile);
    for (const hand of ['left', 'right']) {
      const { mesh, texture } = bake(fs.readFileSync(path.join(directory, 'upstream', `${hand}.glb`)), hand, profile);
      assert.deepEqual(mesh, fs.readFileSync(path.join(directory, `${hand}.bin`)));
      assert.deepEqual(texture, fs.readFileSync(path.join(directory, profile === 'oculus-touch-v3' ? 'color.png' : `${hand}.png`)));
      const vertexCount = mesh.readUInt32LE(4), indexCount = mesh.readUInt32LE(8);
      assert.equal(indexCount, profile === 'oculus-touch-v3' ? 17493 : 13410);
      const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < vertexCount; i++) {
        for (let axis = 0; axis < 3; axis++) {
          const value = mesh.readFloatLE(12 + i * 32 + axis * 4);
          min[axis] = Math.min(min[axis], value);
          max[axis] = Math.max(max[axis], value);
        }
        const normalLength = Math.hypot(...[3, 4, 5].map(k => mesh.readFloatLE(12 + i * 32 + k * 4)));
        assert.ok(Math.abs(normalLength - 1) < 1e-4);
      }
      const extents = max.map((value, axis) => value - min[axis]);
      assert.ok(extents.every(size => size > 0.045 && size < 0.2), `${profile} ${hand} controller is correctly sized in meters.`);
      if (profile !== 'oculus-touch-v3') assert.ok(extents[0] < 0.09, 'Ringless Touch Plus stays narrower than a tracking ring.');
      assert.ok(min.every(value => value > -0.2) && max.every(value => value < 0.2));
    }
  }
});

test('lost tracking, removed sources, gaze, and hand input do not leave controller ghosts', () => {
  const { controls, frame, tracking, session, source } = setup();
  controls.update(0, frame);
  tracking(false);
  assert.equal(controls.update(100, frame), false);
  tracking(true);
  assert.equal(controls.update(4000, frame), true);
  session.inputSources = [];
  assert.equal(controls.update(4100, frame), false);
  session.inputSources = [source];
  assert.equal(controls.update(8000, frame), true);
  source.targetRayMode = 'gaze';
  assert.equal(controls.update(8100, frame), false);
  source.targetRayMode = 'tracked-pointer';
  source.hand = {};
  assert.equal(controls.update(8200, frame), false);
  source.hand = null;
  source.profiles = ['generic-hand-select-grasp'];
  assert.equal(controls.update(8300, frame), false);
});

test('holding a tracked controller button keeps it visible through a long scrub', () => {
  const { controls, frame, source } = setup();
  source.gamepad = { buttons: [{ pressed: true }] };
  assert.equal(controls.update(100, frame), true);
  assert.equal(controls.update(10000, frame), true);
  source.gamepad.buttons[0].pressed = false;
  assert.equal(controls.update(12999, frame), true);
  assert.equal(controls.update(13000, frame), false);
});

test('both eye viewports clear to transparent and resources close once', () => {
  const { controls, calls, layer, frame, session, source } = setup();
  session.inputSources.push({ ...source, handedness: 'right', gripSpace: {}, targetRaySpace: {} });
  assert.equal(layer.blendTextureSourceAlpha, true);
  assert.equal(calls[0][1].scaleFactor, 1.25);
  controls.update(100, frame);
  const before = calls.length;
  controls.draw(frame);
  const drawCalls = calls.slice(before);
  assert.deepEqual(drawCalls.filter(call => call[0] === 'scissor'), [
    ['scissor', 0, 0, 400, 400], ['scissor', 400, 0, 400, 400],
  ]);
  assert.deepEqual(drawCalls.find(call => call[0] === 'clearColor'), ['clearColor', 0, 0, 0, 0]);
  assert.equal(drawCalls.filter(call => call[0] === 'clear').length, 2);
  assert.equal(drawCalls.filter(call => call[0] === 'drawArrays').length, 8);
  controls.close();
  controls.close();
  assert.equal(calls.filter(call => call[0] === 'destroy').length, 1);
  assert.equal(controls.update(200, frame), false);
  const after = calls.length;
  controls.draw(frame);
  assert.equal(calls.length, after);
});
