(() => {
  const token = document.querySelector('meta[name="lanstream-csrf"]')?.content;
  if (!token) return;
  let lastSent = -Infinity, stopped = false;
  function report() {
    const playing = [...document.querySelectorAll('video, audio')].some(media => !media.paused && !media.ended);
    if (stopped || (document.hidden && !playing) || Date.now() - lastSent < 15000) return;
    lastSent = Date.now();
    fetch('/api/activity', {method: 'POST', headers: {'X-LANStream-CSRF': token}, keepalive: true}).catch(() => {});
  }
  for (const event of ['pointerdown', 'keydown', 'scroll', 'visibilitychange']) document.addEventListener(event, report, {passive:true});
  let timer = setInterval(report, 20000);
  window.addEventListener('pagehide', () => { stopped = true; clearInterval(timer); });
  window.addEventListener('pageshow', () => { if (stopped) timer = setInterval(report, 20000); stopped = false; report(); });
  report();
})();
