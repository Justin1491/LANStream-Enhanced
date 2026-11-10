import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import directory_index
from directory_index import DirectoryIndex, root_id


class CleanupIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'media'
        self.root.mkdir()
        self.db = Path(self.tmp.name) / 'index.sqlite3'
        self.index = DirectoryIndex(self.db, lambda suffix: 'video' if suffix == '.mp4' else 'image' if suffix == '.jpg' else None)

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def scan(self, root=None, relative=''):
        self.index.listing(root or self.root, relative, refresh=True)
        self.assertTrue(self.index.wait_for_idle())

    def file(self, relative, size=1):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * size)
        return path

    @staticmethod
    def fingerprint(path):
        info = path.stat()
        return {key: getattr(info, 'st_' + key) for key in ('size', 'mtime', 'mtime_ns', 'ctime_ns', 'ino', 'dev')}

    def test_duration_persists_and_changed_snapshot_invalidates(self):
        path = self.file('clip.mp4')
        self.scan()
        self.assertTrue(self.index.save_duration(self.root, 'clip.mp4', self.fingerprint(path), 12.5))
        self.index.close()
        self.index = DirectoryIndex(self.db, lambda suffix: 'video')
        self.assertEqual(self.index.cleanup([self.root])['items'][0]['duration'], 12.5)
        old = self.fingerprint(path)
        path.write_bytes(b'changed')
        self.scan()
        self.assertIsNone(self.index.cleanup([self.root])['items'][0]['duration'])
        self.assertFalse(self.index.save_duration(self.root, 'clip.mp4', old, 99))
        self.assertEqual(len(list(self.index.duration_candidates([self.root]))), 1)

    def test_trusted_restored_duration_can_seed_before_matching_snapshot(self):
        path = self.file('restored.mp4')
        fingerprint = self.fingerprint(path)
        self.assertFalse(self.index.save_duration(self.root, 'restored.mp4', fingerprint, 9))
        self.assertTrue(self.index.save_duration(self.root, 'restored.mp4', fingerprint, 9, require_entry=False))
        self.assertEqual(self.index.cleanup([self.root])['total'], 0)
        self.scan()
        self.assertEqual(self.index.cleanup([self.root])['items'][0]['duration'], 9)
        path.write_bytes(b'replacement')
        self.scan()
        self.assertIsNone(self.index.cleanup([self.root])['items'][0]['duration'])

    def test_trusted_duration_seed_rejects_unknown_invalid_and_reserved(self):
        path = self.file('clip.mp4')
        fingerprint = self.fingerprint(path)
        self.assertFalse(self.index.save_duration(self.root, 'clip.mp4', fingerprint, None, require_entry=False))
        for duration in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError):
                self.index.save_duration(self.root, 'clip.mp4', fingerprint, duration, require_entry=False)
        self.assertFalse(self.index.save_duration(self.root, '.lanstream-trash/clip.mp4', fingerprint, 9, require_entry=False))

    def test_duration_sort_unknown_last_and_default_does_not_join(self):
        for name in ('a.mp4', 'b.mp4', 'c.mp4'):
            self.file(name)
        (self.root / 'folder').mkdir()
        self.scan()
        self.index.save_duration(self.root, 'a.mp4', self.fingerprint(self.root / 'a.mp4'), 0)
        self.index.save_duration(self.root, 'b.mp4', self.fingerprint(self.root / 'b.mp4'), 20)
        statements = []
        self.index._db.set_trace_callback(statements.append)
        self.index.listing(self.root)
        self.assertFalse(any('media_metadata' in statement for statement in statements))
        for descending, expected in ((False, ['a.mp4', 'b.mp4', 'c.mp4']), (True, ['b.mp4', 'a.mp4', 'c.mp4'])):
            result = self.index.cleanup([self.root], sort='duration', descending=descending, page_size=1)
            self.assertEqual(result['items'][0]['name'], expected[0])
            result = self.index.listing(self.root, sort='duration', descending=descending)
            self.assertEqual([item['name'] for item in result['items']], ['folder', *expected])

    def test_failed_invalid_duration_and_batched_candidates(self):
        for name in ('a.mp4', 'b.mp4', 'c.mp4', 'picture.jpg'):
            self.file(name)
        self.scan()
        for value in (float('nan'), float('inf'), -1):
            with self.assertRaises(ValueError):
                self.index.save_duration(self.root, 'a.mp4', self.fingerprint(self.root / 'a.mp4'), value)
        self.index.save_duration(self.root, 'a.mp4', self.fingerprint(self.root / 'a.mp4'), None, error='Probe failed')
        self.index.save_duration(self.root, 'b.mp4', self.fingerprint(self.root / 'b.mp4'), 10)
        self.assertEqual([row['rel'] for row in self.index.duration_candidates([self.root], batch_size=1)], ['c.mp4'])
        self.assertEqual([row['rel'] for row in self.index.duration_candidates([self.root], retry_failed=True, batch_size=1)], ['a.mp4', 'c.mp4'])

    def test_grouping_after_scope_filter_and_before_paging_deduplicates_roots(self):
        for number in range(105):
            self.file(f'nested/clip{number}.mp4', 7)
        self.file('nested/lone.mp4', 8)
        self.file('outside.mp4', 8)
        self.file('nested/picture.jpg', 8)
        self.scan()
        self.scan(relative='nested')
        self.scan(self.root / 'nested')
        result = self.index.cleanup([self.root, self.root / 'nested'], matching_sizes=True, file_type='videos', page=2)
        self.assertEqual(result['total'], 107)
        self.assertEqual(sum(row['size_count'] == 105 for row in result['items']), 5)
        result = self.index.cleanup([self.root, self.root / 'nested'], scope_root=root_id(self.root), scope_relative='nested', matching_sizes=True, file_type='videos', page=2)
        self.assertEqual(result['total'], 105)
        self.assertEqual(len(result['items']), 5)
        self.assertTrue(all(row['size_count'] == 105 and row['version'] for row in result['items']))
        self.assertEqual(self.index.cleanup([self.root], query='clip104', matching_sizes=True)['total'], 0)

    def test_scope_exclusions_and_cached_only_reads(self):
        self.file('nested/child/deep.mp4')
        self.file('nested/Straße.mp4')
        self.file('secret/hidden.mp4')
        self.scan(relative='nested')
        self.scan(relative='nested/child')
        self.scan(relative='secret')
        with patch('directory_index.os.scandir', side_effect=AssertionError('read enumerated media')):
            result = self.index.cleanup([self.root], query='STRASSE', scope_root=root_id(self.root), scope_relative='nested', recursive=False)
            self.assertEqual(result['total'], 1)
            self.assertEqual(self.index.cleanup([self.root], excluded={root_id(self.root): ('secret',)})['total'], 2)
            self.assertEqual(len(list(self.index.duration_candidates([self.root], scope_relative='nested', recursive=False))), 1)

    def test_cleanup_read_does_not_wait_on_shared_index_lock(self):
        self.file('clip.mp4')
        self.scan()
        result, done = [], threading.Event()
        def read():
            try:
                result.append(self.index.cleanup([self.root])['total'])
            finally:
                done.set()
        with self.index._lock:
            worker = threading.Thread(target=read)
            worker.start()
            completed = done.wait(2)
        worker.join(3)
        self.assertTrue(completed)
        self.assertEqual(result, [1])

    def test_folder_picker_is_indexed_only_and_filters_before_paging(self):
        self.file('alpha/clip.mp4')
        self.file('blocked/clip.mp4')
        self.file('omega/clip.mp4')
        self.scan()
        before = self.index.metrics()['scans_started']
        with patch('directory_index.os.scandir', side_effect=AssertionError('folder picker scanned')):
            result = self.index.cleanup_folders(self.root, excluded=('blocked',), page_size=1, page=2)
            self.assertEqual(result['total'], 2)
            self.assertEqual(result['items'][0]['rel'], 'omega')
            self.assertEqual(self.index.cleanup_folders(self.root, 'unindexed')['total'], 0)
        self.assertEqual(self.index.metrics()['scans_started'], before)

    def test_cleanup_reports_stale_and_offline_cache_without_refresh(self):
        self.file('clip.mp4')
        self.scan()
        self.index.ttl = -1
        before = self.index.metrics()['scans_started']
        self.assertEqual(self.index.cleanup([self.root])['state'], 'stale')
        with self.index._lock:
            self.index._errors[(root_id(self.root), '')] = ('offline', 'Directory unavailable.')
        self.assertEqual(self.index.cleanup([self.root])['state'], 'offline')
        self.assertEqual(self.index.metrics()['scans_started'], before)

    def test_reserved_paths_suppressed_from_new_and_old_snapshots_and_favorites(self):
        self.file('.LaNsTrEaM-TrAsH/item/payload.mp4')
        self.file('okay.mp4')
        self.scan()
        self.assertEqual(self.index.listing(self.root)['total'], 1)
        self.assertTrue(directory_index.is_reserved_path(r'C:\media\.LANSTREAM-TRASH\item'))
        rid = root_id(self.root)
        with self.index._lock, self.index._db:
            self.index._db.execute("INSERT INTO folders VALUES(?,?,?,?)", (rid, '.LaNsTrEaM-TrAsH/item', 'old', 1))
            self.index._db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)", (rid, '.LaNsTrEaM-TrAsH/item', 'old', 'payload.mp4', '.LaNsTrEaM-TrAsH/item/payload.mp4', 'video', 1, 1, 'payload.mp4'))
        self.index.set_favorite('p', self.root, '.LaNsTrEaM-TrAsH/item/payload.mp4', 'video', 'payload.mp4', True)
        search = self.index.search([self.root], query='payload')
        self.assertEqual(search['total'], 0)
        self.assertEqual(search['coverage'][0]['items'], 1)
        self.assertEqual(search['coverage'][0]['folders'], 1)
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)
        self.assertEqual(self.index.favorites('p', [self.root])['total'], 0)
        self.assertEqual(self.index.favorite_flags('p', [{'root_id': rid, 'rel': '.LaNsTrEaM-TrAsH/item/payload.mp4'}]), [False])
        self.assertEqual(list(self.index.thumbnail_sources(self.root, '.LaNsTrEaM-TrAsH/item')), [])
        self.assertEqual(self.index.cleanup([self.root / '.LaNsTrEaM-TrAsH' / 'item'])['total'], 0)

    def test_hidden_aliases_favorites_restart_and_new_alias(self):
        path = self.file('nested/clip.mp4')
        alias = self.root / 'nested'
        self.scan(relative='nested')
        self.index.set_favorite('p', alias, 'clip.mp4', 'video', 'clip.mp4', True)
        self.index.hide_file(self.root, 'nested/clip.mp4')
        self.scan(alias)
        self.scan(self.root.parent, 'media/nested')
        self.assertEqual(self.index.cleanup([self.root.parent])['total'], 0)
        self.assertEqual(self.index.listing(alias)['total'], 0)
        self.assertEqual(self.index.favorites('p', [alias])['total'], 0)
        self.index.close()
        self.index = DirectoryIndex(self.db, lambda suffix: 'video')
        self.assertEqual(self.index.cleanup([self.root, alias])['total'], 0)
        with self.index._condition:
            self.index.hide_file(alias, 'clip.mp4', False)
            self.assertEqual(self.index.cleanup([self.root, alias])['total'], 0)
        self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.cleanup([alias])['total'], 1)
        self.assertTrue(path.exists())

    def test_unhide_discards_cached_mixed_case_entry(self):
        self.file('Folder/Clip.mp4')
        self.scan(relative='Folder')
        self.index.hide_file(self.root, 'Folder/Clip.mp4')
        with self.index._condition:
            self.index.hide_file(self.root, 'Folder/Clip.mp4', False)
            self.assertEqual(self.index.cleanup([self.root])['total'], 0)
        self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)

    def test_scan_held_across_hide_and_unhide_cannot_publish_old_entry(self):
        self.file('clip.mp4')
        self.scan()
        entered, release = threading.Event(), threading.Event()
        original = self.index._insert
        versions = []
        def held(batch):
            versions.append(batch[0][2])
            entered.set()
            release.wait(3)
            original(batch)
        with patch.object(self.index, '_insert', side_effect=held):
            self.index.listing(self.root, refresh=True)
            self.assertTrue(entered.wait(2))
            self.index.hide_file(self.root, 'clip.mp4')
            self.index.hide_file(self.root, 'clip.mp4', False)
            release.set()
            self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)
        self.assertNotEqual(self.index.cleanup([self.root])['items'][0]['version'], versions[0])

    def test_confirmed_tombstone_releases_replacement_with_same_size_and_mtime(self):
        path = self.file('nested/clip.mp4', 5)
        alias = self.root / 'nested'
        self.scan(relative='nested')
        self.scan(alias)
        self.index.set_favorite('p', alias, 'clip.mp4', 'video', 'clip.mp4', True)
        original = self.fingerprint(path)
        self.index.hide_file(self.root, 'nested/clip.mp4', fingerprint=original, pending=True)
        payload = Path(self.tmp.name) / 'trashed.mp4'
        path.rename(payload)
        self.index.hide_file(self.root, 'nested/clip.mp4', fingerprint=original)
        self.scan(alias)
        self.assertEqual(self.index.cleanup([self.root, alias])['total'], 0)
        path.write_bytes(b'other')
        os.utime(path, ns=(original['mtime_ns'], original['mtime_ns']))
        self.scan(alias)
        result = self.index.cleanup([self.root, alias])
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['size'], 5)
        self.assertEqual(self.index.favorites('p', [alias])['total'], 1)
        self.assertTrue(payload.exists())

    def test_pending_tombstone_cannot_release_for_changed_source(self):
        path = self.file('clip.mp4')
        self.scan()
        original = self.fingerprint(path)
        self.index.hide_file(self.root, 'clip.mp4', fingerprint=original, pending=True)
        path.write_bytes(b'changed before completion')
        self.scan()
        self.assertEqual(self.index.cleanup([self.root])['total'], 0)
        self.index.hide_file(self.root, 'clip.mp4', fingerprint=original)
        self.scan()
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)

    def test_scan_started_before_move_cannot_publish_or_clear_tombstone(self):
        path = self.file('clip.mp4')
        self.scan()
        original = self.fingerprint(path)
        entered, release = threading.Event(), threading.Event()
        insert = self.index._insert
        def held(batch):
            entered.set()
            release.wait(3)
            insert(batch)
        with patch.object(self.index, '_insert', side_effect=held):
            self.index.listing(self.root, refresh=True)
            self.assertTrue(entered.wait(2))
            self.index.hide_file(self.root, 'clip.mp4', fingerprint=original, pending=True)
            path.rename(Path(self.tmp.name) / 'trashed.mp4')
            self.index.hide_file(self.root, 'clip.mp4', fingerprint=original)
            path.write_bytes(b'replacement')
            release.set()
            self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.cleanup([self.root])['total'], 0)
        self.scan()
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)

    def test_default_tombstone_uses_indexed_version_for_changed_replacement(self):
        path = self.file('clip.mp4')
        self.scan()
        self.index.hide_file(self.root, 'clip.mp4')
        path.write_bytes(b'replacement')
        self.scan()
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)

    def test_old_record_cannot_clear_newer_same_path_pending_or_confirmed_fence(self):
        path = self.file('clip.mp4')
        self.scan()
        older = self.fingerprint(path)
        self.index.hide_file(self.root, 'clip.mp4', fingerprint=older)
        path.write_bytes(b'newer file')
        newer = self.fingerprint(path)
        for pending in (True, False):
            self.index.hide_file(self.root, 'clip.mp4', fingerprint=newer, pending=True)
            if not pending:
                self.index.hide_file(self.root, 'clip.mp4', fingerprint=newer)
            self.index.hide_file(self.root, 'clip.mp4', False, fingerprint=older)
            self.scan()
            self.assertEqual(self.index.cleanup([self.root])['total'], 0)
        self.index.hide_file(self.root, 'clip.mp4', False, fingerprint=dict(reversed(list(newer.items()))))
        self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)

    def test_recovery_old_record_cannot_replace_newer_pending_or_confirmed_fence(self):
        path = self.file('clip.mp4')
        self.scan()
        older = self.fingerprint(path)
        self.index.hide_file(self.root, 'clip.mp4', fingerprint=older)
        path.write_bytes(b'newer file')
        newer = self.fingerprint(path)
        for pending in (True, False):
            self.index.hide_file(self.root, 'clip.mp4', fingerprint=newer, pending=True)
            if not pending:
                self.index.hide_file(self.root, 'clip.mp4', fingerprint=newer)
            self.index.hide_file(self.root, 'clip.mp4', fingerprint=older)
            self.scan()
            self.assertEqual(self.index.cleanup([self.root])['total'], 0)

    def test_mismatched_unhide_refreshes_new_restore_without_clearing_old_fence_early(self):
        path = self.file('clip.mp4')
        self.scan()
        older = self.fingerprint(path)
        self.index.hide_file(self.root, 'clip.mp4', fingerprint=older)
        path.write_bytes(b'newly restored')
        newer = self.fingerprint(path)
        with self.index._condition:
            self.index.hide_file(self.root, 'clip.mp4', False, fingerprint=newer)
            self.assertEqual(self.index.cleanup([self.root])['total'], 0)
        self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.index.cleanup([self.root])['total'], 1)


if __name__ == '__main__':
    unittest.main()
