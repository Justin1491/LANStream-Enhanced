import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automatic_thumbnails import AutomaticThumbnails
from directory_index import DirectoryIndex
from image_scheduler import ImageScheduler


class AutomaticThumbnailsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.feeders, self.schedulers = [], []

    def tearDown(self):
        for feeder in self.feeders:
            feeder.close(wait=True)
        for scheduler in self.schedulers:
            scheduler.close(wait=True)
        self.temp.cleanup()

    def paths(self, name):
        source, output = self.root / name, self.root / (name + '.jpg')
        source.write_text('media')
        return source, output, 360

    def scheduler(self, generator=None, **kwargs):
        scheduler = ImageScheduler(generator or (lambda source, output, *args: output.write_text('image')), **kwargs)
        self.schedulers.append(scheduler)
        return scheduler

    def feeder(self, scheduler, candidates, **kwargs):
        feeder = AutomaticThumbnails(scheduler, candidates, poll_seconds=.01, **kwargs)
        self.feeders.append(feeder)
        return feeder

    def until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('Timed out waiting for background work')
            time.sleep(.01)

    def test_waits_for_inactivity_and_does_not_enumerate_during_streaming_or_pause(self):
        streaming, seen = [True], []
        scheduler = self.scheduler(streaming=lambda: streaming[0])
        job = self.paths('one')
        feeder = self.feeder(scheduler, lambda stop: seen.append('scan') or iter([job]), idle_seconds=.1)
        time.sleep(.15)
        self.assertEqual(seen, [])
        scheduler.pause(True)
        streaming[0] = False
        time.sleep(.05)
        self.assertEqual(seen, [])
        scheduler.pause(False)
        feeder.touch()
        time.sleep(.05)
        self.assertEqual(seen, [])
        self.until(lambda: feeder.status()['ready'] == 1)

    def test_disabled_then_enabled_and_cached_output_survives_new_scheduler(self):
        job = self.paths('one')
        seen = []
        first = self.scheduler(lambda source, output, *args: (seen.append(source), output.write_text('image')))
        feeder = self.feeder(first, lambda stop: iter([job]), idle_seconds=0, enabled=False)
        time.sleep(.04)
        self.assertEqual(feeder.status()['state'], 'disabled')
        self.assertEqual(seen, [])
        feeder.set_enabled(True)
        self.until(lambda: feeder.status()['ready'] == 1)
        feeder.close(wait=True)
        first.close(wait=True)
        second = self.scheduler(lambda *args: self.fail('Cached thumbnail regenerated after restart'))
        restarted = self.feeder(second, lambda stop: iter([job]), idle_seconds=0)
        self.until(lambda: restarted.status()['ready'] == 1)
        self.assertEqual(len(seen), 1)

    def test_activity_cancels_active_job_and_retries_without_counting_failure(self):
        started, cancelled = threading.Event(), threading.Event()
        attempts = []
        def generate(source, output, width, generation, scheduler):
            attempts.append(source)
            if len(attempts) == 1:
                started.set()
                while scheduler.is_current(source, output):
                    time.sleep(.005)
                cancelled.set()
                return
            output.write_text('image')
        scheduler = self.scheduler(generate)
        job = self.paths('one')
        feeder = self.feeder(scheduler, lambda stop: iter([job]), idle_seconds=.06)
        self.assertTrue(started.wait(1))
        feeder.touch()
        self.assertTrue(cancelled.wait(1))
        self.assertFalse(scheduler.failed(0, job[1]))
        self.until(lambda: feeder.status()['ready'] == 1)
        self.assertEqual(feeder.status()['failed'], 0)
        self.assertEqual(len(attempts), 2)

    def test_failure_is_counted_once_and_does_not_tightly_rescan(self):
        attempts, scans = [], []
        def generate(source, *args):
            attempts.append(source)
            raise OSError('bad media')
        scheduler = self.scheduler(generate, retry_seconds=60)
        job = self.paths('bad')
        def candidates(stop):
            scans.append(1)
            return iter([job])
        feeder = self.feeder(scheduler, candidates, idle_seconds=0, rescan_seconds=.15)
        self.until(lambda: feeder.status()['failed'] == 1)
        time.sleep(.04)
        self.assertEqual(len(scans), 1)
        self.until(lambda: len(scans) >= 2)
        self.until(lambda: feeder.status()['failed'] == 1)
        self.assertEqual(len(attempts), 1)

    def test_waits_behind_visible_job_when_queue_is_full(self):
        gate, release = threading.Event(), threading.Event()
        seen = []
        def generate(source, output, *args):
            seen.append(source.name)
            if source.name == 'visible':
                gate.set()
                release.wait(2)
            output.write_text('image')
        scheduler = self.scheduler(generate, workers=1, max_pending=1)
        scheduler.pause(True)
        auto = self.paths('auto')
        feeder = self.feeder(scheduler, lambda stop: iter([auto]), idle_seconds=0)
        visible = self.paths('visible')
        scheduler.submit(0, 10, *visible)
        scheduler.pause(False)
        self.assertTrue(gate.wait(1))
        self.assertEqual(feeder.status()['checked'], 0)
        release.set()
        self.until(lambda: feeder.status()['ready'] == 1)
        self.assertEqual(seen, ['visible', 'auto'])

    def test_retries_automatic_job_evicted_from_full_queue(self):
        started, release = threading.Event(), threading.Event()
        seen = []
        def generate(source, output, *args):
            seen.append(source.name)
            if source.name == 'blocker':
                started.set()
                release.wait(2)
            output.write_text('image')
        scheduler = self.scheduler(generate, workers=1, max_pending=2)
        scheduler.set_automatic_eligibility(lambda: True)
        scheduler.submit(0, 30, *self.paths('blocker'))
        self.assertTrue(started.wait(1))
        auto = self.paths('auto')
        feeder = self.feeder(scheduler, lambda stop: iter([auto]), idle_seconds=0)
        self.until(lambda: scheduler.scheduled(auto[1]))
        scheduler.submit(0, 0, *self.paths('poster'))
        self.assertFalse(scheduler.scheduled(auto[1]))
        release.set()
        self.until(lambda: feeder.status()['ready'] == 1)
        self.assertEqual(seen, ['blocker', 'poster', 'auto'])

    def test_candidate_exception_and_shutdown(self):
        def candidates(stop):
            raise OSError('offline')
        feeder = self.feeder(self.scheduler(), candidates, idle_seconds=0)
        self.until(lambda: feeder.status()['error'] is not None)
        feeder.close(wait=True)
        self.assertFalse(feeder._thread.is_alive())

    def test_recursive_index_sources_use_snapshots_and_respect_exclusions_hidden_and_trash(self):
        index = DirectoryIndex(self.root / 'index.db', lambda suffix: 'video' if suffix == '.mp4' else None)
        try:
            for directory in ('nested', 'blocked', '.lanstream-trash'):
                (self.root / directory).mkdir()
                (self.root / directory / 'clip.mp4').touch()
            (self.root / 'hidden.mp4').touch()
            (self.root / 'top.mp4').touch()
            for directory in ('', 'nested', 'blocked'):
                index.listing(self.root, directory)
                self.assertTrue(index.wait_for_idle())
            index.hide_file(self.root, 'hidden.mp4')
            with patch('directory_index.os.scandir', side_effect=AssertionError('Must not rescan')):
                sources = list(index.automatic_thumbnail_sources(self.root, excluded=('blocked',)))
            self.assertEqual(sources, ['nested/clip.mp4', 'top.mp4'])
        finally:
            index.close()


if __name__ == '__main__':
    unittest.main()
