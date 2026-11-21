(() => {
  const embeddedVideo = document.getElementById('vrVideo');
  const video = embeddedVideo || document.getElementById('video');
  const stage = document.getElementById('vrVideoStage');
  const enter = document.getElementById('enterVr');
  const projection = document.getElementById('vrProjection');
  const hands = document.getElementById('vrHands');
  const handsChoice = document.getElementById('vrHandChoice');
  const status = document.getElementById('vrStatus');
  const fullscreenHint = status.textContent;
  const playbackError = 'This browser could not play the original video or audio format.';
  const fisheyeHint = 'Dual fisheye playback requires HTTPS and a browser with immersive WebXR Layers.';
  const controlsHint = 'Pull the trigger to show controls. With Hand input on, hold a pinch for 0.7 seconds to show or hide the panel. Point and pinch briefly to select, or pinch and drag the timeline to seek.';
  let session, integration, pendingError;
  let supported = false, attempt = 0, entering = false;

  function isHand(source) {
    return Boolean(source.hand || source.profiles?.some(profile => profile.startsWith('generic-hand')));
  }

  function loadOriginal() {
    if (!video.hasAttribute('src')) video.src = video.dataset.src;
  }

  function showNativePlayback(message = fullscreenHint) {
    if (embeddedVideo && !integration) return;
    if (projection?.value === 'fisheye') {
      video.pause();
      stage.hidden = true;
      enter.hidden = handsChoice.hidden = true;
      status.textContent = message === fullscreenHint ? fisheyeHint : message;
      return;
    }
    enter.hidden = handsChoice.hidden = true;
    stage.hidden = false;
    video.controls = true;
    loadOriginal();
    status.textContent = message;
  }

  function matchRefreshRate(active) {
    const [numerator, denominator] = video.dataset.frameRate.split('/').map(Number);
    const fps = numerator / denominator;
    if (!Number.isFinite(fps) || fps <= 0 || !active.supportedFrameRates) return;
    const matches = Array.from(active.supportedFrameRates).filter(rate => {
      const multiple = Math.round(rate / fps);
      return multiple >= 1 && Math.abs(rate - multiple * fps) < 0.5;
    });
    if (matches.length && !matches.includes(active.frameRate)) {
      active.updateTargetFrameRate?.(Math.min(...matches)).catch(() => {});
    }
  }

  function startControls(active, space, movieLayer) {
    const gl = document.createElement('canvas').getContext('webgl2', { xrCompatible: true });
    const canvas = document.createElement('canvas');
    const paint = canvas.getContext('2d');
    if (!gl || !paint) throw new Error('WebGL controls unavailable.');

    const panelWidth = 640, panelHeight = 128;
    const width = 0.71, height = width / 5, y = -0.30, z = -1.65;
    const timelineStart = 97, timelineEnd = 544, timelineY = 31;
    const buttons = [
      ['back', 80, 68], ['play', 181, 68], ['forward', 282, 68], ['exit', 402, 68], ['hands', 537, 116],
    ];
    // Tabler Icons (MIT), rotated into the mockup's circular skip symbols.
    const rotate = new Path2D('M19.95 11a8 8 0 1 0 -.5 4m.5 5v-5h-5');
    const exit = new Path2D('M14 8v-2a2 2 0 0 0 -2 -2h-7a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h7a2 2 0 0 0 2 -2v-2 M9 12h12l-3 -3 M18 15l3 -3');
    const play = new Path2D('M7 4v16l13 -8l-13 -8z');
    const pause = new Path2D('M6 6a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v12a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1z M14 6a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1v12a1 1 0 0 1 -1 1h-2a1 1 0 0 1 -1 -1z');

    canvas.width = panelWidth * 2;
    canvas.height = panelHeight * 2;
    paint.scale(2, 2);
    const shade = paint.createLinearGradient(0, 0, panelWidth, panelHeight);
    shade.addColorStop(0, 'rgba(32, 34, 34, .94)');
    shade.addColorStop(1, 'rgba(24, 26, 27, .94)');
    paint.textBaseline = 'middle';
    paint.textAlign = 'center';

    const overlay = createVrControllerLayer(active, new XRWebGLBinding(active, gl), gl, space, movieLayer ? undefined : video);
    const handGestures = new Map();
    let handMenu = false;
    function supportsHands() {
      return !active.enabledFeatures || active.enabledFeatures.includes('hand-tracking') || Array.from(active.inputSources).some(isHand);
    }
    let handTracking = supportsHands(), lastHand;
    let panel, inverse, showAtView = true, hideAt = 0, drawn = '', scrub, aimSource;
    let submitted = !movieLayer, drag, placed = false;
    if (!movieLayer) active.updateRenderState({ layers: [overlay.layer] });
    active.addEventListener('visibilitychange', () => {
      if (active.visibilityState === 'visible') return;
      // Give the system menu the view and cancel any unfinished seek.
      hideAt = 0;
      showAtView = false;
      scrub = undefined;
      drag = undefined;
      handGestures.clear();
      handMenu = false;
      if (movieLayer && submitted) {
        active.updateRenderState({ layers: [movieLayer] });
        submitted = false;
      }
    });
    active.addEventListener('end', overlay.close, { once: true });
    active.addEventListener('inputsourceschange', event => {
      handTracking = supportsHands();
      for (const source of event.removed) {
        handGestures.delete(source);
        if (scrub?.source === source) scrub = undefined;
        if (drag?.source === source) drag = undefined;
        if (lastHand === source) lastHand = undefined;
        if (aimSource === source) aimSource = undefined;
      }
      drawn = '';
    });
    active.addEventListener('squeezestart', event => {
      if (event.inputSource.targetRayMode === 'tracked-pointer' && !isHand(event.inputSource)) {
        aimSource = event.inputSource;
        overlay.markActive(performance.now());
      }
    });

    function panelHit(frame, source, allowFreeRay = false) {
      const matrix = frame.getPose(source.targetRaySpace, space)?.transform.matrix;
      if (!matrix) return null;
      // The same target-ray pose feeds the visible beam and the local panel intersection.
      const ox = matrix[12], oy = matrix[13], oz = matrix[14];
      const dx = matrix[8], dy = matrix[9], dz = matrix[10];
      const toward = inverse[2] * dx + inverse[6] * dy + inverse[10] * dz;
      if (toward <= 0.0001) return allowFreeRay ? { matrix, distance: -z } : null;
      const distance = (inverse[2] * ox + inverse[6] * oy + inverse[10] * oz + inverse[14] - z) / toward;
      if (distance <= 0) return allowFreeRay ? { matrix, distance: -z } : null;
      const hitX = ox - distance * dx, hitY = oy - distance * dy, hitZ = oz - distance * dz;
      const x = inverse[0] * hitX + inverse[4] * hitY + inverse[8] * hitZ + inverse[12];
      const yy = inverse[1] * hitX + inverse[5] * hitY + inverse[9] * hitZ + inverse[13] - y;
      const hit = { matrix, distance, cursor: { x, y: yy },
        x: (0.5 + x / width) * panelWidth, y: (0.5 - yy / height) * panelHeight };
      return allowFreeRay && !insidePanel(hit) ? { matrix, distance: -z } : hit;
    }

    function insidePanel(hit) {
      return hit && hit.x >= 0 && hit.x < panelWidth && hit.y >= 0 && hit.y < panelHeight;
    }

    function pointOnRay(matrix, distance) {
      return [matrix[12] - distance * matrix[8], matrix[13] - distance * matrix[9],
        matrix[14] - distance * matrix[10]];
    }

    function movePanel(frame) {
      const ray = frame.getPose(drag.source.targetRaySpace, space);
      if (!ray) {
        drag = undefined;
        return;
      }
      // Keep the grabbed point under the ray at its original distance, preserving the panel's tilt.
      const point = pointOnRay(ray.transform.matrix, drag.distance);
      for (let i = 0; i < 3; i++) panel.matrix[12 + i] = point[i] + drag.offset[i];
      for (let i = 0; i < 3; i++) {
        inverse[12 + i] = -(inverse[i] * panel.matrix[12] + inverse[i + 4] * panel.matrix[13] +
          inverse[i + 8] * panel.matrix[14]);
      }
    }

    function panelAction(hit) {
      if (!insidePanel(hit)) return;
      if (hit.y >= 60 && hit.y <= 112) {
        return buttons.find(([, center, size]) => Math.abs(hit.x - center) <= size / 2)?.[0];
      }
      if (hit.y >= 12 && hit.y <= 52 && hit.x >= timelineStart - 10 && hit.x <= timelineEnd + 10) return 'seek';
    }

    function seekTime(hit) {
      const fraction = Math.max(0, Math.min(1, (hit.x - timelineStart) / (timelineEnd - timelineStart)));
      return fraction * video.duration;
    }

    function timeLabel(time) {
      const seconds = Math.max(0, Math.floor(time));
      const minutes = Math.floor(seconds / 60);
      const pad = value => String(value).padStart(2, '0');
      const minuteLabel = minutes < 60 ? pad(minutes) : Math.floor(minutes / 60) + ':' + pad(minutes % 60);
      return minuteLabel + ':' + pad(seconds % 60);
    }

    function paintPanel(hover) {
      const elapsed = scrub?.time ?? video.currentTime;
      const duration = Number.isFinite(video.duration) ? video.duration : 0;
      const position = Math.round(timelineStart + (duration ? (timelineEnd - timelineStart) * elapsed / duration : 0));
      const state = [hover, video.paused, Math.floor(elapsed), duration, position, hands.checked].join('|');
      if (state === drawn) return;
      drawn = state;
      paint.clearRect(0, 0, panelWidth, panelHeight);
      paint.beginPath();
      paint.roundRect(1, 1, panelWidth - 2, panelHeight - 2, 23);
      paint.fillStyle = shade;
      paint.fill();
      paint.lineWidth = 1;
      paint.strokeStyle = 'rgba(255, 255, 255, .24)';
      paint.stroke();

      paint.fillStyle = '#f5f7fa';
      paint.font = '400 18px system-ui';
      paint.fillText(timeLabel(elapsed), 53, timelineY);
      paint.fillText(timeLabel(duration), 588, timelineY);
      paint.lineWidth = 6;
      paint.lineCap = 'round';
      paint.beginPath();
      paint.moveTo(timelineStart, timelineY);
      paint.lineTo(timelineEnd, timelineY);
      paint.strokeStyle = '#44494c';
      paint.stroke();
      paint.beginPath();
      paint.moveTo(timelineStart, timelineY);
      paint.lineTo(position, timelineY);
      paint.fillStyle = paint.strokeStyle = hover === 'seek' || scrub ? '#61a1ff' : '#347df4';
      paint.stroke();
      paint.beginPath();
      paint.arc(position, timelineY, 8, 0, 2 * Math.PI);
      paint.shadowBlur = 11;
      paint.shadowColor = '#387cf2';
      paint.fill();
      paint.shadowBlur = 0;

      for (const [action, x, size] of buttons) {
        if (action === 'hands' || action === hover) {
          paint.beginPath();
          paint.roundRect(x - size / 2, 69, size, 34, 17);
          if (action === hover) {
            paint.fillStyle = 'rgba(103, 167, 232, .20)';
            paint.fill();
          }
          if (action === 'hands') {
            paint.lineWidth = 1.4;
            paint.strokeStyle = hands.checked ? '#71baff' : '#999da2';
            paint.stroke();
          }
        }
        paint.fillStyle = paint.strokeStyle = '#f5f7fa';
        if (action === 'hands') {
          if (!handTracking) paint.fillStyle = '#a6aab0';
          paint.fillText(handTracking ? (hands.checked ? 'Hands on' : 'Hands off') : 'Hands N/A', x, 86);
          continue;
        }
        paint.save();
        paint.translate(x, 86);
        paint.scale(1.55, 1.55);
        paint.lineWidth = 1.75;
        paint.lineJoin = 'round';
        if (action === 'exit') {
          paint.translate(-12, -12);
          paint.stroke(exit);
        } else if (action === 'play') {
          paint.translate(-12, -12);
          paint.fill(video.paused ? play : pause);
        } else {
          paint.rotate(-Math.PI / 2);
          if (action === 'forward') paint.scale(1, -1);
          paint.translate(-12, -12);
          paint.stroke(rotate);
        }
        paint.restore();
        if (action === 'back' || action === 'forward') {
          paint.font = '600 13px system-ui';
          paint.fillText('15', x, 87);
          paint.font = '400 18px system-ui';
        }
      }
      overlay.uploadPanel(canvas);
    }

    function startAction(source, hit, now) {
      const action = panelAction(hit);
      const seekable = Number.isFinite(video.duration) && video.duration > 0;
      if (!action && insidePanel(hit)) {
        const point = pointOnRay(hit.matrix, hit.distance);
        panel.matrix = panel.matrix.slice();
        inverse = inverse.slice();
        drag = { source, distance: hit.distance, offset: point.map((v, i) => panel.matrix[12 + i] - v) };
        placed = true;
      } else if (action === 'seek' && seekable) scrub = { source, time: seekTime(hit) };
      else if (action === 'play') togglePlayback();
      else if (action === 'exit') active.end().catch(() => {});
      else if (action === 'hands' && handTracking) {
        hands.checked = !hands.checked;
        if (!hands.checked) {
          handGestures.clear();
          handMenu = false;
        }
      } else if ((action === 'back' || action === 'forward') && seekable) {
        video.currentTime = Math.min(video.duration, Math.max(0, video.currentTime + (action === 'back' ? -15 : 15)));
      }
      hideAt = handMenu ? Infinity : now + 5000;
    }

    function updateHand(source, frame, now) {
      const gesture = handGestures.get(source);
      if (!gesture) return;
      const ray = frame.getPose(source.targetRaySpace, space);
      if (!hands.checked || !ray || !Array.from(active.inputSources).includes(source)) {
        handGestures.delete(source);
        if (scrub?.source === source) scrub = undefined;
        if (drag?.source === source) drag = undefined;
        return;
      }
      if (gesture.toggled) return;
      const point = pointOnRay(ray.transform.matrix, gesture.distance);
      if (!gesture.moved && gesture.visible && insidePanel(gesture.hit) && !scrub && !drag &&
        (!gesture.action || gesture.action === 'seek') &&
        Math.hypot(...point.map((v, i) => v - gesture.point[i])) > 0.025) {
        startAction(source, gesture.hit, now);
        gesture.moved = scrub?.source === source || drag?.source === source;
      }
      if (!gesture.moved && !scrub && !drag && now - gesture.start >= 700) {
        // Consume both hands until release so one held gesture cannot toggle twice.
        for (const pending of handGestures.values()) pending.toggled = true;
        scrub = drag = undefined;
        handMenu = !hideAt;
        hideAt = handMenu && panel ? Infinity : 0;
        showAtView = handMenu && !placed;
      }
    }

    active.addEventListener('selectstart', event => {
      if (active.visibilityState && active.visibilityState !== 'visible') return;
      const now = performance.now();
      const source = event.inputSource;
      if (isHand(source)) {
        if (!hands.checked || !handTracking || handGestures.has(source)) return;
        const ray = event.frame.getPose(source.targetRaySpace, space);
        if (!ray || !Array.from(active.inputSources).includes(source)) return;
        lastHand = source;
        const hit = panel && panelHit(event.frame, source);
        const distance = Math.min(hit?.distance || 1.5, 2);
        handGestures.set(source, { start: now, hit, action: panelAction(hit), distance,
          point: pointOnRay(ray.transform.matrix, distance), visible: Boolean(hideAt),
          toggled: Array.from(handGestures.values()).some(gesture => gesture.toggled), moved: false });
        return;
      }
      if (source.targetRayMode !== 'tracked-pointer') return;
      overlay.markActive(now);
      aimSource = source;
      if (scrub || drag) return;
      handGestures.clear();
      if (handMenu) {
        handMenu = false;
        hideAt = now + 5000;
      }
      if (!hideAt) {
        if (placed) hideAt = now + 5000;
        else showAtView = true;
        return;
      }
      startAction(source, panelHit(event.frame, source), now);
    });
    active.addEventListener('select', event => {
      const source = event.inputSource;
      if (isHand(source)) {
        updateHand(source, event.frame, performance.now());
        const gesture = handGestures.get(source);
        if (!gesture || gesture.toggled) return;
        if (!gesture.moved && gesture.action && gesture.visible && hideAt && !scrub && !drag) {
          const hit = panelHit(event.frame, source);
          if (panelAction(hit) === gesture.action) startAction(source, hit, performance.now());
        }
        // A second select from the same pinch cannot activate another control.
        gesture.toggled = true;
      }
      if (drag?.source === source) movePanel(event.frame);
      if (!scrub || scrub.source !== source || (isHand(source) && !hands.checked)) return;
      const hit = panelHit(event.frame, source);
      video.currentTime = hit ? seekTime(hit) : scrub.time;
    });
    active.addEventListener('selectend', event => {
      const now = performance.now();
      if (isHand(event.inputSource)) {
        handGestures.delete(event.inputSource);
      } else if (event.inputSource.targetRayMode === 'tracked-pointer') overlay.markActive(now);
      if (scrub?.source === event.inputSource) {
        scrub = undefined;
        hideAt = handMenu ? Infinity : now + 5000;
      }
      if (drag?.source === event.inputSource) {
        drag = undefined;
        hideAt = handMenu ? Infinity : now + 5000;
      }
    });

    function frame(time, xrFrame) {
      if (session !== active) return;
      try {
        if (active.visibilityState && active.visibilityState !== 'visible') {
          if (!movieLayer && active.renderState.layers.includes(overlay.layer)) {
            overlay.draw(xrFrame, { showControllers: false });
          }
          active.requestAnimationFrame(frame);
          return;
        }
        if (showAtView) {
          const transform = xrFrame.getViewerPose(space)?.transform;
          if (transform) {
            panel = { matrix: transform.matrix, width, height, y, z };
            inverse = transform.inverse.matrix;
            showAtView = false;
            hideAt = handMenu ? Infinity : time + 5000;
          }
        }
        for (const source of handGestures.keys()) updateHand(source, xrFrame, time);
        const pendingHand = Array.from(handGestures.values()).some(gesture => gesture.visible && !gesture.toggled);
        if (!scrub && !drag && !pendingHand && hideAt && time >= hideAt) {
          hideAt = 0;
        }
        if (drag) movePanel(xrFrame);
        if (scrub) {
          const hit = panelHit(xrFrame, scrub.source);
          if (hit) scrub.time = seekTime(hit);
        }

        const showControllers = overlay.update(time, xrFrame);
        const visible = !movieLayer || Boolean(hideAt || showControllers);
        if (movieLayer && visible !== submitted) {
          active.updateRenderState({ layers: visible ? [movieLayer, overlay.layer] : [movieLayer] });
          submitted = visible;
        }
        if (visible && active.renderState.layers.includes(overlay.layer)) {
          let aiming, priority = -Infinity;
          if (hideAt) {
            for (const source of active.inputSources) {
              const hand = isHand(source);
              if (hand ? !hands.checked || !handTracking
                : !showControllers || source.targetRayMode !== 'tracked-pointer') continue;
              if ((scrub && scrub.source !== source) || (drag && drag.source !== source)) continue;
              const hit = panelHit(xrFrame, source, hand);
              if (!hit || (!hand && !insidePanel(hit))) continue;
              // Keep an active controller ahead of hovering hands until a pinch takes over.
              const preferred = hand ? lastHand : aimSource;
              let rank = source === preferred ? 2 : Number(source.handedness === 'right');
              if (hand) rank = handGestures.has(source) ? 3 : rank - 3;
              // With two resting hands, show the one pointing at a control.
              if (hand && !hit.cursor && !handGestures.has(source)) rank -= 3;
              if (rank <= priority) continue;
              priority = rank;
              aiming = { hit, hand };
            }
            paintPanel(panelAction(aiming?.hit));
          }
          overlay.draw(xrFrame, {
            panel: hideAt ? panel : undefined, showControllers, cursor: aiming?.hit.cursor,
            beam: aiming?.hit, handPointer: aiming?.hand,
          });
        }
        active.requestAnimationFrame(frame);
      } catch (error) {
        console.warn('VR controls unavailable', error);
        if (!movieLayer) {
          const message = 'Unable to render dual fisheye. ' + error.message;
          status.textContent = message;
          stop(message);
        } else {
          const message = 'Unable to render VR180. ' + fullscreenHint;
          if (embeddedVideo) stop(message);
          else active.end().then(() => showNativePlayback(message)).catch(() => {});
        }
      }
    }
    active.requestAnimationFrame(frame);
  }

  function togglePlayback() {
    if (video.paused) video.play().catch(reportPlaybackError);
    else video.pause();
  }

  function reportPlaybackError() {
    enter.disabled = true;
    status.textContent = playbackError;
    if (embeddedVideo) stop(playbackError);
    else if (session) session.end().catch(() => {});
  }
  video.addEventListener('error', reportPlaybackError);

  function finish() {
    entering = false;
    enter.disabled = Boolean(video.error);
    if (!integration) return;
    const { time, onEnd } = integration;
    const at = video.hasAttribute('src') && video.readyState >= 1 ? video.currentTime : time;
    integration = undefined;
    video.pause();
    video.removeAttribute('src');
    video.load();
    stage.hidden = true;
    video.controls = false;
    enter.hidden = false;
    enter.disabled = false;
    const error = pendingError;
    pendingError = undefined;
    onEnd(at, error);
  }

  function stop(error) {
    ++attempt;
    pendingError = error;
    if (session) session.end().catch(finish);
    else finish();
  }

  async function start(options) {
    if (session || entering || enter.disabled) return;
    const generation = ++attempt;
    entering = true;
    integration = options;
    pendingError = undefined;
    options?.beforeStart();
    enter.disabled = true;
    function prepareOriginal() {
      if (options) {
        video.volume = options.volume;
        video.addEventListener('loadedmetadata', () => {
          if (generation === attempt && integration === options) video.currentTime = options.time;
        }, { once: true });
      }
      loadOriginal();
    }
    const fisheye = projection?.value === 'fisheye';
    if (fisheye && !supported) {
      pendingError = status.textContent = fisheyeHint;
      finish();
      return;
    }
    if (!fisheye && embeddedVideo && (!supported || typeof XRMediaBinding !== 'function')) {
      prepareOriginal();
      showNativePlayback();
      video.play().catch(reportPlaybackError);
      try {
        if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
        else video.requestFullscreen?.().catch(() => {});
      } catch {}
      return;
    }
    try {
      const active = await navigator.xr.requestSession('immersive-vr', {
        requiredFeatures: ['layers'], optionalFeatures: ['hand-tracking'],
      });
      if (generation !== attempt) { await active.end(); return; }
      session = active;
      active.addEventListener('end', () => {
        session = undefined;
        video.pause();
        finish();
      }, { once: true });
      prepareOriginal();
      await video.play();
      if (session !== active) return;
      const space = await active.requestReferenceSpace('local');
      if (session !== active) return;
      const layer = fisheye ? null : new XRMediaBinding(active).createEquirectLayer(video, {
        space, centralHorizontalAngle: Math.PI, layout: 'stereo-left-right',
      });
      if (layer) active.updateRenderState({ layers: [layer] });
      matchRefreshRate(active);
      startControls(active, space, layer);
    } catch (error) {
      if (generation !== attempt) return;
      const reason = fisheye ? 'Unable to start dual fisheye. ' + error.message : 'Unable to start VR. Tap Play to resume playback.';
      if (embeddedVideo) pendingError = video.error ? playbackError : reason;
      if (session) await session.end().catch(() => {});
      if (embeddedVideo) { finish(); return; }
      entering = false;
      enter.disabled = Boolean(video.error);
      if (video.error) reportPlaybackError();
      else showNativePlayback(fisheye ? reason : 'Unable to start VR180. ' + fullscreenHint);
    }
  }

  if (embeddedVideo) {
    globalThis.lanstreamVr = { start, stop };
    video.addEventListener('webkitendfullscreen', () => { if (integration && video.controls && !session) stop(); });
    document.addEventListener('fullscreenchange', () => {
      if (integration && video.controls && !session && !document.fullscreenElement) stop();
    });
  } else enter.addEventListener('click', () => start());

  function ready(canEnter) {
    supported = canEnter;
    enter.disabled = false;
    if (canEnter && (projection?.value === 'fisheye' || typeof XRMediaBinding === 'function')) {
      if (!embeddedVideo && !stage.hidden) {
        video.pause();
        video.controls = false;
        stage.hidden = true;
      }
      enter.hidden = handsChoice.hidden = false;
      status.textContent = controlsHint;
    } else if (!embeddedVideo) showNativePlayback();
  }
  projection?.addEventListener('change', () => ready(supported));
  if (!navigator.xr || typeof XRWebGLBinding !== 'function') ready(false);
  else navigator.xr.isSessionSupported('immersive-vr').then(ready).catch(() => ready(false));
})();
