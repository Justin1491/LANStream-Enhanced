import json
import contextlib
import os
import sys
import tempfile
import threading
import subprocess
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from directory_index import DirectoryIndex, root_id
from library_trash import TrashManager, fingerprint


@unittest.skipUnless(os.name == 'nt', 'Windows native mutation checks')
class TrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'fictional-media'
        self.root.mkdir()
        self.path = self.root / 'example.mp4'
        self.path.write_bytes(b'fictional media only')
        self.db = Path(self.temp.name) / 'trash.sqlite3'
        self.allowed = {'owner', 'other'}
        self.hidden = []
        self.hide_versions = []
        self.manager = self.make_manager()

    def resolve(self, profile, rid, rel):
        if profile not in self.allowed or rid != root_id(self.root) or rel.startswith('excluded/'):
            raise PermissionError('private value must not appear')
        return self.root

    def make_manager(self):
        def changed(root, rel, hidden, **kwargs):
            self.hidden.append((rel, hidden))
            self.hide_versions.append(kwargs)
        return TrashManager(self.db, resolve=self.resolve, busy=lambda p: False,
                            changed=changed)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def run_job(self, action, items, profile='owner', operation=None):
        job = self.manager.submit(profile, action, items, operation or str(uuid.uuid4()))
        self.assertTrue(self.manager.wait_for_idle(10))
        return self.manager.status(profile, job['job_id'])

    def selected(self):
        info = self.path.stat()
        return {'root_id': root_id(self.root), 'rel': self.path.name, 'size': info.st_size, 'mtime': info.st_mtime}

    def preview(self, action='move', item=None, profile='owner'):
        job = self.run_job('preview_' + action, [item or self.selected()], profile)
        self.assertEqual(job['results'][0]['status'], 'ready', job)
        return {'confirmation_token': job['results'][0]['confirmation_token']}

    def move(self):
        result = self.run_job('move', [self.preview()])['results'][0]
        self.assertEqual(result['status'], 'moved', result)
        return result['item_id']

    @staticmethod
    def index_callback(index):
        def changed(root, rel, hidden, *, restored_duration=None, **kwargs):
            index.hide_file(root, rel, hidden, **kwargs)
        return changed

    def test_saved_duration_forwarded_only_for_fresh_successful_restore(self):
        token = self.preview(item=self.selected() | {'duration': 123.5})
        item = self.run_job('move', [token])['results'][0]['item_id']
        self.assertTrue(all('restored_duration' not in row for row in self.hide_versions))
        restore = self.preview('restore', {'item_id': item})
        result = self.run_job('restore', [restore | {'duration': 999}])['results'][0]
        self.assertEqual(result['status'], 'restored')
        self.assertEqual(self.hide_versions[-1]['restored_duration'], 123.5)
        self.assertEqual(self.hide_versions[-1]['fingerprint'], fingerprint(self.path))
        self.manager._startup()
        self.assertNotIn('restored_duration', self.hide_versions[-1])
        self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])
        self.assertNotIn('restored_duration', self.hide_versions[-1])
        item = self.move()
        self.run_job('purge', [self.preview('purge', {'item_id': item})])
        self.assertNotIn('restored_duration', self.hide_versions[-1])

    def test_native_move_restore_and_purge(self):
        item = self.move()
        self.assertFalse(self.path.exists())
        self.assertIn((self.path.name, True), self.hidden)
        listing = self.manager.listing('other', [self.root], {})
        self.assertEqual(listing['total'], 1)
        restore = self.preview('restore', {'item_id': item}, 'other')
        self.assertEqual(self.run_job('restore', [restore], 'other')['results'][0]['status'], 'restored')
        self.assertEqual(self.path.read_bytes(), b'fictional media only')
        item = self.move()
        purge = self.preview('purge', {'item_id': item})
        self.assertEqual(self.run_job('purge', [purge])['results'][0]['status'], 'purged')
        self.assertEqual(self.manager.listing('owner', [self.root], {})['total'], 0)

    def test_stale_preview_and_replacement_do_not_move(self):
        token = self.preview()
        self.path.unlink()
        self.path.write_bytes(b'a different replacement')
        self.assertEqual(self.run_job('move', [token])['results'][0]['status'], 'error')
        self.assertEqual(self.path.read_bytes(), b'a different replacement')
        selection = self.selected() | {'size': 1}
        self.assertEqual(self.run_job('preview_move', [selection])['results'][0]['status'], 'error')

    def test_restore_no_overwrite_and_changed_payload_no_purge(self):
        item = self.move()
        self.path.write_bytes(b'replacement')
        self.assertEqual(self.run_job('preview_restore', [{'item_id': item}])['results'][0]['status'], 'error')
        self.assertEqual(self.path.read_bytes(), b'replacement')
        self.path.unlink()
        token = self.preview('purge', {'item_id': item})
        payload = self.root / '.lanstream-trash' / item / 'payload'
        payload.write_bytes(b'replacement payload')
        self.assertEqual(self.run_job('purge', [token])['results'][0]['status'], 'error')
        self.assertTrue(payload.exists())

    def test_manifest_recovery_after_local_database_loss(self):
        item = self.move()
        self.manager.close()
        for suffix in ('', '-wal', '-shm'):
            Path(str(self.db) + suffix).unlink(missing_ok=True)
        self.manager = self.make_manager()
        result = self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])
        self.assertEqual(result['results'][0]['status'], 'recovered', result)
        self.assertEqual(self.manager.listing('other', [self.root], {})['items'][0]['item_id'], item)
        token = self.preview('restore', {'item_id': item})
        self.assertEqual(self.run_job('restore', [token])['results'][0]['status'], 'restored')

    def test_operation_id_and_authorization(self):
        token = self.preview()
        operation = str(uuid.uuid4())
        first = self.run_job('move', [token], operation=operation)
        again = self.manager.submit('owner', 'move', [token], operation)
        self.assertEqual(first['job_id'], again['job_id'])
        self.assertIsNone(self.manager.status('other', first['job_id']))
        self.allowed.remove('owner')
        self.assertEqual(self.manager.listing('owner', [self.root], {})['total'], 0)
        self.assertEqual(self.manager.status('owner', first['job_id'])['results'], [{'status': 'error', 'error': 'Access unavailable', 'request_index': 0}])

    def test_failed_items_retain_authorized_identity_and_request_index(self):
        result = self.run_job('preview_move', [self.selected() | {'size': 1}])['results'][0]
        self.assertEqual(result['request_index'], 0)
        self.assertEqual(result['rel'], self.path.name)
        self.assertEqual(result['name'], self.path.name)
        self.assertEqual(result['root_id'], root_id(self.root))
        self.allowed.remove('owner')
        result = self.run_job('preview_move', [self.selected()])['results'][0]
        self.assertEqual(result, {'status': 'error', 'error': 'Access unavailable', 'request_index': 0})

    def test_changed_source_does_not_leave_manifestless_item_directory(self):
        token = self.preview()
        self.path.write_bytes(b'changed after confirmation')
        result = self.run_job('move', [token])['results'][0]
        self.assertEqual(result['status'], 'error')
        self.assertFalse(list((self.root / '.lanstream-trash').glob('*/')))
        self.assertEqual(self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])['results'][0]['status'], 'recovered')

    def test_busy_source_handle_does_not_create_item_directory(self):
        token = self.preview()
        with open(self.path, 'rb'):
            result = self.run_job('move', [token])['results'][0]
        self.assertEqual(result['status'], 'error')
        self.assertFalse(list((self.root / '.lanstream-trash').glob('*/')))
        self.assertEqual(self.path.read_bytes(), b'fictional media only')

    def test_recovery_skips_bad_items_and_preserves_valid_payload(self):
        item = self.move()
        orphan = self.root / '.lanstream-trash' / str(uuid.uuid4())
        orphan.mkdir()
        (orphan / 'payload').write_bytes(b'unknown preserved')
        self.manager.close()
        for suffix in ('', '-wal', '-shm'):
            Path(str(self.db) + suffix).unlink(missing_ok=True)
        self.manager = self.make_manager()
        result = self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])['results'][0]
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['needs_attention'], 1)
        self.assertEqual(self.manager.listing('owner', [self.root], {})['items'][0]['item_id'], item)
        self.assertEqual((orphan / 'payload').read_bytes(), b'unknown preserved')

    def test_recovery_with_recreated_source_preserves_both_and_unhides_replacement(self):
        item = self.move()
        self.path.write_bytes(b'legitimate new original')
        result = self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])['results'][0]
        self.assertEqual(result['status'], 'recovered')
        self.assertEqual(self.manager.listing('owner', [self.root], {})['items'][0]['state'], 'attention')
        self.assertEqual(self.hidden[-1], (self.path.name, False))
        self.assertEqual(self.path.read_bytes(), b'legitimate new original')
        self.assertEqual((self.root / '.lanstream-trash' / item / 'payload').read_bytes(), b'fictional media only')

    def test_successful_purge_clears_hidden_original_path(self):
        item = self.move()
        token = self.preview('purge', {'item_id': item})
        self.path.write_bytes(b'new original')
        self.assertEqual(self.run_job('purge', [token])['results'][0]['status'], 'purged')
        self.assertEqual(self.hidden[-1], (self.path.name, False))
        self.assertEqual(self.path.read_bytes(), b'new original')

    def test_completed_purge_restart_clears_suppression_after_callback_gap(self):
        item = self.move()
        token = self.preview('purge', {'item_id': item})
        with patch.object(self.manager, '_checkpoint', side_effect=lambda point: (_ for _ in ()).throw(OSError()) if point == 'purge_completed_journal' else None):
            self.run_job('purge', [token])
        self.manager.close()
        self.manager = self.make_manager()
        self.assertTrue(self.manager.wait_for_idle(10))
        self.assertEqual(self.hidden[-1], (self.path.name, False))

    def test_completed_move_restart_promotes_pending_suppression(self):
        token = self.preview()
        with patch.object(self.manager, '_checkpoint', side_effect=lambda point: (_ for _ in ()).throw(OSError()) if point == 'move_completed_journal' else None):
            self.run_job('move', [token])
        self.assertTrue(self.hide_versions[-1]['pending'])
        self.manager.close()
        self.manager = self.make_manager()
        self.assertTrue(self.manager.wait_for_idle(10))
        self.assertFalse(self.hide_versions[-1]['pending'])

    def test_older_samepath_record_cannot_clear_or_replace_newer_suppression(self):
        index = DirectoryIndex(Path(self.temp.name) / 'index.sqlite3', lambda suffix: 'video')
        self.manager.changed = self.index_callback(index)
        try:
            index.listing(self.root)
            self.assertTrue(index.wait_for_idle())
            older = self.move()
            self.path.write_bytes(b'newer fictional media')
            newer = self.move()
            expected = self.manager._item(newer)['fingerprint']
            def assert_newer_hidden():
                with index._lock:
                    rows = index._db.execute('SELECT fingerprint,pending FROM hidden_files').fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(json.loads(rows[0][0]), expected)
                self.assertFalse(rows[0][1])
            assert_newer_hidden()
            original_scan = os.scandir
            for terminal_older in (False, True):
                if terminal_older:
                    token = self.preview('purge', {'item_id': older})
                    self.assertEqual(self.run_job('purge', [token])['results'][0]['status'], 'purged')
                    assert_newer_hidden()
                for newer_first in (False, True):
                    @contextlib.contextmanager
                    def ordered_scan(path):
                        with original_scan(path) as entries:
                            ordered = list(entries)
                        ordered.sort(key=lambda entry: entry.name == newer, reverse=newer_first)
                        yield iter(ordered)
                    with patch('library_trash.os.scandir', side_effect=ordered_scan):
                        result = self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])
                    self.assertEqual(result['results'][0]['status'], 'recovered')
                    assert_newer_hidden()
            self.assertTrue((self.root / '.lanstream-trash' / newer / 'payload').exists())
        finally:
            self.manager.close()
            index.close()

    def test_restored_then_retrashed_identity_keeps_current_suppression(self):
        index = DirectoryIndex(Path(self.temp.name) / 'index.sqlite3', lambda suffix: 'video')
        self.manager.changed = self.index_callback(index)
        try:
            index.listing(self.root)
            self.assertTrue(index.wait_for_idle())
            older = self.move()
            token = self.preview('restore', {'item_id': older})
            self.assertEqual(self.run_job('restore', [token])['results'][0]['status'], 'restored')
            newer = self.move()
            self.assertEqual(self.manager._item(older)['fingerprint'], self.manager._item(newer)['fingerprint'])
            original_scan = os.scandir
            for newer_first in (False, True):
                @contextlib.contextmanager
                def ordered_scan(path):
                    with original_scan(path) as entries:
                        ordered = list(entries)
                    ordered.sort(key=lambda entry: entry.name == newer, reverse=newer_first)
                    yield iter(ordered)
                with patch('library_trash.os.scandir', side_effect=ordered_scan):
                    self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])
                known = self.manager._rows()
                known.sort(key=lambda row: row['item_id'] == newer, reverse=newer_first)
                with patch.object(self.manager, '_rows', return_value=known):
                    self.manager._startup()
                with index._lock:
                    rows = index._db.execute('SELECT fingerprint FROM hidden_files').fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual(json.loads(rows[0][0]), self.manager._item(newer)['fingerprint'])
        finally:
            self.manager.close()
            index.close()

    def test_newest_samepath_restore_and_purge_do_not_leave_older_fence(self):
        for action in ('restore', 'purge'):
            with self.subTest(action=action):
                fixture = TrashTests('test_native_move_restore_and_purge')
                fixture.setUp()
                index = DirectoryIndex(Path(fixture.temp.name) / 'index.sqlite3', lambda suffix: 'video')
                fixture.manager.changed = self.index_callback(index)
                try:
                    index.listing(fixture.root)
                    self.assertTrue(index.wait_for_idle())
                    older = fixture.move()
                    fixture.path.write_bytes(b'newer fictional media')
                    newer = fixture.move()
                    # Recovery may encounter older A first after losing the index cache.
                    index.hide_file(fixture.root, fixture.path.name, fingerprint=fixture.manager._item(older)['fingerprint'], pending=True)
                    index.hide_file(fixture.root, fixture.path.name, fingerprint=fixture.manager._item(older)['fingerprint'])
                    token = fixture.preview(action, {'item_id': newer})
                    self.assertEqual(fixture.run_job(action, [token])['results'][0]['status'], 'restored' if action == 'restore' else 'purged')
                    self.assertTrue(index.wait_for_idle())
                    if action == 'purge':
                        fixture.path.write_bytes(b'new legitimate replacement')
                        index.listing(fixture.root, refresh=True)
                        self.assertTrue(index.wait_for_idle())
                    self.assertEqual(index.cleanup([fixture.root])['total'], 1)
                    self.assertTrue((fixture.root / '.lanstream-trash' / older / 'payload').exists())
                finally:
                    fixture.manager.close()
                    index.close()
                    fixture.tearDown()

    def test_confirmation_is_single_use_even_after_restore(self):
        token = self.preview()
        moved = self.run_job('move', [token])['results'][0]
        restored = self.run_job('restore', [self.preview('restore', {'item_id': moved['item_id']})])
        self.assertEqual(restored['results'][0]['status'], 'restored')
        self.assertEqual(self.run_job('move', [token])['results'][0]['status'], 'error')
        self.assertTrue(self.path.exists())

    def test_purge_remains_possible_after_original_parent_is_removed(self):
        folder = self.root / 'folder'
        folder.mkdir()
        self.path = folder / 'example.mp4'
        self.path.write_bytes(b'fictional')
        selected = self.selected() | {'rel': 'folder/example.mp4'}
        token = self.run_job('preview_move', [selected])['results'][0]['confirmation_token']
        item = self.run_job('move', [{'confirmation_token': token}])['results'][0]['item_id']
        folder.rmdir()
        purge = self.preview('purge', {'item_id': item})
        self.assertEqual(self.run_job('purge', [purge])['results'][0]['status'], 'purged')

    def test_reject_paths_platform_busy_and_lock(self):
        for rel in ('../example.mp4', 'x:stream', '.LANSTREAM-TRASH/x', 'NUL.mp4', '/example.mp4', 'folder./x'):
            self.assertEqual(self.run_job('preview_move', [self.selected() | {'rel': rel}])['results'][0]['status'], 'error')
        with patch.object(self.manager, 'windows', False):
            self.assertEqual(self.run_job('preview_move', [self.selected()])['results'][0]['status'], 'error')
        with patch.object(self.manager, 'busy', return_value=True):
            self.assertEqual(self.run_job('preview_move', [self.selected()])['results'][0]['error'], 'File is busy')
        self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])
        with self.manager._root_lock(self.root):
            self.assertEqual(self.run_job('preview_move', [self.selected()])['results'][0]['error'], 'Library is busy')

    def test_request_reads_do_not_access_filesystem(self):
        gate = threading.Event()
        original = self.manager._execute
        def blocked(*args):
            gate.wait(5)
            return original(*args)
        with patch.object(self.manager, '_execute', side_effect=blocked):
            job = self.manager.submit('owner', 'preview_move', [self.selected()], str(uuid.uuid4()))
            with patch('library_trash.os.stat', side_effect=AssertionError('request touched share')):
                self.manager.status('owner', job['job_id'])
                self.manager.listing('owner', [self.root], {})
            gate.set()
            self.assertTrue(self.manager.wait_for_idle(10))

    def test_fault_after_move_reconciles_without_moving_replacement(self):
        token = self.preview()
        with patch.object(self.manager, '_checkpoint', side_effect=lambda point: (_ for _ in ()).throw(OSError()) if point == 'move_renamed' else None):
            result = self.run_job('move', [token])
        self.assertEqual(result['results'][0]['status'], 'error')
        self.assertFalse(self.path.exists())
        self.manager.close()
        self.manager = self.make_manager()
        self.assertTrue(self.manager.wait_for_idle(10))
        listing = self.manager.listing('owner', [self.root], {})
        self.assertEqual(listing['items'][0]['state'], 'trashed', listing)

    def test_all_intent_and_completion_boundaries_fail_closed(self):
        boundaries = {
            'move': ('move_journal', 'move_manifest', 'move_hidden', 'move_renamed', 'move_completed_manifest', 'move_completed_journal'),
            'restore': ('restore_journal', 'restore_manifest', 'restore_applied', 'restore_completed_manifest', 'restore_completed_journal'),
            'purge': ('purge_journal', 'purge_manifest', 'purge_applied', 'purge_completed_manifest', 'purge_completed_journal'),
        }
        for action, points in boundaries.items():
            for point in points:
                with self.subTest(action=action, point=point):
                    fixture = TrashTests('test_native_move_restore_and_purge')
                    fixture.setUp()
                    try:
                        item = fixture.move() if action != 'move' else None
                        token = fixture.preview(action, {'item_id': item} if item else None)
                        def fault(current):
                            if current == point:
                                raise OSError('injected')
                        with patch.object(fixture.manager, '_checkpoint', side_effect=fault):
                            fixture.run_job(action, [token])
                        fixture.manager.close()
                        fixture.manager = fixture.make_manager()
                        self.assertTrue(fixture.manager.wait_for_idle(10))
                        payloads = list((fixture.root / '.lanstream-trash').glob('*/payload'))
                        if action != 'purge' or point in ('purge_journal', 'purge_manifest'):
                            self.assertTrue(fixture.path.exists() or payloads, 'Media was lost')
                        self.assertFalse(fixture.path.exists() and payloads, 'A move copied media')
                        if action == 'purge' and point == 'purge_applied':
                            rows = fixture.manager.listing('owner', [fixture.root], {})['items']
                            self.assertEqual(rows[0]['state'], 'attention')
                    finally:
                        fixture.tearDown()

    def test_failed_native_rename_rolls_back_visibility_and_keeps_source(self):
        for failure in (PermissionError(), OSError(17, 'cross-device')):
            token = self.preview()
            with patch('library_trash._rename', side_effect=failure):
                self.assertEqual(self.run_job('move', [token])['results'][0]['status'], 'error')
            self.assertEqual(self.path.read_bytes(), b'fictional media only')
            self.assertEqual(self.hidden[-1], (self.path.name, False))

    def test_restore_collision_after_preview_preserves_both_files(self):
        item = self.move()
        token = self.preview('restore', {'item_id': item})
        self.path.write_bytes(b'new replacement')
        self.assertEqual(self.run_job('restore', [token])['results'][0]['status'], 'error')
        self.assertEqual(self.path.read_bytes(), b'new replacement')
        self.assertTrue((self.root / '.lanstream-trash' / item / 'payload').exists())

    def test_junction_ancestor_and_replaced_payload_symlink_are_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        (outside / 'other.mp4').write_bytes(b'private')
        junction = self.root / 'junction'
        completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        selected = self.selected() | {'rel': 'junction/other.mp4', 'size': 7, 'mtime': (outside / 'other.mp4').stat().st_mtime}
        self.assertEqual(self.run_job('preview_move', [selected])['results'][0]['status'], 'error')
        item = self.move()
        payload = self.root / '.lanstream-trash' / item / 'payload'
        held = payload.with_name('preserved')
        payload.rename(held)
        try:
            payload.symlink_to(outside / 'other.mp4')
        except OSError:
            held.rename(payload)
            return  # Junction rejection above still executes without symlink privilege.
        self.assertEqual(self.run_job('preview_purge', [{'item_id': item}])['results'][0]['status'], 'error')
        self.assertEqual((outside / 'other.mp4').read_bytes(), b'private')

    def test_physical_identity_deduplicates_overlapping_selected_aliases(self):
        alias = self.root / 'alias.mp4'
        os.link(self.path, alias)
        items = [self.selected(), self.selected() | {'rel': alias.name}]
        results = self.run_job('preview_move', items)['results']
        self.assertEqual([result['status'] for result in results], ['ready', 'error'])

    def test_native_lock_excludes_another_process(self):
        script = '''
import sys
from pathlib import Path
from library_trash import TrashManager, Conflict
m=TrashManager(sys.argv[1],resolve=lambda *a:Path(sys.argv[2]),busy=lambda p:False,changed=lambda *a:None)
try:
    with m._root_lock(Path(sys.argv[2])):
        print('unexpected acquisition')
except Conflict:
    print('busy')
finally:
    m.close()
'''
        with self.manager._root_lock(self.root):
            result = subprocess.run([sys.executable, '-B', '-c', script, str(Path(self.temp.name) / 'other.sqlite3'), str(self.root)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'busy')

    def test_fence_and_hide_precede_native_move(self):
        import library_trash
        original = library_trash._rename
        def checked(handle, destination):
            self.assertTrue(self.manager.is_busy(self.path))
            self.assertEqual(self.hidden[-1], (self.path.name, True))
            self.assertTrue(self.hide_versions[-1]['pending'])
            self.assertEqual(self.hide_versions[-1]['fingerprint'], fingerprint(self.path))
            return original(handle, destination)
        token = self.preview()
        with patch('library_trash._rename', side_effect=checked):
            self.assertEqual(self.run_job('move', [token])['results'][0]['status'], 'moved')
        self.assertFalse(self.manager.is_busy(self.path))
        self.assertFalse(self.hide_versions[-1]['pending'])

    def test_malformed_recovery_metadata_never_enters_listing(self):
        item = self.move()
        manifest = self.root / '.lanstream-trash' / item / 'manifest.json'
        data = json.loads(manifest.read_text())
        data['size'] = ['malformed']
        manifest.write_text(json.dumps(data))
        self.manager.close()
        for suffix in ('', '-wal', '-shm'):
            Path(str(self.db) + suffix).unlink(missing_ok=True)
        self.manager = self.make_manager()
        result = self.run_job('recover', [{'root_id': root_id(self.root), 'rel': ''}])
        self.assertEqual(result['results'][0]['status'], 'recovered')
        self.assertEqual(result['results'][0]['needs_attention'], 1)
        self.assertEqual(self.manager.listing('owner', [self.root], {})['total'], 0)

    def test_trash_folder_scope_and_duration_unknown_last(self):
        for relative, duration in (('folder/short.mp4', 3), ('folder/long.mp4', 30), ('folder/sub/unknown.mp4', None), ('outside.mp4', 7)):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fictional')
            info = path.stat()
            selected = {'root_id': root_id(self.root), 'rel': relative, 'size': info.st_size, 'mtime': info.st_mtime, 'duration': duration}
            token = self.run_job('preview_move', [selected])['results'][0]['confirmation_token']
            self.assertEqual(self.run_job('move', [{'confirmation_token': token}])['results'][0]['status'], 'moved')
        direct = self.manager.listing('owner', [self.root], {}, scope_relative='folder', recursive=False)
        self.assertEqual(direct['total'], 2)
        excluded = self.manager.listing('owner', [self.root], {root_id(self.root): ['folder/sub']}, scope_relative='folder')
        self.assertEqual(excluded['total'], 2)
        for descending, expected in ((False, ['short.mp4', 'long.mp4', 'unknown.mp4']), (True, ['long.mp4', 'short.mp4', 'unknown.mp4'])):
            result = self.manager.listing('owner', [self.root], {}, scope_relative='folder', recursive=True, sort='duration', descending=descending)
            self.assertEqual([row['name'] for row in result['items']], expected)

    def test_root_marker_conflict_and_payload_symlink(self):
        trash = self.root / '.lanstream-trash'
        trash.mkdir()
        (trash / 'unrelated.txt').write_text('do not modify')
        self.assertEqual(self.run_job('move', [self.preview()])['results'][0]['status'], 'error')
        self.assertTrue(self.path.exists())
        self.assertEqual((trash / 'unrelated.txt').read_text(), 'do not modify')


if __name__ == '__main__':
    unittest.main()
