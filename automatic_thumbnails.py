"""Feed one low-priority thumbnail at a time while LANStream is unused."""
import logging
import threading
import time


class AutomaticThumbnails:
    def __init__(self, scheduler, candidates, idle_seconds=60, enabled=True,
                 poll_seconds=.2, rescan_seconds=300):
        self.scheduler, self.candidates = scheduler, candidates
        self.idle_seconds = max(0, idle_seconds)
        self.poll_seconds, self.rescan_seconds = max(.01, poll_seconds), max(.1, rescan_seconds)
        self._enabled, self._last_activity = bool(enabled), time.monotonic()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._status = dict(state='waiting', checked=0, ready=0, failed=0, error=None)
        scheduler.set_automatic_eligibility(self._idle)
        self._thread = threading.Thread(target=self._run, name='automatic-thumbnails', daemon=True)
        self._thread.start()

    def _idle(self):
        # Called under the scheduler lock; never acquire the feeder lock here.
        return self._enabled and not self._stop.is_set() and time.monotonic() - self._last_activity >= self.idle_seconds

    def touch(self):
        self._last_activity = time.monotonic()
        self.scheduler.cancel_automatic()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        self.touch()

    def status(self):
        with self._lock:
            status = dict(self._status, enabled=self._enabled)
        if not self._enabled:
            status['state'] = 'disabled'
        elif not self.scheduler.automatic_allowed():
            status['state'] = 'waiting'
        return status

    def _update(self, **values):
        with self._lock:
            self._status.update(values)

    def _run(self):
        sources, current, next_scan = None, None, 0
        while not self._stop.is_set():
            try:
                if not self.scheduler.automatic_allowed():
                    self._stop.wait(self.poll_seconds)
                    continue
                if current is not None:
                    source, output, width = current
                    if self.scheduler.scheduled(output):
                        self._stop.wait(self.poll_seconds)
                        continue
                    if self.scheduler.cached(source, output):
                        with self._lock:
                            self._status['ready'] += 1
                        current = None
                    elif self.scheduler.failed(0, output) or self.scheduler._fingerprint(source) is None:
                        with self._lock:
                            self._status['failed'] += 1
                        current = None
                    else:
                        self.scheduler.submit(0, 30, source, output, width)
                        self._stop.wait(self.poll_seconds)
                        continue
                if sources is None:
                    if time.monotonic() < next_scan:
                        self._update(state='error' if self._status['error'] else 'complete')
                        self._stop.wait(self.poll_seconds)
                        continue
                    sources = iter(self.candidates(self._stop))
                    self._update(state='preparing', checked=0, ready=0, failed=0, error=None)
                try:
                    current = next(sources)
                    with self._lock:
                        self._status['checked'] += 1
                except StopIteration:
                    sources = None
                    next_scan = time.monotonic() + self.rescan_seconds
            except Exception:
                logging.getLogger(__name__).exception('Automatic thumbnail preparation failed')
                sources, current = None, None
                next_scan = time.monotonic() + self.rescan_seconds
                self._update(state='error', error='Could not read the library for automatic thumbnails.')
                self._stop.wait(self.poll_seconds)

    def close(self, wait=False):
        self._stop.set()
        self.scheduler.cancel_automatic()
        self._thread.join(timeout=None if wait else 1)
