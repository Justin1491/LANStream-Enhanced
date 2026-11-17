"""Cleanup routes use fictional media and isolated config/cache only."""
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


class CleanupRoutes(unittest.TestCase):
    def setUp(self):
        import app as server
        self.server = server
        self.temp = tempfile.TemporaryDirectory()
        work = Path(self.temp.name)
        self.root = work / 'media'
        self.root.mkdir()
        (self.root / 'sample clip.mp4').write_bytes(b'fictional sample')
        (self.root / 'Private').mkdir()
        (self.root / 'Private' / 'hidden.mp4').write_bytes(b'private')
        config = work / 'config.txt'
        config.write_text(f'<alice>\n{self.root}\n-/Private\n<bob>\n{self.root}\n', encoding='utf-8')
        self.index = server.DirectoryIndex(work / 'index.sqlite3', server.kind_for_suffix)
        self.patches = [patch.object(server, 'CONFIG_FILE', config),
                        patch.object(server, 'directory_index', self.index)]
        for item in self.patches:
            item.start()
        if hasattr(server, 'cleanup_service'):
            from cleanup_routes import CleanupService
            self.service = CleanupService(server, work / 'cleanup')
            self.service_patch = patch.object(server, 'cleanup_service', self.service)
            self.service_patch.start()
        self.client = server.app.test_client()
        server.app.config.update(TESTING=True)
        self.client.post('/login', data={'password': 'alice'})
        self.index.listing(self.root)
        self.assertTrue(self.index.wait_for_idle())

    def tearDown(self):
        if hasattr(self, 'service'):
            self.service.close()
            self.service_patch.stop()
        self.index.close()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def csrf(self):
        self.client.get('/cleanup')
        with self.client.session_transaction() as value:
            return {'X-LANStream-CSRF': value['csrf_token']}

    def post(self, payload):
        return self.client.post('/api/cleanup/actions', json=payload, headers=self.csrf())

    def wait_job(self, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.client.get('/api/cleanup/jobs/' + job['job_id']).get_json()
            if result['state'] == 'complete':
                return result
            time.sleep(.01)
        self.fail('cleanup job did not finish')

    def rename(self, row, name):
        response = self.post(dict(action='rename', items=[dict(selection_token=row['selection_token'], name=name)],
                                  operation_id=str(uuid.uuid4())))
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        return self.wait_job(response.get_json())['results'][0]

    def test_rename_preserves_extension_favorites_durations_and_new_urls(self):
        from cleanup_jobs import fingerprint
        source = self.root / 'sample clip.mp4'
        self.index.save_duration(self.root, source.name, fingerprint(source), 42.5)
        self.index.set_favorite('alice', self.root, source.name, 'video', source.name, True)
        self.index.set_favorite('bob', self.root, source.name, 'video', source.name, True)
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            row = self.client.get('/api/cleanup/files').get_json()['items'][0]
            result = self.rename(row, 'Summer.final 你好😀😀😀')
            self.assertEqual(result['status'], 'renamed', result)
            self.assertFalse(source.exists())
            target = self.root / 'Summer.final 你好😀😀😀.mp4'
            self.assertEqual(target.read_bytes(), b'fictional sample')
            self.assertTrue(self.index.wait_for_idle())
            rows = self.client.get('/api/cleanup/files').get_json()['items']
            self.assertEqual([r['name'] for r in rows], [target.name])
            self.assertEqual(rows[0]['duration'], 42.5)
            for profile in ('alice', 'bob'):
                favorite = self.index.favorites(profile, [self.root])['items'][0]
                self.assertEqual(favorite['rel'], target.name)
            self.assertIn('Summer.final', rows[0]['url'])
            self.assertEqual(self.rename(row, 'stale')['status'], 'error')

    def test_rename_no_overwrite_case_only_and_unchanged_name(self):
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            row = self.client.get('/api/cleanup/files').get_json()['items'][0]
            other = self.root / 'existing.mp4'
            other.write_bytes(b'keep destination')
            self.assertEqual(self.rename(row, 'existing')['status'], 'error')
            self.assertEqual(other.read_bytes(), b'keep destination')
            self.assertEqual(self.rename(row, 'sample clip')['status'], 'renamed')
            self.assertEqual(self.rename(row, 'Sample Clip')['status'], 'renamed')
            self.assertIn('Sample Clip.mp4', [p.name for p in self.root.iterdir()])

    def test_rename_rejects_invalid_names_changed_and_busy_files(self):
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            row = self.client.get('/api/cleanup/files').get_json()['items'][0]
            for name in ('', ' ', '.', '..', '../outside', 'bad/name', 'bad\\name', 'bad:name',
                         'bad?name', 'bad*name', 'bad"name', 'bad<name', 'bad|name', 'bad\nname',
                         'trailing.', 'trailing ', 'CON', 'NUL.txt', 'x' * 256):
                with self.subTest(name=name):
                    self.assertEqual(self.rename(row, name)['status'], 'error')
            with patch.object(self.service.trash, 'busy', return_value=True):
                self.assertEqual(self.rename(row, 'busy')['status'], 'error')
            (self.root / row['rel']).write_bytes(b'changed after selection')
            self.assertEqual(self.rename(row, 'changed')['status'], 'error')
            self.assertTrue((self.root / row['rel']).exists())

    def test_rename_updates_nested_root_aliases_without_waiting_for_a_scan(self):
        from cleanup_jobs import fingerprint
        from directory_index import root_id
        folder = self.root / 'MixedCase'
        folder.mkdir()
        source = folder / 'nested.mp4'
        source.write_bytes(b'fictional nested file')
        self.index.listing(self.root, 'MixedCase')
        self.index.listing(folder)
        self.assertTrue(self.index.wait_for_idle())
        before = fingerprint(source)
        for root, rel in ((self.root, 'MixedCase/nested.mp4'), (folder, 'nested.mp4')):
            self.index.set_favorite('alice', root, rel, 'video', source.name, True)
            self.index.save_duration(root, rel, before, 72.5)
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            row = next(r for r in self.client.get('/api/cleanup/files').get_json()['items'] if r['name']=='nested.mp4')
            # Hold scans to exercise the immediate cached listing during a slow SMB refresh.
            with patch.object(self.index, '_schedule'):
                result = self.rename(row, 'Nested New')
                self.assertEqual(result['status'], 'renamed', result)
                for root, rel in ((self.root, 'MixedCase/Nested New.mp4'), (folder, 'Nested New.mp4')):
                    rows = self.index.cleanup([root])['items']
                    item = next(r for r in rows if r['name']=='Nested New.mp4')
                    self.assertEqual(item['rel'], rel)
                    self.assertEqual(item['duration'], 72.5)
                    favorite = self.index.favorites('alice', [root])['items'][0]
                    self.assertEqual(favorite['rel'], rel)
                    self.assertEqual(favorite['root_id'], root_id(root))

    def test_rename_reuses_a_trashed_name_without_hiding_the_new_file(self):
        destination = self.root / 'previous.mp4'
        destination.write_bytes(b'original destination to keep in Trash')
        self.index.listing(self.root, refresh=True)
        self.assertTrue(self.index.wait_for_idle())
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            rows = self.client.get('/api/cleanup/files').get_json()['items']
            previous = next(r for r in rows if r['name']=='previous.mp4')
            source = next(r for r in rows if r['name']=='sample clip.mp4')
            preview = self.wait_job(self.post(dict(action='preview_move',items=[{'selection_token':previous['selection_token']}],operation_id=str(uuid.uuid4()))).get_json())
            moved = self.wait_job(self.post(dict(action='move',items=[{'confirmation_token':preview['results'][0]['confirmation_token']}],operation_id=str(uuid.uuid4()))).get_json())
            self.assertEqual(moved['results'][0]['status'],'moved')
            self.assertTrue(self.index.wait_for_idle())
            with patch.object(self.index, '_schedule'):
                result = self.rename(source, 'previous')
                self.assertEqual(result['status'],'renamed',result)
                self.assertEqual([r['name'] for r in self.client.get('/api/cleanup/files').get_json()['items']],['previous.mp4'])
            self.assertEqual(destination.read_bytes(), b'fictional sample')
            trashed = self.client.get('/api/cleanup/trash').get_json()['items'][0]
            payload = self.root / '.lanstream-trash' / trashed['item_id'] / 'payload'
            self.assertEqual(payload.read_bytes(), b'original destination to keep in Trash')

    def test_rename_access_tokens_csrf_and_write_setting(self):
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            row = self.client.get('/api/cleanup/files').get_json()['items'][0]
            payload = dict(action='rename', items=[dict(selection_token=row['selection_token'],name='new')], operation_id=str(uuid.uuid4()))
            self.assertEqual(self.client.post('/api/cleanup/actions',json=payload).status_code,403)
            for items in ([dict(selection_token='tampered',name='new')], payload['items'] * 2,
                          [dict(selection_token=row['selection_token'], name=5)]):
                self.assertEqual(self.post(payload | {'items': items}).status_code,400)
            other = self.server.app.test_client()
            other.post('/login',data={'password':'bob'})
            other.get('/cleanup')
            with other.session_transaction() as session:
                headers={'X-LANStream-CSRF':session['csrf_token']}
            self.assertEqual(other.post('/api/cleanup/actions',json=payload,headers=headers).status_code,400)
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='false'):
            self.assertEqual(self.post(payload).status_code,403)
            self.assertNotIn('selection_token', self.client.get('/api/browse/0/').get_json()['items'][0])

    def test_detail_and_browse_offer_rename_without_changing_existing_selection(self):
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'), \
                patch.object(self.server, 'probe', return_value={'streams':[], 'format':{}}), \
                patch.object(self.server.image_scheduler, 'submit'):
            response = self.client.get('/video/0/sample%20clip.mp4')
            self.assertEqual(response.status_code,200)
            self.assertIn(b'data-rename-file', response.data)
            row = next(r for r in self.client.get('/api/browse/0/?details=1').get_json()['items'] if r['kind']=='video')
            self.assertIn('selection_token',row)
            self.assertEqual(self.rename(row, 'from Details')['status'], 'renamed')

    def test_detail_delete_uses_existing_signed_preview_and_recoverable_trash(self):
        import html
        import re
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'), \
                patch.object(self.server, 'probe', return_value={'streams':[], 'format':{}}), \
                patch.object(self.server.image_scheduler, 'submit'):
            page = self.client.get('/video/0/sample%20clip.mp4').get_data(as_text=True)
            self.assertIn('id="deleteFile"', page)
            token = html.unescape(re.search(r'id="deleteFile"[^>]*data-token="([^"]+)"', page)[1])
            job = self.post(dict(action='preview_move',items=[dict(selection_token=token)],operation_id=str(uuid.uuid4())))
            ready = self.wait_job(job.get_json())['results'][0]
            self.assertEqual(ready['status'], 'ready')
            self.assertTrue((self.root / 'sample clip.mp4').exists())
            job = self.post(dict(action='move',items=[dict(confirmation_token=ready['confirmation_token'])],operation_id=str(uuid.uuid4())))
            self.assertEqual(self.wait_job(job.get_json())['results'][0]['status'], 'moved')
            self.assertFalse((self.root / 'sample clip.mp4').exists())
            self.assertEqual(self.client.get('/api/cleanup/trash').get_json()['items'][0]['name'], 'sample clip.mp4')
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='false'), \
                patch.object(self.server, 'probe', return_value={'streams':[], 'format':{}}), \
                patch.object(self.server.image_scheduler, 'submit'):
            (self.root / 'sample clip.mp4').write_bytes(b'fictional sample')
            self.assertNotIn(b'id="deleteFile"', self.client.get('/video/0/sample%20clip.mp4').data)

    def test_player_hides_single_audio_without_losing_transcode_track(self):
        for count in (0, 1, 2):
            streams = [{'codec_type':'audio','index':i+2,'codec_name':'aac'} for i in range(count)]
            with patch.object(self.server, 'probe', return_value={'streams':streams,'format':{}}), \
                    patch.dict(self.server.SETTINGS, autoplay='false'):
                page = self.client.get('/play/0/sample%20clip.mp4').get_data(as_text=True)
                self.assertEqual('<select id="audioSelect"' in page, count > 1)
                if count == 1:
                    self.assertIn('id="audioSelect" value="2"', page)
                self.assertIn('const autoplay=false;', page)

    def test_page_and_cached_table_without_filesystem_or_probe(self):
        self.assertEqual(self.client.get('/cleanup').status_code, 200)
        with patch('os.scandir', side_effect=AssertionError('enumerated media')), \
                patch.object(self.server, 'probe', side_effect=AssertionError('probed media')):
            response = self.client.get('/api/cleanup/files?q=sample&sort=size')
            self.assertEqual(response.status_code, 200)
            result = response.get_json()
            self.assertEqual(result['total'], 1)
            self.assertIn('selection_token', result['items'][0])
            self.assertFalse(result['writes_enabled'])

    def test_write_guard_csrf_forged_tokens_and_limits(self):
        payload = dict(action='preview_move', items=[{'selection_token': 'forged'}], operation_id=str(uuid.uuid4()))
        self.assertEqual(self.post(payload).status_code, 403)
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            self.assertEqual(self.client.post('/api/cleanup/actions', json=payload).status_code, 403)
            self.assertEqual(self.post(payload).status_code, 400)
            self.assertEqual(self.post(dict(payload, items=payload['items'] * 101)).status_code, 400)
            self.assertEqual(self.post(dict(payload, operation_id='../bad')).status_code, 400)

    def test_trash_preview_move_restore_and_profile_safe_jobs(self):
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            from cleanup_jobs import fingerprint
            self.index.save_duration(self.root, 'sample clip.mp4', fingerprint(self.root / 'sample clip.mp4'), 42.5)
            row = self.client.get('/api/cleanup/files').get_json()['items'][0]
            preview = self.wait_job(self.post(dict(action='preview_move', items=[{'selection_token': row['selection_token']}], operation_id=str(uuid.uuid4()))).get_json())
            token = preview['results'][0]['confirmation_token']
            moved = self.wait_job(self.post(dict(action='move', items=[{'confirmation_token': token}], operation_id=str(uuid.uuid4()))).get_json())
            self.assertEqual(moved['results'][0]['status'], 'moved')
            self.assertFalse((self.root / row['rel']).exists())
            self.assertEqual(self.client.get('/api/cleanup/files').get_json()['total'], 0)
            trashed = self.client.get('/api/cleanup/trash').get_json()['items'][0]
            other = self.server.app.test_client()
            other.post('/login', data={'password': 'bob'})
            self.assertEqual(other.get('/api/cleanup/jobs/' + moved['job_id']).status_code, 404)
            preview = self.wait_job(self.post(dict(action='preview_restore', items=[{'item_id': trashed['item_id']}], operation_id=str(uuid.uuid4()))).get_json())
            restored = self.wait_job(self.post(dict(action='restore', items=[{'confirmation_token': preview['results'][0]['confirmation_token']}], operation_id=str(uuid.uuid4()))).get_json())
            self.assertEqual(restored['results'][0]['status'], 'restored')
            self.assertEqual((self.root / row['rel']).read_bytes(), b'fictional sample')
            self.assertTrue(self.index.wait_for_idle())
            self.assertEqual(self.client.get('/api/cleanup/files').get_json()['items'][0]['duration'], 42.5)

    def test_reserved_paths_are_never_served_or_indexed(self):
        target = self.root / '.lanstream-trash'
        target.mkdir()
        (target / 'payload.mp4').write_bytes(b'private trash')
        for url in ('/browse/0/.lanstream-trash', '/api/browse/0/.lanstream-trash',
                    '/raw/0/.lanstream-trash/payload.mp4', '/video/0/.lanstream-trash/payload.mp4'):
            self.assertIn(self.client.get(url).status_code, (403, 404), url)

    def test_invalid_query_and_duration_sort_do_not_probe(self):
        self.assertEqual(self.client.get('/api/cleanup/files?sort=arbitrary').status_code, 400)
        self.assertEqual(self.client.get('/api/cleanup/files?scope=arbitrary').status_code, 400)
        with patch.object(self.server, 'probe', side_effect=AssertionError('duration sort probed')):
            response = self.client.get('/api/browse/0/?sort=duration&details=1')
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.get_json()['items'][0]['duration'])

    def test_groups_search_returns_renamed_subfolder_members_without_io(self):
        copies = self.root / 'Copies' / 'Deep'
        copies.mkdir(parents=True)
        original = self.root / 'sample clip.mp4'
        (copies / 'renamed.mp4').write_bytes(original.read_bytes())
        self.index.listing(self.root)
        self.index.listing(self.root, 'Copies')
        self.index.listing(self.root, 'Copies/Deep')
        self.assertTrue(self.index.wait_for_idle())
        from directory_index import root_id
        query = dict(scope='subtree', root=root_id(self.root), group_by='size', q='sample')
        with patch('os.scandir', side_effect=AssertionError('enumerated media')), \
                patch.object(self.server, 'probe', side_effect=AssertionError('probed media')):
            response = self.client.get('/api/cleanup/groups', query_string=query)
            self.assertEqual(response.status_code, 200)
            result = response.get_json()
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['page_size'], 20)
            self.assertEqual(result['items'][0]['count'], 2)
            members = self.client.get('/api/cleanup/files', query_string=dict(query, group_key=result['items'][0]['group_key']))
            self.assertEqual(members.status_code, 200)
            rows = members.get_json()['items']
            self.assertEqual({row['name'] for row in rows}, {'sample clip.mp4', 'renamed.mp4'})
            self.assertTrue(all(row['selection_token'] for row in rows))
            self.assertEqual(self.client.get('/api/cleanup/groups', query_string=dict(query, scope='folder')).get_json()['total'], 0)

    def test_duration_groups_use_displayed_seconds_and_missing_count(self):
        from cleanup_jobs import fingerprint
        original = self.root / 'sample clip.mp4'
        copy = self.root / 'smaller.mp4'
        copy.write_bytes(b'export')
        (self.root / 'unknown.mp4').write_bytes(b'unknown duration')
        self.index.listing(self.root, refresh=True)
        self.assertTrue(self.index.wait_for_idle())
        for path, seconds in ((original, 42.9), (copy, 42.1)):
            self.index.save_duration(self.root, path.name, fingerprint(path), seconds)
        result = self.client.get('/api/cleanup/groups?group_by=duration&q=sample').get_json()
        self.assertIsNotNone(result)
        self.assertEqual(result['missing_durations'], 1)
        self.assertEqual(result['items'][0]['value'], 42)
        rows = self.client.get('/api/cleanup/files?group_by=duration&group_key=42&q=sample').get_json()['items']
        self.assertEqual({row['name'] for row in rows}, {'sample clip.mp4', 'smaller.mp4'})

    def test_group_arguments_and_scope_are_validated(self):
        for url in ('/api/cleanup/groups?group_by=hash', '/api/cleanup/groups?group_key=1',
                    '/api/cleanup/files?group_by=size', '/api/cleanup/files?group_key=1',
                    '/api/cleanup/files?group_by=size&group_key=-1',
                    '/api/cleanup/files?group_by=duration&group_key=NaN',
                    '/api/cleanup/files?group_by=size&group_key=999999999999999999999',
                    '/api/cleanup/groups?scope=subtree&root=root_forged'):
            self.assertIn(self.client.get(url).status_code, (400, 404), url)
        other = self.server.app.test_client()
        self.assertIn(other.get('/api/cleanup/groups').status_code, (302, 401, 404))

    def test_group_counts_and_members_recheck_profile_exclusions(self):
        from directory_index import root_id
        original = self.root / 'sample clip.mp4'
        hidden = self.root / 'Private' / 'hidden.mp4'
        hidden.write_bytes(original.read_bytes())
        self.index.listing(self.root, 'Private', refresh=True)
        self.assertTrue(self.index.wait_for_idle())
        self.assertEqual(self.client.get('/api/cleanup/groups').get_json()['total'], 0)
        query = dict(group_by='size', group_key=str(original.stat().st_size))
        self.assertEqual(self.client.get('/api/cleanup/files', query_string=query).get_json()['total'], 0)
        other = self.server.app.test_client()
        other.post('/login', data={'password': 'bob'})
        self.assertEqual(other.get('/api/cleanup/groups').get_json()['items'][0]['count'], 2)
        query.update(scope='subtree', root=root_id(self.root), relative='Private')
        self.assertEqual(self.client.get('/api/cleanup/files', query_string=query).status_code, 400)

    @unittest.skipUnless(os.name == 'nt', 'Native Windows Trash required')
    def test_restored_mixed_case_folder_keeps_saved_duration_group(self):
        from cleanup_jobs import fingerprint
        original = self.root / 'sample clip.mp4'
        restored = self.root / 'MixedCase' / 'Saved' / 'returned.mp4'
        restored.parent.mkdir(parents=True)
        restored.write_bytes(original.read_bytes())
        self.index.listing(self.root, 'MixedCase/Saved')
        self.assertTrue(self.index.wait_for_idle())
        for path, relative in ((original, original.name), (restored, 'MixedCase/Saved/returned.mp4')):
            self.index.save_duration(self.root, relative, fingerprint(path), 42.5)
        with patch.dict(self.server.SETTINGS, library_cleanup_enabled='true'):
            row = self.client.get('/api/cleanup/files?q=returned').get_json()['items'][0]
            preview = self.wait_job(self.post(dict(action='preview_move', items=[{'selection_token': row['selection_token']}], operation_id=str(uuid.uuid4()))).get_json())
            moved = self.wait_job(self.post(dict(action='move', items=[{'confirmation_token': preview['results'][0]['confirmation_token']}], operation_id=str(uuid.uuid4()))).get_json())
            self.assertEqual(moved['results'][0]['status'], 'moved')
            trashed = self.client.get('/api/cleanup/trash').get_json()['items'][0]
            preview = self.wait_job(self.post(dict(action='preview_restore', items=[{'item_id': trashed['item_id']}], operation_id=str(uuid.uuid4()))).get_json())
            job = self.wait_job(self.post(dict(action='restore', items=[{'confirmation_token': preview['results'][0]['confirmation_token']}], operation_id=str(uuid.uuid4()))).get_json())
            self.assertEqual(job['results'][0]['status'], 'restored')
            self.assertTrue(self.index.wait_for_idle())
            group = self.client.get('/api/cleanup/groups?group_by=duration').get_json()['items']
            self.assertEqual([(item['value'], item['count']) for item in group], [(42, 2)])
            rows = self.client.get('/api/cleanup/files?group_by=duration&group_key=42').get_json()['items']
            self.assertEqual({item['rel'] for item in rows}, {original.name, 'MixedCase/Saved/returned.mp4'})


if __name__ == '__main__':
    unittest.main()
