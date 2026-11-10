"""Explicit, bounded duration preparation; listing requests never probe media."""
import math
import os
import stat
import threading
import uuid

from directory_index import DirectoryIndex, _relative


def fingerprint(path):
    value = path.stat(follow_symlinks=False)
    return dict(size=value.st_size, mtime=value.st_mtime, mtime_ns=value.st_mtime_ns,
                ctime_ns=value.st_ctime_ns, ino=value.st_ino, dev=value.st_dev)


def valid_duration(info):
    try:
        value = float(info['format']['duration'])
        return value if math.isfinite(value) and value >= 0 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


class DurationJobs:
    def __init__(self, index, *, resolve, probe, streaming, busy=lambda path: False):
        self.index, self.resolve, self.probe = index, resolve, probe
        self.streaming, self.busy = streaming, busy
        self._condition = threading.Condition()
        self._job = None
        self._closed = False
        self._thread = None
        self._active = None

    def is_busy(self, path):
        with self._condition:
            return self._active == os.path.normcase(os.path.abspath(path))

    def _snapshot(self):
        job = dict(self._job)
        job.pop('owner', None)
        job['paused'] = bool(job['running'] and (job['manual_pause'] or self.streaming()))
        job.pop('manual_pause', None)
        return job

    def status(self, profile_id):
        with self._condition:
            return self._snapshot() if self._job and self._job['owner'] == profile_id else None

    def start(self, profile_id, roots, **scope):
        with self._condition:
            if self._closed:
                raise ValueError('Duration preparation is shutting down.')
            if self._job and self._job['running']:
                if self._job['owner'] != profile_id:
                    raise ValueError('Duration preparation is busy. Try again later.')
                return self._snapshot()
            self._job = dict(id=uuid.uuid4().hex, owner=profile_id, running=True,
                             manual_pause=False, stopping=False, checked=0, ready=0,
                             failed=0, changed=0, error='', scope=scope.get('scope_relative', ''))
            self._thread = threading.Thread(target=self._run, args=(profile_id, roots, scope),
                                            name='duration-preparation', daemon=True)
            self._thread.start()
            return self._snapshot()

    def control(self, profile_id, action):
        if action not in {'pause', 'resume', 'stop'}:
            raise ValueError('Unknown preparation action.')
        with self._condition:
            if not self._job or self._job['owner'] != profile_id:
                raise PermissionError('Preparation not available.')
            if action == 'stop':
                self._job['stopping'] = True
            else:
                self._job['manual_pause'] = action == 'pause'
            self._condition.notify_all()
            return self._snapshot()

    def _run(self, owner, roots, scope):
        try:
            for item in self.index().duration_candidates(roots, **scope):
                with self._condition:
                    while not self._closed and not self._job['stopping'] and (
                            self._job['manual_pause'] or self.streaming()):
                        self._condition.wait(.1)
                    if self._closed or self._job['stopping']:
                        return
                    self._job['checked'] += 1
                root = None
                before = None
                try:
                    relative = _relative(item['rel'])
                    if '.lanstream-trash' in relative.casefold().split('/'):
                        raise ValueError('Reserved path.')
                    root = self.resolve(owner, item['root_id'], relative)
                    source = DirectoryIndex._safe_target(root, relative)
                    before = fingerprint(source)
                    if not stat.S_ISREG(source.lstat().st_mode):
                        raise ValueError('Not a regular file.')
                    if (before['size'], before['mtime']) != (item['size'], item['mtime']):
                        with self._condition:
                            self._job['changed'] += 1
                        continue
                    if self.busy(source):
                        raise ValueError('File is busy; retry later.')
                    with self._condition:
                        self._active = os.path.normcase(os.path.abspath(source))
                    info = self.probe(source)
                    # Recheck access and identity after a possibly slow network probe.
                    if self.resolve(owner, item['root_id'], relative) != root or fingerprint(source) != before:
                        with self._condition:
                            self._job['changed'] += 1
                        continue
                    duration = valid_duration(info)
                    error = '' if duration is not None else 'Duration unavailable.'
                    saved = self.index().save_duration(root, relative, before, duration, error)
                    with self._condition:
                        self._job['ready' if saved and duration is not None else 'failed'] += 1
                except Exception:
                    # Do not put private paths or raw FFprobe/share errors in job responses.
                    if root is not None and before is not None:
                        self.index().save_duration(root, item['rel'], before, None, 'Unable to read duration. Retry when available.')
                    with self._condition:
                        self._job['failed'] += 1
                finally:
                    with self._condition:
                        self._active = None
        except Exception:
            with self._condition:
                self._job['error'] = 'Unable to prepare durations. Check library access and retry.'
        finally:
            with self._condition:
                self._active = None
                self._job['running'] = False

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=31)
