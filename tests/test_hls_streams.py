import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hls_streams import HLSStream, HLSStreams


class HLSStreamsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.tracker = Mock()
        self.tracker.start.return_value = "tracked"
        self.manager = HLSStreams(self.root, self.tracker)
        self.source = self.root / "movie.mp4"
        self.stream_id = "a" * 32 + "-1"

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def create(self, stream_id=None, code="import time; time.sleep(60)", source=None):
        return self.manager.get(
            stream_id or self.stream_id, ("movie", 0),
            lambda directory: [sys.executable, "-c", code, str(directory)],
            source=source or self.source, backend="software", start=0,
        )

    def test_reuses_identity_and_checks_source_and_names(self):
        stream = self.create()
        self.assertIs(self.create(), stream)
        self.assertIs(self.manager.lookup(self.stream_id, self.source), stream)
        with self.assertRaises(ValueError):
            self.manager.get(self.stream_id, ("different",), Mock(), source=self.source, backend="software", start=0)
        with self.assertRaises(KeyError):
            self.manager.lookup(self.stream_id, self.root / "other.mp4")
        for name in ("../index.m3u8", "index.m3u8.tmp", "segment1.m4s", "stderr.log", "/init.mp4"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                stream.wait_for(name, timeout=0)
        for stream_id in ("../bad", "a" * 32 + "-0", "a" * 32 + "-1000000000", "A" * 32 + "-1"):
            with self.subTest(stream_id=stream_id), self.assertRaises(ValueError):
                self.create(stream_id)

    def test_child_publishes_file_and_close_kills_and_removes(self):
        stream = self.create(code="import pathlib,sys,time; pathlib.Path(sys.argv[1], 'index.m3u8').write_text('#EXTM3U'); time.sleep(60)")
        self.assertEqual(stream.wait_for("index.m3u8", timeout=5).read_text(), "#EXTM3U")
        self.assertEqual(stream.tracker_id, "tracked")
        self.manager.close()
        self.assertIsNotNone(stream.process.poll())
        self.assertFalse(stream.directory.exists())
        self.tracker.stop.assert_called_once_with("tracked")

    def test_relative_output_is_written_inside_owned_directory(self):
        app_output = Path.cwd() / "init.mp4"
        original = app_output.read_bytes() if app_output.exists() else None
        stream = self.create(code="from pathlib import Path; Path('init.mp4').write_bytes(b'owned fixture')")
        try:
            self.assertEqual(stream.wait_for("init.mp4", timeout=5).read_bytes(), b"owned fixture")
            self.assertEqual(app_output.read_bytes() if app_output.exists() else None, original)
        finally:
            if app_output.exists() and app_output.read_bytes() == b"owned fixture":
                if original is None:
                    app_output.unlink()
                else:
                    app_output.write_bytes(original)

    def test_failure_tail_and_missing_file(self):
        stream = self.create(code="import sys; sys.stderr.write('x' * 4000 + 'encoder failed'); sys.exit(3)")
        with self.assertRaises(FileNotFoundError):
            stream.wait_for("index.m3u8", timeout=5)
        self.assertTrue(stream.error_detail().endswith("encoder failed"))
        self.assertLessEqual(len(stream.error_detail()), 2000)

    def test_timeout_does_not_kill_live_encoder(self):
        stream = self.create()
        with self.assertRaises(TimeoutError):
            stream.wait_for("index.m3u8", timeout=0.01)
        self.assertIsNone(stream.process.poll())

    def test_diagnostics_distinguish_live_startup_from_encoder_exit(self):
        stream = self.create()
        (stream.directory / 'progress.log').write_text(
            'frame=12\nout_time_us=400000\nspeed=0.25x\nprogress=continue\nframe=99\n')
        report = stream.diagnostics()
        self.assertEqual(report['state'], 'starting')
        self.assertEqual(report['phase'], 'first_segment')
        self.assertEqual(report['progress']['frames'], 12, 'Ignore an unfinished progress update')
        self.assertEqual(report['progress']['output_seconds'], 0.4)
        self.assertTrue(report['running'])
        self.assertIsNone(report['exit_code'])
        stream.process.terminate()
        stream.process.wait(timeout=5)
        report = stream.diagnostics()
        self.assertEqual(report['state'], 'failed')
        self.assertEqual(report['phase'], 'encoder_exit')
        self.assertFalse(report['running'])
        self.assertIsNotNone(report['exit_code'])

    def test_diagnostics_report_publication_and_redact_private_paths(self):
        source = self.root / 'Private name.mkv'
        stream = self.create(source=source)
        for name, data in [('init.mp4', b'init'), ('segment000007.m4s', b'segment'),
                           ('index.m3u8', b'#EXTM3U\nsegment000007.m4s\n')]:
            (stream.directory / name).write_bytes(data)
        stream._stderr.write((f'Cannot open "{source}" or "{stream.directory / "init.mp4"}"\n'
                              'https://user:password@example.invalid/media\n').encode())
        stream._stderr.flush()
        report = stream.diagnostics()
        self.assertEqual(report['state'], 'ready')
        self.assertEqual(report['files']['segments'], 1)
        self.assertEqual(report['files']['init_bytes'], 4)
        self.assertNotIn(str(self.root), str(report))
        self.assertNotIn('Private name', str(report))
        self.assertNotIn('password', str(report))

    def test_readiness_sees_files_published_as_the_encoder_exits(self):
        directory = self.root / 'final-publication'
        directory.mkdir()
        process = Mock()

        def finish():
            for name in ('init.mp4', 'segment000000.m4s', 'index.m3u8'):
                (directory / name).write_bytes(b'published')
            return 0

        process.poll.side_effect = finish
        stream = HLSStream(directory, (), self.source, process, Mock(), self.tracker, 'tracked', 'qsv')
        report = stream.diagnostics()
        self.assertEqual(report['state'], 'ready')
        self.assertEqual(report['exit_code'], 0)
        self.assertEqual(report['files']['segments'], 1)

    def test_final_file_published_during_exit_check_is_available(self):
        for name in ("index.m3u8", "segment000000.m4s"):
            with self.subTest(name=name):
                directory = self.root / name
                directory.mkdir()
                published = directory / name
                process = Mock()

                def finish():
                    published.write_bytes(b"completed output")
                    return 0

                # Publish the final file as the process reports successful exit.
                process.poll.side_effect = finish
                stream = HLSStream(directory, (), self.source, process,
                                   Mock(), self.tracker, "tracked")
                self.assertEqual(stream.wait_for(name, timeout=0).read_bytes(),
                                 b"completed output")

    def test_replacement_stops_only_same_page_and_source(self):
        first = self.create()
        other = self.create("b" * 32 + "-1")
        different_source = self.create("a" * 32 + "-2", source=self.root / "other.mp4")
        replacement = self.create("a" * 32 + "-3")
        self.assertIsNotNone(first.process.poll())
        self.assertTrue(first.directory.exists())
        self.assertIsNone(other.process.poll())
        self.assertIsNone(different_source.process.poll())
        self.assertIsNone(replacement.process.poll())

    def test_delayed_older_generation_does_not_replace_new_encoder(self):
        newest = self.create("a" * 32 + "-2")
        with self.assertRaises(ValueError):
            self.create("a" * 32 + "-1")
        self.assertIsNone(newest.process.poll())

    def test_refreshing_existing_older_generation_keeps_new_encoder(self):
        older = self.create("a" * 32 + "-2")
        newest = self.create("a" * 32 + "-3")
        self.assertIs(self.create("a" * 32 + "-2"), older)
        self.assertIsNone(newest.process.poll())

    def test_idle_expiry_reaps_real_child(self):
        with patch.object(HLSStreams, "IDLE_SECONDS", 0.05), patch.object(HLSStreams, "REAP_SECONDS", 0.02):
            stream = self.create()
            deadline = time.monotonic() + 5
            while stream.directory.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIsNotNone(stream.process.poll())
            self.assertFalse(stream.directory.exists())
            with self.assertRaises(KeyError):
                self.manager.lookup(self.stream_id, self.source)

    def test_cap_rejects_without_evicting_other_viewer(self):
        with patch.object(HLSStreams, "MAX_ACTIVE", 1):
            stream = self.create()
            with self.assertRaises(RuntimeError):
                self.create("b" * 32 + "-1")
            self.assertIsNone(stream.process.poll())

    def test_spawn_failure_cleans_directory(self):
        with patch("hls_streams.subprocess.Popen", side_effect=OSError("missing encoder")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_locked_directory_cleanup_is_retried(self):
        stream = self.create()
        with patch.object(HLSStreams, "IDLE_SECONDS", 0):
            with patch("hls_streams.shutil.rmtree", side_effect=PermissionError("file open")):
                with self.manager._lock:
                    self.manager._sweep()
                self.assertIsNotNone(stream.process.poll())
                self.assertTrue(stream.directory.exists())
            with self.manager._lock:
                self.manager._sweep()
            self.assertFalse(stream.directory.exists())

    def test_session_cap_cleans_retired_files(self):
        with patch.object(HLSStreams, "MAX_SESSIONS", 2):
            first = self.create()
            second = self.create("a" * 32 + "-2")
            third = self.create("a" * 32 + "-3")
            self.assertFalse(first.directory.exists())
            self.assertTrue(second.directory.exists())
            self.assertTrue(third.directory.exists())

    def test_reaper_stops_tracker_when_encoder_exits(self):
        with patch.object(HLSStreams, "REAP_SECONDS", 0.02):
            stream = self.create(code="pass")
            deadline = time.monotonic() + 5
            while not self.tracker.stop.called and time.monotonic() < deadline:
                time.sleep(0.02)
            self.tracker.stop.assert_called_once_with("tracked")
            self.assertTrue(stream.directory.exists())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object ownership")
    def test_windows_parent_termination_kills_owned_encoder(self):
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        pid_file = self.root / "encoder.pid"
        code = """
import sys, time
from pathlib import Path
from unittest.mock import Mock
from hls_streams import HLSStreams
root = Path(sys.argv[1])
manager = HLSStreams(root / 'helper', Mock())
stream = manager.get('a' * 32 + '-1', ('test',),
    lambda directory: [sys.executable, '-c', 'import time; time.sleep(60)'],
    source=root / 'fixture.mp4', backend='software', start=0)
(root / 'encoder.pid').write_text(str(stream.process.pid))
time.sleep(60)
"""
        child_handle = None
        with (self.root / "helper.log").open("wb") as log:
            helper = subprocess.Popen([sys.executable, "-c", code, str(self.root)],
                                      cwd=Path(__file__).resolve().parents[1],
                                      stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 5
                while not pid_file.exists() and helper.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(pid_file.exists(), "helper failed: " + (self.root / "helper.log").read_text())
                child_handle = kernel.OpenProcess(0x100001, False, int(pid_file.read_text()))
                self.assertTrue(child_handle)
                self.assertEqual(kernel.WaitForSingleObject(child_handle, 0), 258)
                helper.terminate()
                helper.wait(timeout=5)
                self.assertEqual(kernel.WaitForSingleObject(child_handle, 5000), 0,
                                 "encoder survived termination of its owner")
            finally:
                if helper.poll() is None:
                    helper.kill()
                    helper.wait(timeout=5)
                if child_handle:
                    if kernel.WaitForSingleObject(child_handle, 0) == 258:
                        kernel.TerminateProcess(child_handle, 1)
                        kernel.WaitForSingleObject(child_handle, 5000)
                    kernel.CloseHandle(child_handle)

    def test_stop_errors_do_not_kill_reaper(self):
        with patch.object(HLSStreams, "REAP_SECONDS", 0.02), patch.object(HLSStreams, "IDLE_SECONDS", 0):
            stream = self.create()
            with patch.object(stream.process, "terminate", side_effect=OSError("exit race")), \
                 patch.object(stream.process, "kill", side_effect=OSError("temporarily denied")):
                time.sleep(0.08)
                self.assertTrue(self.manager._reaper.is_alive())
                self.assertTrue(stream.directory.exists())
            deadline = time.monotonic() + 5
            while stream.directory.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(stream.directory.exists())

    @unittest.skipUnless(os.name == "nt", "Windows Job Object ownership")
    def test_job_assignment_failure_kills_child_and_close_releases_handle(self):
        first = self.create()
        created = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            created.append(process)
            return process

        with patch.object(self.manager._job, "assign", side_effect=OSError("assignment refused")), \
             patch("hls_streams.subprocess.Popen", side_effect=capture):
            with self.assertRaises(OSError):
                self.create("b" * 32 + "-1")
        self.assertIsNotNone(created[0].poll())
        self.assertEqual(list(self.root.iterdir()), [first.directory])
        self.manager.close()
        self.assertIsNone(self.manager._job.handle)


if __name__ == "__main__":
    unittest.main()
