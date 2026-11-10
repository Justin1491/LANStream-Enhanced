import tempfile
import threading
import time
import unittest
from pathlib import Path


class DurationJobsTests(unittest.TestCase):
    def setUp(self):
        from cleanup_jobs import DurationJobs
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.file = self.root / 'clip.mp4'
        self.file.write_bytes(b'fictional video')
        stat = self.file.stat()
        self.saved = []
        self.streaming = threading.Event()
        self.entered = threading.Event()
        self.release = threading.Event()

        class Index:
            def duration_candidates(inner, *args, **kwargs):
                yield dict(root_id='root_test', rel='clip.mp4', size=stat.st_size, mtime=stat.st_mtime)

            def save_duration(inner, root, relative, fingerprint, duration, error=''):
                self.saved.append((duration, error))
                return True

        self.probe = lambda path: {'format': {'duration': '12.5'}}
        self.jobs = DurationJobs(lambda: Index(), resolve=lambda *args: self.root,
                                 probe=lambda path: self.probe(path), streaming=self.streaming.is_set)

    def tearDown(self):
        self.release.set()
        if hasattr(self, 'jobs'):
            self.jobs.close()
        self.temp.cleanup()

    def wait(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            status = self.jobs.status('alice')
            if status and not status['running']:
                return status
            time.sleep(.01)
        self.fail('duration preparation did not finish')

    def test_saved_duration_and_profile_private_status(self):
        self.jobs.start('alice', [self.root])
        result = self.wait()
        self.assertEqual(self.saved, [(12.5, '')])
        self.assertEqual(result['ready'], 1)
        self.assertIsNone(self.jobs.status('bob'))

    def test_playback_pauses_and_stop_does_not_probe(self):
        self.streaming.set()
        self.jobs.start('alice', [self.root])
        time.sleep(.1)
        self.assertEqual(self.saved, [])
        self.assertTrue(self.jobs.status('alice')['paused'])
        self.jobs.control('alice', 'stop')
        self.wait()
        self.assertEqual(self.saved, [])

    def test_changed_file_during_probe_is_discarded(self):
        def probe(path):
            path.write_bytes(b'a changed fictional video')
            return {'format': {'duration': 10}}
        self.probe = probe
        self.jobs.start('alice', [self.root])
        self.assertEqual(self.wait()['changed'], 1)
        self.assertEqual(self.saved, [])

    def test_nonfinite_and_missing_duration_are_failures_not_zero(self):
        self.probe = lambda path: {'format': {'duration': 'nan'}}
        self.jobs.start('alice', [self.root])
        self.assertEqual(self.wait()['failed'], 1)
        self.assertIsNone(self.saved[0][0])

    def test_one_active_job_and_no_cross_profile_control(self):
        self.streaming.set()
        job = self.jobs.start('alice', [self.root])
        with self.assertRaises(ValueError):
            self.jobs.start('bob', [self.root])
        with self.assertRaises(PermissionError):
            self.jobs.control('bob', 'stop')
        self.assertEqual(self.jobs.start('alice', [self.root])['id'], job['id'])


if __name__ == '__main__':
    unittest.main()
