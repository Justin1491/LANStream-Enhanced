import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.request import Request, urlopen


class _BackgroundStatus:
    def __init__(self, initial):
        self._lock = threading.Lock()
        self._state = dict(initial, running=False)
        self._closed = False

    def status(self):
        with self._lock:
            return dict(self._state)

    def _start(self, work):
        with self._lock:
            if self._closed or self._state['running']:
                return False
            self._state['running'] = True
        threading.Thread(target=self._run, args=(work,), daemon=True).start()
        return True

    def _run(self, work):
        try:
            result = work()
        except Exception:
            result = {'error': 'Unable to complete the request.'}
        with self._lock:
            self._state.update(result, running=False)

    def close(self):
        with self._lock:
            self._closed = True


class StorageStatus(_BackgroundStatus):
    def __init__(self, app_dir, resource_dir, cache_dir):
        super().__init__({'cache': {}, 'system': {}, 'partial': False, 'scanned': False, 'error': None})
        self.app_dir, self.resource_dir, self.cache_dir = map(Path, (app_dir, resource_dir, cache_dir))

    def refresh(self):
        return self._start(self._scan)

    def _scan(self):
        partial = False
        visited = set()
        deadline = time.monotonic() + 30
        count = 0
        cache_abs = os.path.normcase(os.path.abspath(self.cache_dir))

        def size(path, exclude_cache=False, discovered=False):
            nonlocal partial, count
            path = Path(path)
            absolute = os.path.normcase(os.path.abspath(path))
            if exclude_cache and (absolute == cache_abs or absolute.startswith(cache_abs + os.sep)):
                return 0
            if absolute in visited:
                return 0
            visited.add(absolute)
            count += 1
            if count > 200000 or time.monotonic() > deadline:
                partial = True
                return 0
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    return 0
                if stat.S_ISDIR(info.st_mode):
                    total = 0
                    with os.scandir(path) as entries:
                        for entry in entries:
                            if count >= 200000 or time.monotonic() > deadline:
                                partial = True
                                break
                            total += size(entry.path, exclude_cache, True)
                    return total
                return info.st_size if stat.S_ISREG(info.st_mode) else 0
            except FileNotFoundError:
                partial |= discovered
                return 0
            except OSError:
                partial = True
                return 0

        cache = {}
        try:
            if self.cache_dir.exists():
                # Never enumerate through a cache directory that is itself a link or junction.
                info = self.cache_dir.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    partial = True
                else:
                    with os.scandir(self.cache_dir) as entries:
                        for entry in entries:
                            if count >= 200000 or time.monotonic() > deadline:
                                partial = True
                                break
                            category = 'index' if entry.name in ('index.sqlite3', 'index.sqlite3-wal', 'index.sqlite3-shm') else entry.name
                            cache[category] = cache.get(category, 0) + size(entry.path, discovered=True)
        except OSError:
            partial = True
        system = {}
        groups = {
            'Runtime': [(self.app_dir, 'LANStream.exe'), (self.app_dir, '_internal')],
            'Application': [(self.resource_dir, name) for name in ('app.py', 'launcher.py', 'desktop_launcher.py', 'cleanup_jobs.py', 'cleanup_routes.py', 'directory_index.py', 'hls_streams.py', 'image_scheduler.py', 'library_trash.py', 'system_status.py', 'automatic_thumbnails.py')],
            'Resources': [(self.resource_dir, name) for name in ('templates', 'static')],
            'Tools': [(self.app_dir, name) for name in ('tools', 'ffmpeg.exe', 'ffprobe.exe', 'ffmpeg-nvenc.exe', '7zz.exe', '7z.exe', '7za.exe', '7z.dll', 'ffmpeg-LICENSE.txt', '7zip-LICENSE.txt')],
            'Configuration and logs': [(self.app_dir, name) for name in ('config.txt', 'logs', 'acceptance-logs', 'release-manifest.json', 'BUILD-IDENTITY.json')],
        }
        try:
            with os.scandir(self.app_dir) as entries:
                for index, entry in enumerate(entries):
                    if index >= 200000 or time.monotonic() > deadline:
                        partial = True
                        break
                    if entry.name.lower().endswith('.log'):
                        groups['Configuration and logs'].append((self.app_dir, entry.name))
        except OSError:
            partial = True
        for label, paths in groups.items():
            system[label] = sum(size(root / name, True) for root, name in paths)
        return dict(cache=cache, system=system, partial=partial, scanned=True, error=None)


class UpdateStatus(_BackgroundStatus):
    def __init__(self, app_dir):
        super().__init__({'current': self._current(Path(app_dir)), 'latest': None, 'checked': False, 'error': None})

    @staticmethod
    def _current(app_dir):
        for name, key in (('BUILD-IDENTITY.json', 'source_commit'), ('release-manifest.json', 'revision')):
            try:
                value = json.loads((app_dir / name).read_text(encoding='utf-8-sig')).get(key)
                if isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{7,64}', value):
                    return value.lower()
            except (OSError, ValueError, AttributeError):
                pass
        return None

    def check(self):
        return self._start(self._check)

    def _check(self):
        try:
            request = Request('https://api.github.com/repos/Justin1491/LANStream-Enhanced/commits/main', headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'LANStream-Enhanced'})
            with urlopen(request, timeout=8) as response:
                data = json.loads(response.read(65537))
            sha = data.get('sha')
            if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{40}', sha):
                raise ValueError('Invalid revision')
            return dict(latest=sha.lower(), checked=True, error=None)
        except Exception:
            return dict(latest=None, checked=True, error='Update check failed. Try again later.')
