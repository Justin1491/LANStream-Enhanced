import os
import sys
import subprocess
import sqlite3
from contextlib import closing
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from directory_index import DirectoryIndex, root_id


class DirectoryIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'media'
        self.root.mkdir()
        self.db = Path(self.tmp.name) / 'index.sqlite3'
        self.index = DirectoryIndex(self.db, lambda suffix: 'video' if suffix == '.mp4' else None)

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def ready(self, relative='', **kwargs):
        self.index.listing(self.root, relative, **kwargs)
        self.assertTrue(self.index.wait_for_idle())
        return self.index.listing(self.root, relative, **kwargs)

    def test_empty_one_and_hundred(self):
        self.assertEqual(self.ready()['total'], 0)
        for n in range(100):
            (self.root / f'file{n}.mp4').touch()
        self.index.listing(self.root, refresh=True)
        self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.listing(self.root)['total'], 100)
        self.assertEqual(len(self.index.listing(self.root, page_size=1)['items']), 1)

    def test_natural_unicode_sort_filter_and_exclusion(self):
        for name in ['item10.mp4', 'item2.mp4', 'ÉCOLE.mp4', 'STRASSE.mp4']:
            (self.root / name).touch()
        (self.root / 'zfolder').mkdir()
        result = self.ready(page_size=2)
        self.assertEqual([x['name'] for x in result['items']], ['zfolder', 'item2.mp4'])
        self.assertEqual(self.index.listing(self.root, query='école')['total'], 1)
        self.assertEqual(self.index.listing(self.root, query='straße')['total'], 1)
        result = self.index.listing(self.root, excluded=(self.root / 'zfolder',), page_size=1)
        self.assertEqual(result['total'], 4)
        self.assertEqual(result['items'][0]['name'], 'item2.mp4')
        self.assertEqual(self.index.listing(self.root, excluded=(self.root.parent,))['total'], 0)

    def test_no_filesystem_on_warm_request(self):
        self.ready()
        with patch('directory_index.os.scandir', side_effect=AssertionError('request scanned')):
            self.assertEqual(self.index.listing(self.root)['state'], 'ready')

    def test_validation(self):
        for relative in ['../x', '/x', 'C:/x', '\\\\host\\share', 'a\\b', 'a/../b', 'a:b', 'CON', 'a/NUL.txt', 'LPT1', 'x.', 'x ']:
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.index.listing(self.root, relative)

    def test_persistence_favorites_and_search(self):
        (self.root / 'nested').mkdir()
        (self.root / 'nested' / 'Straße.mp4').touch()
        self.ready('nested')
        self.index.set_favorite('p', self.root, 'nested/Straße.mp4', 'video', 'Straße.mp4', True)
        self.index.close()
        self.index = DirectoryIndex(self.db, lambda suffix: 'video')
        result = self.index.search([self.root], query='STRASSE')
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['parent'], 'nested')
        self.assertEqual(self.index.favorites('p', [Path('/unrelated'), self.root])['total'], 1)
        self.assertEqual(self.index.favorites('other', [self.root])['total'], 0)
        self.assertEqual(self.index.favorites('p', [self.root], excluded={root_id(self.root): ('nested',)})['total'], 0)
        self.assertEqual(self.index.favorite_flags('p', result['items']), [True])
        self.assertEqual(self.index.favorite_flags('other', result['items']), [False])
        excluded = self.index.search([self.root], query='', excluded={root_id(self.root): ('nested',)})
        self.assertEqual(excluded['coverage'][0]['folders'], 0)

    def test_offline_retains_snapshot(self):
        (self.root / 'one.mp4').touch()
        initial = self.ready()
        with patch('directory_index.os.scandir', side_effect=OSError('private secret path')):
            self.index.listing(self.root, refresh=True)
            self.assertTrue(self.index.wait_for_idle())
        result = self.index.listing(self.root)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['version'], initial['version'])
        self.assertEqual(result['state'], 'offline')
        self.assertNotIn('secret', result['error'])

    def test_deduplicates_and_timeout_is_bounded(self):
        gate = threading.Event()
        real_scandir = os.scandir
        def slow(path):
            gate.wait(2)
            return real_scandir(path)
        self.index.scan_timeout = .05
        with patch('directory_index.os.scandir', side_effect=slow):
            start = time.monotonic()
            for _ in range(20):
                self.index.listing(self.root)
            self.assertLess(time.monotonic() - start, .5)
            time.sleep(.1)
            self.assertEqual(self.index.metrics()['scans_started'], 1)
            self.assertEqual(self.index.listing(self.root)['state'], 'error')
            gate.set()
            self.assertTrue(self.index.wait_for_idle())

    def test_symlink_skip(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (outside / 'secret.mp4').touch()
        try:
            (self.root / 'link').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('symlink permission unavailable')
        self.assertEqual(self.ready()['total'], 0)
        self.assertEqual(self.ready('link')['state'], 'error')

    def stream_listing(self, count):
        class Stream:
            def __enter__(self):
                return (SimpleNamespace(name=f'video{n}.mp4', is_symlink=lambda: False,
                        stat=lambda **kw: SimpleNamespace(st_mode=0o100644, st_size=10, st_mtime=1))
                        for n in range(count))

            def __exit__(self, *args):
                pass

        with patch('directory_index.os.scandir', return_value=Stream()):
            self.index.listing(self.root)
            self.assertTrue(self.index.wait_for_idle())

    def test_streams_25969_entries_and_filters_before_paging(self):
        self.stream_listing(25969)
        result = self.index.listing(self.root, page=260)
        self.assertEqual(result['total'], 25969)
        self.assertEqual(len(result['items']), 69)
        result = self.index.listing(self.root, query='video25968', page_size=1)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['name'], 'video25968.mp4')

    def test_streams_hundred_thousand_entries_into_bounded_pages(self):
        self.stream_listing(100000)
        result = self.index.listing(self.root, page=1000)
        self.assertEqual(result['total'], 100000)
        self.assertEqual(len(result['items']), 100)
        self.assertEqual(result['items'][-1]['name'], 'video99999.mp4')

    def test_corrupt_or_unknown_schema_fails_without_deleting_data(self):
        broken = Path(self.tmp.name) / 'broken.sqlite3'
        broken.write_bytes(b'not a database; preserve this file')
        with self.assertRaises(sqlite3.DatabaseError):
            DirectoryIndex(broken, lambda suffix: None)
        self.assertEqual(broken.read_bytes(), b'not a database; preserve this file')
        future = Path(self.tmp.name) / 'future.sqlite3'
        with closing(sqlite3.connect(future)) as connection:
            connection.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            DirectoryIndex(future, lambda suffix: None)
        with closing(sqlite3.connect(future)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 99)

    def test_failed_partial_snapshot_never_replaces_last_good(self):
        (self.root / 'original.mp4').touch()
        original = self.ready()
        class BrokenStream:
            def __enter__(self):
                def entries():
                    for n in range(257):
                        yield SimpleNamespace(name=f'new{n}.mp4', is_symlink=lambda: False,
                            stat=lambda **kw: SimpleNamespace(st_mode=0o100644, st_size=1, st_mtime=1))
                    raise OSError('interrupted')
                return entries()
            def __exit__(self, *args):
                pass
        with patch('directory_index.os.scandir', return_value=BrokenStream()):
            self.index.listing(self.root, refresh=True)
            self.assertTrue(self.index.wait_for_idle())
        result = self.index.listing(self.root)
        self.assertEqual(result['version'], original['version'])
        self.assertEqual(result['total'], 1)

    def test_stale_queues_refresh_and_size_sort_uses_whole_folder(self):
        for n in range(4):
            (self.root / f'{n}.mp4').write_bytes(b'x' * n)
        self.ready()
        result = self.index.listing(self.root, sort='size', descending=True, page_size=1)
        self.assertEqual(result['items'][0]['size'], 3)
        self.index.ttl = -1
        result = self.index.listing(self.root)
        self.assertEqual(result['state'], 'stale')
        self.assertTrue(result['refreshing'])
        self.assertTrue(self.index.wait_for_idle())

    def test_host_queue_does_not_block_other_host(self):
        other_same_host = Path(self.tmp.name) / 'media2'
        other_host = Path(self.tmp.name) / 'backup'
        other_same_host.mkdir()
        other_host.mkdir()
        self.index._host = lambda root: root.name[0]
        gate, started, other_started, same_started = threading.Event(), threading.Event(), threading.Event(), threading.Event()
        real_scandir = os.scandir
        def controlled(path):
            if path == self.root:
                started.set()
                gate.wait(2)
            elif path == other_host:
                other_started.set()
            elif path == other_same_host:
                same_started.set()
            return real_scandir(path)
        with patch('directory_index.os.scandir', side_effect=controlled):
            self.index.listing(self.root)
            self.assertTrue(started.wait(1))
            self.index.listing(other_same_host)
            self.index.listing(other_host)
            self.assertTrue(other_started.wait(1))
            self.assertFalse(same_started.is_set())
            gate.set()
            self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.metrics()['scans_completed'], 3)

    def test_bounded_pending_queue(self):
        self.index.max_pending = 1
        gate, started = threading.Event(), threading.Event()
        real_scandir = os.scandir
        def controlled(path):
            started.set()
            gate.wait(2)
            return real_scandir(path)
        for name in ('two', 'three'):
            (self.root / name).mkdir()
        with patch('directory_index.os.scandir', side_effect=controlled):
            self.index.listing(self.root)
            self.assertTrue(started.wait(1))
            self.index.listing(self.root, 'two')
            self.index.listing(self.root, 'three')
            self.assertEqual(self.index.metrics()['pending'], 1)
            self.assertEqual(self.index.metrics()['rejected'], 1)
            gate.set()
            self.assertTrue(self.index.wait_for_idle())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_junction_skip_and_traversal(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        junction = self.root / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest('junction creation unavailable')
        self.assertEqual(self.ready()['total'], 0)
        self.assertEqual(self.ready('junction')['state'], 'error')

    def wait_crawl(self):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = self.index.crawl_status('p', [self.root])[0]
            if not status['running']:
                return status
            time.sleep(.025)
        self.fail('crawl did not finish')

    def test_recursive_crawl_exclusions_and_profile_scope(self):
        (self.root / 'allowed' / 'child').mkdir(parents=True)
        (self.root / 'blocked').mkdir()
        (self.root / 'allowed' / 'child' / 'findme.mp4').touch()
        (self.root / 'blocked' / 'secret.mp4').touch()
        self.index.crawl_start('p', self.root, excluded=('blocked',))
        status = self.wait_crawl()
        self.assertEqual(status['completed'], 3)
        self.assertEqual(status['errors'], 0)
        self.assertEqual(self.index.search([self.root], query='findme')['total'], 1)
        self.assertEqual(self.index.search([self.root], query='secret')['total'], 0)
        self.assertEqual(self.index.crawl_status('other', [self.root]), [])

    def test_crawl_pause_restart_and_explicit_resume(self):
        for n in range(8):
            (self.root / f'dir{n}').mkdir()
        self.index.crawl_start('p', self.root)
        self.index.crawl_pause('p', self.root)
        self.assertFalse(self.index.crawl_status('p', [self.root])[0]['running'])
        self.index.close()
        self.index = DirectoryIndex(self.db, lambda suffix: 'video')
        status = self.index.crawl_status('p', [self.root])[0]
        self.assertTrue(status['paused'])
        self.assertFalse(status['running'])
        time.sleep(.2)
        self.assertEqual(self.index.metrics()['scans_started'], 0)
        self.index.crawl_start('p', self.root)
        self.assertEqual(self.wait_crawl()['completed'], 9)

    def test_completed_crawl_can_discover_additions_on_explicit_restart(self):
        self.index.crawl_start('p', self.root)
        self.wait_crawl()
        (self.root / 'new').mkdir()
        (self.root / 'new' / 'added.mp4').touch()
        self.index.crawl_start('p', self.root)
        self.wait_crawl()
        self.assertEqual(self.index.search([self.root], query='added')['total'], 1)

    def test_parent_refresh_retires_removed_descendants_but_keeps_favorites(self):
        folder = self.root / 'old'
        folder.mkdir()
        (folder / 'needle.mp4').touch()
        self.ready()
        self.ready('old')
        self.index.set_favorite('p', self.root, 'old/needle.mp4', 'video', 'needle.mp4', True)
        (folder / 'needle.mp4').unlink()
        folder.rmdir()
        self.index.listing(self.root, refresh=True)
        self.index.wait_for_idle()
        self.assertEqual(self.index.search([self.root], query='needle')['total'], 0)
        self.assertEqual(self.index.favorites('p', [self.root])['total'], 1)


if __name__ == '__main__':
    unittest.main()
