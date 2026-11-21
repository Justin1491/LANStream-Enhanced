const vrControllerModelBase = typeof document !== 'undefined' && document.currentScript?.src
  ? new URL('vr180-models/', document.currentScript.src).href : '/static/vr180-models/';

function createVrControllerLayer(session, binding, gl, localSpace, fisheyeVideo) {
  const layer = binding.createProjectionLayer({
    textureType: 'texture', colorFormat: gl.RGBA8,
    depthFormat: gl.DEPTH_COMPONENT24, scaleFactor: binding.nativeProjectionScaleFactor || 1,
  });
  layer.blendTextureSourceAlpha = true;
  const framebuffer = gl.createFramebuffer();
  const program = gl.createProgram();
  const buffer = gl.createBuffer();
  const vao = gl.createVertexArray();
  const panelBuffer = gl.createBuffer();
  const panelVao = gl.createVertexArray();
  const whiteTexture = gl.createTexture();
  const panelTexture = gl.createTexture();
  let fisheye;
  let closed = false, hideAt = 0, poses = [];
  const previous = new Map();
  const assets = new Map();
  const loading = typeof AbortController === 'function' ? new AbortController() : undefined;

  function close() {
    if (closed) return;
    closed = true;
    poses = [];
    previous.clear();
    fisheye?.close();
    gl.deleteFramebuffer(framebuffer);
    gl.deleteProgram(program);
    gl.deleteBuffer(buffer);
    gl.deleteVertexArray(vao);
    gl.deleteBuffer(panelBuffer);
    gl.deleteVertexArray(panelVao);
    gl.deleteTexture(whiteTexture);
    gl.deleteTexture(panelTexture);
    for (const asset of assets.values()) releaseModels(asset);
    assets.clear();
    loading?.abort();
    layer.destroy();
  }

  try {
    if (fisheyeVideo) fisheye = createVrFisheyeRenderer(gl, fisheyeVideo);
    for (const [type, source] of [
      [gl.VERTEX_SHADER, `#version 300 es
        in vec3 position;
        in vec3 normal;
        in vec2 texcoord;
        uniform mat4 projection, view, pose;
        uniform vec3 size, offset;
        out mediump float light;
        out mediump vec2 uv;
        void main() {
          uv = texcoord;
          light = 0.45 + 0.55 * max(0.0, dot(mat3(pose) * normal, normalize(vec3(-1, 2, 1))));
          gl_Position = projection * view * pose * vec4(position * size + offset, 1);
        }`],
      [gl.FRAGMENT_SHADER, `#version 300 es
        precision mediump float;
        uniform vec4 color;
        uniform sampler2D atlas;
        uniform bool textured, unlit;
        uniform int spot;
        in mediump float light;
        in mediump vec2 uv;
        out vec4 fragment;
        void main() {
          if (spot != 0) {
            float radius = distance(uv, vec2(0.5));
            if (radius > 0.5) discard;
            if (spot == 2 && radius > 0.35) { fragment = vec4(0.03, 0.09, 0.12, 1); return; }
          }
          vec4 texel = textured ? texture(atlas, uv) : vec4(1);
          fragment = vec4(texel.rgb * color.rgb * (unlit ? 1.0 : light) * color.a, texel.a * color.a);
        }`],
    ]) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const error = gl.getShaderInfoLog(shader);
        gl.deleteShader(shader);
        throw new Error(error || 'Unable to compile VR controller shader.');
      }
      gl.attachShader(program, shader);
      gl.deleteShader(shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program) || 'Unable to link VR controller shader.');
    }

    // One flat-shaded cube serves as the fallback controller and hit beam.
    const vertices = [];
    for (let axis = 0; axis < 3; axis++) {
      for (const sign of [-1, 1]) {
        for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, -1], [1, 1], [-1, 1]]) {
          const point = [0, 0, 0], normal = [0, 0, 0];
          point[axis] = sign * 0.5;
          point[(axis + 1) % 3] = a * 0.5;
          point[(axis + 2) % 3] = b * 0.5;
          normal[axis] = sign;
          vertices.push(...point, ...normal);
        }
      }
    }
    gl.bindVertexArray(vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.STATIC_DRAW);
    for (const [name, offset] of [['position', 0], ['normal', 12]]) {
      const location = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, 3, gl.FLOAT, false, 24, offset);
    }
    gl.bindVertexArray(panelVao);
    gl.bindBuffer(gl.ARRAY_BUFFER, panelBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
      -0.5, -0.5, 0, 0, 0, 1, 0, 0,
      0.5, -0.5, 0, 0, 0, 1, 1, 0,
      0.5, 0.5, 0, 0, 0, 1, 1, 1,
      -0.5, -0.5, 0, 0, 0, 1, 0, 0,
      0.5, 0.5, 0, 0, 0, 1, 1, 1,
      -0.5, 0.5, 0, 0, 0, 1, 0, 1,
    ]), gl.STATIC_DRAW);
    for (const [name, size, offset] of [['position', 3, 0], ['normal', 3, 12], ['texcoord', 2, 24]]) {
      const location = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, gl.FLOAT, false, 32, offset);
    }
    gl.activeTexture(gl.TEXTURE0);
    for (const target of [whiteTexture, panelTexture]) {
      gl.bindTexture(gl.TEXTURE_2D, target);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([255, 255, 255, 255]));
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }
    gl.bindTexture(gl.TEXTURE_2D, null);
    gl.bindVertexArray(null);
    gl.bindBuffer(gl.ARRAY_BUFFER, null);
  } catch (error) {
    close();
    throw error;
  }
  const uniforms = Object.fromEntries(['projection', 'view', 'pose', 'size', 'offset', 'color', 'atlas', 'textured', 'unlit', 'spot']
    .map(name => [name, gl.getUniformLocation(program, name)]));

  function releaseModels(asset) {
    for (const model of asset.models.values()) {
      gl.deleteBuffer(model.vertices);
      gl.deleteBuffer(model.indices);
      gl.deleteVertexArray(model.vao);
    }
    asset.models.clear();
    for (const texture of asset.textures) gl.deleteTexture(texture);
    asset.textures = [];
  }

  function controllerProfile(source) {
    const profile = source.profiles?.[0];
    return ['oculus-touch-v3', 'meta-quest-touch-plus', 'meta-quest-touch-plus-v2'].includes(profile) ? profile : undefined;
  }

  async function loadModels(profile, asset) {
    if (typeof fetch !== 'function' || typeof createImageBitmap !== 'function') {
      asset.state = 'failed';
      return false;
    }
    const bitmaps = [];
    try {
      const directory = profile === 'oculus-touch-v3' ? '' : `${profile}/`;
      const images = profile === 'oculus-touch-v3' ? ['color.png'] : ['left.png', 'right.png'];
      const responses = await Promise.all(['left.bin', 'right.bin', ...images].map(async name => {
        const response = await fetch(vrControllerModelBase + directory + name, { signal: loading?.signal, credentials: 'same-origin' });
        if (!response.ok) throw new Error('Unable to load local controller assets.');
        return response;
      }));
      const [left, right] = await Promise.all(responses.slice(0, 2).map(response => response.arrayBuffer()));
      for (const response of responses.slice(2)) {
        bitmaps.push(await createImageBitmap(await response.blob(), { imageOrientation: 'none', premultiplyAlpha: 'none' }));
      }
      if (closed) return false;
      for (const [hand, bytes] of [['left', left], ['right', right]]) {
        const header = new DataView(bytes);
        const count = header.getUint32(4, true), indexCount = header.getUint32(8, true);
        if (header.getUint32(0, true) !== 0x314d3251 || count > 65535 || !count || !indexCount
          || indexCount % 3 || bytes.byteLength !== 12 + count * 32 + indexCount * 2) {
          throw new Error('Invalid local controller mesh.');
        }
        const vertices = new Float32Array(bytes, 12, count * 8);
        const indices = new Uint16Array(bytes, 12 + count * 32, indexCount);
        if (!vertices.every(Number.isFinite) || indices.some(index => index >= count)) {
          throw new Error('Invalid local controller vertices.');
        }
        const model = { vao: gl.createVertexArray(), vertices: gl.createBuffer(), indices: gl.createBuffer(), count: indexCount };
        asset.models.set(hand, model);
        gl.bindVertexArray(model.vao);
        gl.bindBuffer(gl.ARRAY_BUFFER, model.vertices);
        gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.STATIC_DRAW);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, model.indices);
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
        for (const [name, size, offset] of [['position', 3, 0], ['normal', 3, 12], ['texcoord', 2, 24]]) {
          const location = gl.getAttribLocation(program, name);
          gl.enableVertexAttribArray(location);
          gl.vertexAttribPointer(location, size, gl.FLOAT, false, 32, offset);
        }
      }
      for (const bitmap of bitmaps) {
        const texture = gl.createTexture();
        asset.textures.push(texture);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, bitmap);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.generateMipmap(gl.TEXTURE_2D);
      }
      asset.models.get('left').texture = asset.textures[0];
      asset.models.get('right').texture = asset.textures.at(-1);
      asset.state = 'ready';
      return true;
    } catch (error) {
      releaseModels(asset);
      asset.state = 'failed';
      if (!closed) console.warn('Controller models unavailable; using fallback controllers.', error);
      return false;
    } finally {
      for (const bitmap of bitmaps) bitmap.close();
      if (!closed) {
        gl.bindVertexArray(null);
        gl.bindBuffer(gl.ARRAY_BUFFER, null);
        gl.bindTexture(gl.TEXTURE_2D, null);
      }
    }
  }

  function prepareModels(source) {
    const profile = controllerProfile(source);
    if (profile && !assets.has(profile)) {
      const asset = { models: new Map(), textures: [], state: 'pending' };
      assets.set(profile, asset);
      asset.ready = loadModels(profile, asset);
    }
    return profile && assets.get(profile);
  }
  for (const source of session.inputSources) prepareModels(source);

  function uploadPanel(canvas) {
    if (closed) return;
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, panelTexture);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
    gl.generateMipmap(gl.TEXTURE_2D);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.bindTexture(gl.TEXTURE_2D, null);
  }

  function markActive(time) {
    hideAt = time + 3000;
  }

  function moved(before, after) {
    if (!before) return true;
    const p = after.position, q = after.orientation;
    const distance = (p.x - before[0]) ** 2 + (p.y - before[1]) ** 2 + (p.z - before[2]) ** 2;
    const dot = Math.abs(q.x * before[3] + q.y * before[4] + q.z * before[5] + q.w * before[6]);
    return distance >= 0.015 ** 2 || dot < Math.cos(Math.PI / 120);
  }

  function update(time, frame) {
    if (closed) return false;
    poses = [];
    const present = new Set();
    for (const source of session.inputSources) {
      if (source.targetRayMode !== 'tracked-pointer' || source.hand
        || source.profiles?.some(profile => profile.startsWith('generic-hand'))) continue;
      prepareModels(source);
      present.add(source);
      if (source.gamepad?.buttons.some(button => button.pressed)) markActive(time);
      const baseline = previous.get(source) || {};
      const tracked = { source, handedness: source.handedness };
      for (const [name, space] of [['grip', source.gripSpace], ['ray', source.targetRaySpace]]) {
        const pose = space && frame.getPose(space, localSpace);
        if (pose) {
          tracked[name] = pose.transform.matrix;
          if (moved(baseline[name], pose.transform)) {
            const { position: p, orientation: q } = pose.transform;
            baseline[name] = [p.x, p.y, p.z, q.x, q.y, q.z, q.w];
            markActive(time);
          }
        } else {
          delete baseline[name];
        }
      }
      previous.set(source, baseline);
      if (tracked.grip || tracked.ray) poses.push(tracked);
    }
    for (const source of previous.keys()) {
      if (!present.has(source)) previous.delete(source);
    }
    return poses.length > 0 && time < hideAt;
  }

  function box(pose, size, offset, color, unlit = false) {
    gl.bindVertexArray(vao);
    gl.uniform1i(uniforms.textured, 0);
    gl.uniform1i(uniforms.spot, 0);
    gl.uniform1i(uniforms.unlit, unlit ? 1 : 0);
    gl.uniformMatrix4fv(uniforms.pose, false, pose);
    gl.uniform3fv(uniforms.size, size);
    gl.uniform3fv(uniforms.offset, offset);
    gl.uniform4fv(uniforms.color, color);
    gl.drawArrays(gl.TRIANGLES, 0, 36);
  }

  function draw(frame, { panel, showControllers = true, beam, cursor, handPointer = false } = {}) {
    if (closed) return;
    const viewer = frame.getViewerPose(localSpace);
    if (!viewer) return;
    fisheye?.upload();
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    gl.useProgram(program);
    gl.activeTexture(gl.TEXTURE0);
    gl.uniform1i(uniforms.atlas, 0);
    gl.enable(gl.SCISSOR_TEST);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.depthMask(true);
    gl.disable(gl.CULL_FACE);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.colorMask(true, true, true, true);
    gl.clearColor(0, 0, 0, 0);
    gl.clearDepth(1);
    for (const view of viewer.views) {
      const image = binding.getViewSubImage(layer, view);
      const { x, y, width, height } = image.viewport;
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, image.colorTexture, 0);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.TEXTURE_2D, image.depthStencilTexture, 0);
      gl.viewport(x, y, width, height);
      gl.scissor(x, y, width, height);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      if (fisheye) {
        fisheye.draw(view);
        gl.useProgram(program);
        gl.enable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
      }
      gl.uniformMatrix4fv(uniforms.projection, false, view.projectionMatrix);
      gl.uniformMatrix4fv(uniforms.view, false, view.transform.inverse.matrix);
      if (panel) {
        gl.bindTexture(gl.TEXTURE_2D, panelTexture);
        gl.bindVertexArray(panelVao);
        gl.uniform1i(uniforms.textured, 1);
        gl.uniform1i(uniforms.unlit, 1);
        gl.uniform1i(uniforms.spot, 0);
        gl.uniformMatrix4fv(uniforms.pose, false, panel.matrix);
        gl.uniform3fv(uniforms.size, [panel.width, panel.height, 1]);
        gl.uniform3fv(uniforms.offset, [0, panel.y, panel.z]);
        gl.uniform4fv(uniforms.color, [1, 1, 1, 1]);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      }
      gl.bindTexture(gl.TEXTURE_2D, whiteTexture);
      for (const tracked of showControllers ? poses : []) {
        const color = tracked.handedness === 'left' ? [0.34, 0.7, 1, 1] : [0.9, 0.92, 0.96, 1];
        const asset = assets.get(controllerProfile(tracked.source));
        if (asset?.state === 'pending') continue;
        const model = tracked.grip && asset?.state === 'ready' && asset.models.get(tracked.handedness);
        if (model) {
          // shortcut: buttons retain the upstream rest pose, animate only if input feedback needs it.
          gl.bindTexture(gl.TEXTURE_2D, model.texture);
          gl.bindVertexArray(model.vao);
          gl.uniform1i(uniforms.textured, 1);
          gl.uniform1i(uniforms.unlit, 0);
          gl.uniform1i(uniforms.spot, 0);
          gl.uniformMatrix4fv(uniforms.pose, false, tracked.grip);
          gl.uniform3fv(uniforms.size, [1, 1, 1]);
          gl.uniform3fv(uniforms.offset, [0, 0, 0]);
          gl.uniform4fv(uniforms.color, [1, 1, 1, 1]);
          gl.drawElements(gl.TRIANGLES, model.count, gl.UNSIGNED_SHORT, 0);
        } else {
          box(tracked.grip || tracked.ray, [0.034, 0.085, 0.038], [0, -0.025, 0.015], [0.2, 0.24, 0.3, 1]);
          box(tracked.grip || tracked.ray, [0.052, 0.025, 0.065], [0, 0.025, -0.005], color);
        }
      }
      if (beam && Number.isFinite(beam.distance) && beam.distance > 0) {
        const start = Math.min(0.04, beam.distance / 2);
        const thickness = handPointer ? 0.004 : 0.0025;
        box(beam.matrix, [thickness, thickness, beam.distance - start], [0, 0, -(beam.distance + start) / 2], [0.15, 0.9, 1, 0.85], true);
      }
      if (panel && (cursor || (handPointer && beam))) {
        gl.bindVertexArray(panelVao);
        gl.uniform1i(uniforms.textured, 0);
        gl.uniform1i(uniforms.unlit, 1);
        gl.uniform1i(uniforms.spot, handPointer ? 2 : 1);
        gl.uniformMatrix4fv(uniforms.pose, false, cursor ? panel.matrix : beam.matrix);
        const diameter = handPointer ? 0.019 : 0.012;
        gl.uniform3fv(uniforms.size, [diameter, diameter, 1]);
        gl.uniform3fv(uniforms.offset, cursor ? [cursor.x, panel.y + cursor.y, panel.z + 0.0003] : [0, 0, -beam.distance]);
        gl.uniform4fv(uniforms.color, [0.15, 0.9, 1, 1]);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      }
    }
    gl.bindVertexArray(null);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.bindTexture(gl.TEXTURE_2D, null);
    gl.disable(gl.SCISSOR_TEST);
    gl.disable(gl.BLEND);
    gl.disable(gl.DEPTH_TEST);
  }

  return { layer, update, markActive, draw, uploadPanel, close,
    get ready() { if (closed) return Promise.resolve(false); return Promise.all([...assets.values()].map(asset => asset.ready)).then(results => results.every(Boolean)); } };
}
