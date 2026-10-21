"""Owned, short-lived FFmpeg HLS encoders and their published files."""

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


_STREAM_ID = re.compile(r"[0-9a-f]{32}-[1-9][0-9]{0,8}")
_PUBLISHED_NAME = re.compile(r"(?:index\.m3u8|init\.mp4|segment[0-9]{6}\.m4s)")


class _WindowsJob:
    """A noninherited handle makes Windows kill encoders on owner termination."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits),
                        ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        import ctypes

        handle = self.api.OpenProcess(0x0101, False, process.pid)  # SET_QUOTA | TERMINATE
        if not handle:
            if process.poll() is not None:
                return
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                error = ctypes.WinError(ctypes.get_last_error())
                if process.poll() is None:
                    raise error
        finally:
            self.api.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _stop_process(process):
    for stop in (process.terminate, process.kill):
        if process.poll() is not None:
            return True
        try:
            stop()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            continue
    return process.poll() is not None


class HLSStream:
    def __init__(self, directory, identity, source, process, stderr, tracker, tracker_id):
        self.directory = directory
        self.identity = identity
        self.source = source
        self.process = process
        self.tracker_id = tracker_id
        self.touched = time.monotonic()
        self.retired_at = None
        self._stderr = stderr
        self._tracker = tracker
        self._stopped = False

    def _record_exit(self):
        if not self._stopped and self.process.poll() is not None:
            self._tracker.stop(self.tracker_id)
            self._stopped = True

    def _stop(self):
        if not _stop_process(self.process):
            return False
        self._record_exit()
        self._stderr.close()
        return True

    def wait_for(self, name, timeout=20):
        if not _PUBLISHED_NAME.fullmatch(name):
            raise ValueError("Invalid HLS filename")
        path = self.directory / name
        deadline = time.monotonic() + max(0, timeout)
        while True:
            if path.is_file() and not path.is_symlink():
                return path
            if self.process.poll() is not None:
                raise FileNotFoundError(name)
            if time.monotonic() >= deadline:
                raise TimeoutError("HLS encoder has not published " + name)
            time.sleep(0.05)

    def error_detail(self):
        try:
            with (self.directory / "stderr.log").open("rb") as log:
                log.seek(0, 2)
                log.seek(max(0, log.tell() - 8000))
                return log.read().decode("utf-8", errors="replace")[-2000:]
        except OSError:
            return ""


class HLSStreams:
    IDLE_SECONDS = 45
    REAP_SECONDS = 1
    RETAIN_SECONDS = 10
    MAX_ACTIVE = 16
    MAX_SESSIONS = 32

    def __init__(self, cache_dir: Path, tracker):
        self.cache_dir = Path(cache_dir)
        self.tracker = tracker
        self._streams = {}
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._reaper = None
        self._job = None

    @staticmethod
    def _validate_id(stream_id):
        if not _STREAM_ID.fullmatch(stream_id):
            raise ValueError("Invalid HLS stream ID")

    def _remove(self, stream_id, stream):
        if not stream._stop():
            return
        try:
            shutil.rmtree(stream.directory)
        except FileNotFoundError:
            pass
        except OSError:
            # Windows may still have a segment open; retry on the next sweep.
            return
        self._streams.pop(stream_id, None)

    def _sweep(self):
        now = time.monotonic()
        for stream_id, stream in list(self._streams.items()):
            stream._record_exit()
            expired = (now - stream.retired_at >= self.RETAIN_SECONDS
                       if stream.retired_at is not None
                       else now - stream.touched >= self.IDLE_SECONDS)
            if expired:
                self._remove(stream_id, stream)

    def _run_reaper(self):
        while not self._closed.wait(self.REAP_SECONDS):
            with self._lock:
                self._sweep()

    def get(self, stream_id, identity: tuple, command_factory, *, source: Path,
            backend: str, start: float) -> HLSStream:
        self._validate_id(stream_id)
        source = Path(source)
        with self._lock:
            if self._closed.is_set():
                raise RuntimeError("HLS streams are closed")
            self._sweep()
            if stream_id in self._streams:
                stream = self._streams[stream_id]
                if stream.identity != identity or stream.source != source:
                    raise ValueError("HLS stream identity does not match")
                stream.touched = time.monotonic()
                return stream

            if any(key[:32] == stream_id[:32] and stream.source == source
                   and int(key[33:]) > int(stream_id[33:])
                   for key, stream in self._streams.items()):
                raise ValueError("HLS stream generation is stale")
            replacements = [stream for key, stream in self._streams.items()
                            if key[:32] == stream_id[:32] and stream.source == source]
            active = sum(stream.process.poll() is None and stream not in replacements
                         for stream in self._streams.values())
            if active >= self.MAX_ACTIVE:
                raise RuntimeError("Too many active HLS streams")
            if len(self._streams) >= self.MAX_SESSIONS:
                for key, stream in list(self._streams.items()):
                    if stream.retired_at is not None:
                        self._remove(key, stream)
                        if len(self._streams) < self.MAX_SESSIONS:
                            break
                if len(self._streams) >= self.MAX_SESSIONS:
                    raise RuntimeError("Too many HLS sessions")
            for stream in replacements:
                if not stream._stop():
                    raise RuntimeError("Could not stop previous HLS encoder")
                if stream.retired_at is None:
                    stream.retired_at = time.monotonic()

            if os.name == "nt" and self._job is None:
                self._job = _WindowsJob()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix="hls-", dir=self.cache_dir))
            stderr = None
            process = None
            try:
                stderr = (directory / "stderr.log").open("wb")
                process = subprocess.Popen(command_factory(directory), stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=stderr, cwd=directory)
                if self._job is not None:
                    self._job.assign(process)
                tracker_id = self.tracker.start(backend, source, start_offset=start, detail="HLS")
            except BaseException:
                if process is not None:
                    _stop_process(process)
                if stderr is not None:
                    stderr.close()
                shutil.rmtree(directory, ignore_errors=True)
                raise
            stream = HLSStream(directory, identity, source, process, stderr, self.tracker, tracker_id)
            self._streams[stream_id] = stream
            if self._reaper is None:
                self._reaper = threading.Thread(target=self._run_reaper, name="hls-reaper", daemon=True)
                self._reaper.start()
            return stream

    def lookup(self, stream_id, source: Path) -> HLSStream:
        self._validate_id(stream_id)
        with self._lock:
            self._sweep()
            stream = self._streams.get(stream_id)
            if stream is None or stream.source != Path(source):
                raise KeyError(stream_id)
            stream.touched = time.monotonic()
            return stream

    def close(self):
        self._closed.set()
        with self._lock:
            try:
                for stream_id, stream in list(self._streams.items()):
                    self._remove(stream_id, stream)
            finally:
                if self._job is not None:
                    self._job.close()
