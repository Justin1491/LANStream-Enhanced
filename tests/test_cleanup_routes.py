"""Cleanup routes use fictional media and isolated config/cache only."""
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


if __name__ == '__main__':
    unittest.main()
