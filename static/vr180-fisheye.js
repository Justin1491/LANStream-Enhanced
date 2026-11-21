function createVrFisheyeRenderer(gl, video) {
  const maximum = gl.getParameter(gl.MAX_TEXTURE_SIZE);
  const program = gl.createProgram(), vao = gl.createVertexArray(), texture = gl.createTexture();
  let closed = false, callback, dirty = true, frameTime, lastTime = NaN, lastFrames;
  let width = 0, height = 0;

  function invalidate() {
    dirty = true;
    frameTime = undefined;
    lastTime = NaN;
    lastFrames = undefined;
  }

  function close() {
    if (closed) return;
    closed = true;
    if (callback !== undefined) video.cancelVideoFrameCallback?.(callback);
    for (const event of ['loadeddata', 'seeked', 'emptied']) video.removeEventListener(event, invalidate);
    gl.deleteTexture(texture);
    gl.deleteVertexArray(vao);
    gl.deleteProgram(program);
  }

  try {
    for (const [type, source] of [
      [gl.VERTEX_SHADER, `#version 300 es
        uniform mat4 projection, rotation;
        out highp vec3 direction;
        void main() {
          vec2 corner = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
          vec2 screen = corner * 2.0 - 1.0;
          direction = mat3(rotation) * vec3((screen.x + projection[2][0]) / projection[0][0],
            (screen.y + projection[2][1]) / projection[1][1], -1.0);
          gl_Position = vec4(screen, 1.0, 1.0);
        }`],
      [gl.FRAGMENT_SHADER, `#version 300 es
        precision highp float;
        uniform sampler2D movie;
        uniform vec2 dimensions;
        uniform float eye;
        in highp vec3 direction;
        out vec4 fragment;
        void main() {
          vec3 ray = normalize(direction);
          float radial = length(ray.xy);
          float angle = atan(radial, -ray.z);
          if (ray.z > 0.0) { fragment = vec4(0, 0, 0, 1); return; }
          vec2 circle = radial > 0.000001 ? ray.xy / radial * (angle / 1.5707963267948966) : vec2(0);
          float radius = min(dimensions.x * 0.5, dimensions.y) * 0.5;
          vec2 uv = vec2(eye * 0.5 + 0.25, 0.5) + circle * radius / dimensions;
          uv.x = clamp(uv.x, eye * 0.5 + 0.5 / dimensions.x, (eye + 1.0) * 0.5 - 0.5 / dimensions.x);
          fragment = vec4(texture(movie, uv).rgb, 1);
        }`],
    ]) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const error = gl.getShaderInfoLog(shader);
        gl.deleteShader(shader);
        throw new Error(error || 'Unable to compile VR fisheye shader.');
      }
      gl.attachShader(program, shader);
      gl.deleteShader(shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program) || 'Unable to link VR fisheye shader.');
    }
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindTexture(gl.TEXTURE_2D, null);
    for (const event of ['loadeddata', 'seeked', 'emptied']) video.addEventListener(event, invalidate);
    if (typeof video.requestVideoFrameCallback === 'function') {
      const decoded = (_, metadata) => {
        if (closed) return;
        frameTime = metadata.mediaTime;
        if (frameTime !== lastTime) dirty = true;
        callback = video.requestVideoFrameCallback(decoded);
      };
      callback = video.requestVideoFrameCallback(decoded);
    }
  } catch (error) {
    close();
    throw error;
  }
  const uniforms = Object.fromEntries(['projection', 'rotation', 'movie', 'dimensions', 'eye']
    .map(name => [name, gl.getUniformLocation(program, name)]));

  function upload() {
    if (closed || video.seeking || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
    if (video.videoWidth > maximum || video.videoHeight > maximum) {
      throw new Error(`This fisheye video is ${video.videoWidth}×${video.videoHeight}; this headset supports video textures up to ${maximum}×${maximum}.`);
    }
    const frames = video.getVideoPlaybackQuality?.().totalVideoFrames;
    const time = dirty ? frameTime ?? video.currentTime : video.currentTime;
    const resized = width !== video.videoWidth || height !== video.videoHeight;
    // Quest may throttle video callbacks while the source element is hidden.
    const changed = dirty || (frames !== undefined ? frames !== lastFrames
      : typeof video.requestVideoFrameCallback !== 'function' && time !== lastTime);
    if (!resized && (!changed || time === lastTime)) return;
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    try {
      if (resized) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
      else gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, video);
      if (gl.getError() !== gl.NO_ERROR) throw new Error('Unable to upload the original fisheye video to this headset.');
      width = video.videoWidth;
      height = video.videoHeight;
      lastTime = time;
      lastFrames = frames;
      dirty = false;
    } finally {
      gl.bindTexture(gl.TEXTURE_2D, null);
    }
  }

  function draw(view) {
    if (closed || !width || !height) return;
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.BLEND);
    gl.useProgram(program);
    gl.bindVertexArray(vao);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.uniform1i(uniforms.movie, 0);
    gl.uniform2f(uniforms.dimensions, width, height);
    gl.uniform1f(uniforms.eye, view.eye === 'right' ? 1 : 0);
    gl.uniformMatrix4fv(uniforms.projection, false, view.projectionMatrix);
    // Only rotation follows the head: viewer translation must not move a distant movie.
    gl.uniformMatrix4fv(uniforms.rotation, false, view.transform.matrix);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  return { upload, draw, close };
}
