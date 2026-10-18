import sys
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_scheduler import ImageScheduler


class ImageSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name)
        self.schedulers = []

    def tearDown(self):
        for scheduler in self.schedulers:
            scheduler.close()
        self.temp.cleanup()

    def scheduler(self, generator, **kwargs):
        scheduler = ImageScheduler(generator, **kwargs)
        self.schedulers.append(scheduler)
        return scheduler

    def paths(self, name):
        source, output = self.root / name, self.root / (name + '.jpg')
        source.write_text('media')
        return source, output

    def submit(self, scheduler, name, priority=10):
        source, output = self.paths(name)
        return scheduler.submit(0, priority, source, output, 360)

    def test_navigation_survives_and_visible_work_is_promoted_and_deduplicated(self):
        seen = []
        def generate(source, output, width, generation, scheduler):
            seen.append(source.name)
            output.write_text('image')
        scheduler = self.scheduler(generate)
        scheduler.pause(True)
        for name in ('old', 'visible', 'other'):
            self.submit(scheduler, name)
        self.assertEqual(scheduler.new_generation(), 0)
        source, output = self.root / 'visible', self.root / 'visible.jpg'
        scheduler.submit(123, 1, source, output, 360)
        self.assertEqual(scheduler.status()['pending'], 3)
        scheduler.pause(False)
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertEqual(seen[0], 'visible')
        self.assertEqual(set(seen), {'old', 'visible', 'other'})
        self.assertEqual(scheduler.status()['completed'], 3)

    def test_cap_includes_active_and_workers_are_bounded(self):
        started, release = threading.Event(), threading.Event()
        def generate(source, output, *args):
            started.set()
            release.wait(2)
            output.write_text('image')
        scheduler = self.scheduler(generate, max_pending=2)
        self.assertTrue(self.submit(scheduler, 'active'))
        self.assertTrue(started.wait(1))
        self.assertTrue(self.submit(scheduler, 'queued'))
        self.assertFalse(self.submit(scheduler, 'overflow'))
        self.assertEqual(scheduler.status()['active'] + scheduler.status()['pending'], 2)
        release.set()
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertEqual(self.scheduler(generate, workers=20).status()['workers'], 2)

    def test_cache_reuse_and_source_replacement(self):
        source, output = self.paths('media')
        output.write_text('existing')
        scheduler = self.scheduler(lambda *args: None)
        self.assertTrue(scheduler.cached(source, output))
        source.write_text('replacement with a different size')
        self.assertFalse(scheduler.cached(source, output))

    def test_queueing_replacement_does_not_validate_old_image(self):
        source, output = self.paths('media')
        original = source.stat()
        output.write_text('old image')
        scheduler = self.scheduler(lambda *args: None)
        scheduler.pause(True)
        self.assertTrue(scheduler.cached(source, output))
        source.write_text('replacement, different contents')
        os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertFalse(scheduler.cached(source, output))
        self.assertTrue(scheduler.submit(0, 10, source, output, 360))
        self.assertFalse(scheduler.cached(source, output))
        scheduler.pause(False)
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertFalse(scheduler.cached(source, output))

    def test_poster_displaces_lower_priority_pending_job_when_full(self):
        seen = []
        def generate(source, output, *args):
            seen.append(source.name)
            output.write_text('image')
        scheduler = self.scheduler(generate, max_pending=2)
        scheduler.pause(True)
        self.submit(scheduler, 'old-one', priority=10)
        self.submit(scheduler, 'old-two', priority=10)
        self.assertTrue(self.submit(scheduler, 'poster', priority=0))
        self.assertEqual(scheduler.status()['pending'], 2)
        scheduler.pause(False)
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertEqual(seen[0], 'poster')

    def test_changed_active_source_cannot_publish(self):
        started, release = threading.Event(), threading.Event()
        results = []
        def generate(source, output, width, generation, scheduler):
            started.set()
            release.wait(2)
            results.append(scheduler.is_current(source, output))
            if results[-1]:
                output.write_text('image')
        scheduler = self.scheduler(generate)
        self.submit(scheduler, 'media')
        self.assertTrue(started.wait(1))
        (self.root / 'media').write_text('replacement')
        release.set()
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertEqual(results, [False])
        self.assertFalse((self.root / 'media.jpg').exists())

    def test_failure_backoff_and_worker_survives(self):
        attempts = []
        def generate(source, output, *args):
            attempts.append(source.name)
            if source.name == 'bad':
                raise ValueError('failed')
            output.write_text('image')
        scheduler = self.scheduler(generate, retry_seconds=.05)
        self.submit(scheduler, 'bad')
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertTrue(scheduler.failed(0, self.root / 'bad.jpg'))
        self.assertFalse(scheduler.submit(0, 10, self.root / 'bad', self.root / 'bad.jpg', 360))
        self.submit(scheduler, 'good')
        self.assertTrue(scheduler.wait_for_idle(2))
        time.sleep(.06)
        self.assertFalse(scheduler.failed(0, self.root / 'bad.jpg'))
        self.assertTrue(scheduler.submit(0, 10, self.root / 'bad', self.root / 'bad.jpg', 360))
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertEqual(attempts.count('bad'), 2)
        self.assertIn('good', attempts)

    def test_missing_output_is_a_failure_and_pause_retains_work(self):
        scheduler = self.scheduler(lambda *args: None)
        scheduler.pause(True)
        self.submit(scheduler, 'missing')
        self.assertFalse(scheduler.wait_for_idle(.02))
        self.assertEqual(scheduler.status()['pending'], 1)
        scheduler.pause(False)
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertTrue(scheduler.failed(0, self.root / 'missing.jpg'))
        self.assertEqual(scheduler.status()['failed'], 1)

    def test_streaming_pauses_background_but_allows_poster(self):
        streaming = threading.Event()
        streaming.set()
        seen = []
        def generate(source, output, *args):
            seen.append(source.name)
            output.write_text('image')
        scheduler = self.scheduler(generate, streaming=streaming.is_set)
        self.submit(scheduler, 'background')
        self.submit(scheduler, 'poster', 0)
        deadline = time.monotonic() + 1
        while 'poster' not in seen and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(seen, ['poster'])
        self.assertTrue(scheduler.status()['paused'])
        streaming.clear()
        self.assertTrue(scheduler.wait_for_idle(2))
        self.assertEqual(seen, ['poster', 'background'])


if __name__ == '__main__':
    unittest.main()

