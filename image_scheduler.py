"""Bounded thumbnail work that survives folder navigation."""
import logging
import threading
import time
from pathlib import Path


class ImageScheduler:
    def __init__(self, generator, workers=1, max_pending=64, retry_seconds=60, streaming=None):
        self._generator = generator
        self._limit = max(1, int(max_pending))
        self._retry_seconds = max(0, retry_seconds)
        self._streaming = streaming or (lambda: False)
        self._condition = threading.Condition()
        self._pending = {}
        self._active = {}
        self._failures = {}
        self._fingerprints = {}
        self._sequence = 0
        self._completed = 0
        self._paused = False
        self._closed = False
        self._threads = [threading.Thread(target=self._worker, name=f'image-worker-{i}', daemon=True)
                         for i in range(max(1, min(2, int(workers))))]
        for thread in self._threads:
            thread.start()

    @staticmethod
    def _fingerprint(source):
        try:
            stat = Path(source).stat()
            return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)
        except OSError:
            return None

    def new_generation(self):
        return 0

    def current_generation(self):
        return 0

    def cached(self, source, output):
        source, output = Path(source), Path(output)
        fingerprint = self._fingerprint(source)
        if fingerprint is None:
            return False
        try:
            # shortcut: legacy images use timestamps after restart, persist fingerprints if replacements preserve timestamps.
            fresh = output.is_file() and output.stat().st_mtime_ns >= fingerprint[1]
        except OSError:
            return False
        with self._condition:
            known = self._fingerprints.get(str(output))
            if known is not None and known != fingerprint:
                return False
            if fresh:
                self._fingerprints[str(output)] = fingerprint
            return fresh

    def is_current(self, source, output):
        fingerprint = self._fingerprint(source)
        with self._condition:
            job = self._active.get(str(output))
            return job is not None and job['source'] == Path(source) and fingerprint == job['fingerprint']

    def mark_failed(self, generation, output):
        key = str(output)
        with self._condition:
            job = self._active.get(key)
            fingerprint = job['fingerprint'] if job else self._fingerprints.get(key)
            self._failures[key] = (fingerprint, time.monotonic() + self._retry_seconds)

    def failed(self, generation, output):
        with self._condition:
            failure = self._failures.get(str(output))
            return bool(failure and failure[1] > time.monotonic())

    def submit(self, generation, priority, source, output, width):
        source, output = Path(source), Path(output)
        if self.cached(source, output):
            return False
        fingerprint = self._fingerprint(source)
        if fingerprint is None:
            return False
        key = str(output)
        with self._condition:
            if self._closed or key in self._active:
                return False
            failure = self._failures.get(key)
            if failure and failure[0] == fingerprint and failure[1] > time.monotonic():
                return False
            if key not in self._pending and len(self._pending) + len(self._active) >= self._limit:
                worst = max(self._pending, key=lambda item: (self._pending[item]['priority'], -self._pending[item]['sequence']), default=None)
                if worst is None or self._pending[worst]['priority'] <= priority:
                    return False
                del self._pending[worst]
            self._failures.pop(key, None)
            self._sequence += 1
            existing = self._pending.get(key)
            self._pending[key] = dict(source=source, output=output, width=width,
                                      fingerprint=fingerprint, sequence=self._sequence,
                                      priority=min(priority, existing['priority']) if existing else priority)
            self._condition.notify_all()
            return True

    def _streaming_active(self):
        try:
            return bool(self._streaming())
        except Exception:
            logging.getLogger(__name__).exception('Cannot read thumbnail streaming state')
            return True

    def _worker(self):
        while True:
            with self._condition:
                while True:
                    if self._closed:
                        return
                    streaming = self._streaming_active()
                    eligible = [(key, job) for key, job in self._pending.items()
                                if not self._paused and (not streaming or job['priority'] == 0)]
                    if eligible:
                        key, job = min(eligible, key=lambda pair: (pair[1]['priority'], -pair[1]['sequence']))
                        del self._pending[key]
                        self._active[key] = job
                        break
                    self._condition.wait(.1)
            try:
                previous_output = self._fingerprint(job['output'])
                if self.is_current(job['source'], job['output']):
                    self._generator(job['source'], job['output'], job['width'], 0, self)
                if self.is_current(job['source'], job['output']):
                    published = self._fingerprint(job['output'])
                    if published is not None and published != previous_output:
                        with self._condition:
                            self._fingerprints[key] = job['fingerprint']
                    if self.cached(job['source'], job['output']):
                        with self._condition:
                            self._completed += 1
                            self._failures.pop(key, None)
                    else:
                        self.mark_failed(0, job['output'])
            except Exception:
                self.mark_failed(0, job['output'])
                logging.getLogger(__name__).exception('Thumbnail generation failed for %s', job['source'])
            finally:
                with self._condition:
                    self._active.pop(key, None)
                    self._condition.notify_all()

    def status(self):
        with self._condition:
            return dict(pending=len(self._pending), active=len(self._active),
                        failed=sum(deadline > time.monotonic() for _, deadline in self._failures.values()),
                        paused=self._paused or self._streaming_active(), completed=self._completed,
                        workers=len(self._threads))

    def pause(self, paused):
        with self._condition:
            self._paused = bool(paused)
            self._condition.notify_all()

    def wait_for_idle(self, timeout):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._pending or self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._condition.notify_all()
