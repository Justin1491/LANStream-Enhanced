"""Route checks use fictional media/config and never access configured real shares."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ExplorerRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='lanstream-routes-')
        cls.work = Path(cls.temp.name)
        cls.config = cls.work / 'config.txt'
        cls.config.write_text('port=8061\n', encoding='utf-8')
        os.environ['LANSTREAM_CONFIG_FILE'] = str(cls.config)
        os.environ['LANSTREAM_CACHE_DIR'] = str(cls.work / 'cache')
        import app
        cls.server = app
        app.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls.server, 'directory_index'):
            cls.server.directory_index.close()
        cls.server.image_scheduler.close() if hasattr(cls.server.image_scheduler, 'close') else None
        cls.temp.cleanup()

    def setUp(self):
        self.root = self.work / self.id().rsplit('.', 1)[-1]
        self.root.mkdir()
        (self.root / 'Private').mkdir()
        (self.root / 'Private' / 'Hidden.mp4').touch()
        (self.root / 'Café Clip 2.mp4').write_bytes(b'0123456789')
        (self.root / 'Café Clip 10.mp4').touch()
        self.config.write_text(f'port=8061\n<alice>\n{self.root}\n-/Private\n<bob>\n{self.root}\n', encoding='utf-8')
        self.client = self.server.app.test_client()
        self.client.post('/login', data={'password': 'alice'})

    def warm(self, relative=''):
        url = '/api/browse/0/' + relative
        self.client.get(url)
        self.assertTrue(self.server.directory_index.wait_for_idle())
        return self.client.get(url)

    def csrf(self, client=None):
        client = client or self.client
        client.get('/')
        with client.session_transaction() as session:
            return {'X-LANStream-CSRF': session['csrf_token']}

    def test_cold_shell_and_warm_page_do_not_enumerate_in_request(self):
        response = self.client.get('/browse/0/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'explorer', response.data)
        response = self.warm()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['total'], 2)
        self.assertEqual([item['name'] for item in payload['items']], ['Café Clip 2.mp4', 'Café Clip 10.mp4'])
        with patch('os.scandir', side_effect=AssertionError('warm request scanned media')):
            self.assertEqual(self.client.get('/api/browse/0/').status_code, 200)

    def test_search_exclusions_scope_and_full_parent_paths(self):
        self.warm()
        bob = self.server.app.test_client()
        bob.post('/login', data={'password': 'bob'})
        bob.get('/api/browse/0/Private')
        self.server.directory_index.wait_for_idle()
        self.assertEqual(self.client.get('/api/search?q=HIDDEN').get_json()['total'], 0)
        results = self.client.get('/api/search?q=CAFÉ').get_json()
        self.assertEqual(results['total'], 2)
        self.assertNotIn(str(self.root), str(results))
        self.assertEqual(self.client.get('/api/browse/0/Private').status_code, 404)

    def test_favorite_profile_isolation_csrf_and_missing_source(self):
        self.warm()
        payload = dict(root_index=0, rel='Café Clip 2.mp4', kind='video', enabled=True)
        self.assertEqual(self.client.post('/api/favorites', json=payload).status_code, 403)
        response = self.client.post('/api/favorites', json=payload, headers=self.csrf())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/favorites').get_json()['total'], 1)
        bob = self.server.app.test_client()
        bob.post('/login', data={'password': 'bob'})
        self.assertEqual(bob.get('/api/favorites').get_json()['total'], 0)
        (self.root / 'Café Clip 2.mp4').unlink()
        self.assertEqual(self.client.get('/api/favorites').get_json()['total'], 1)
        self.assertEqual(self.client.get('/media/0/Café Clip 2.mp4').status_code, 404)

    def test_traversal_and_unauthenticated_api_are_rejected(self):
        for path in ['../Private', 'C:/Windows', '..%5cPrivate', '%2f%2fhost/share', 'Private%00']:
            self.assertIn(self.client.get('/api/browse/0/' + path).status_code, (400, 403, 404))
        self.assertEqual(self.server.app.test_client().get('/api/search?q=Clip').status_code, 401)

    def test_direct_byte_range_and_qsv_seek_audio_command(self):
        response = self.client.get('/media/0/Café Clip 2.mp4', headers={'Range': 'bytes=2-5'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b'2345')
        response.close()
        command = self.server.transcode_command('qsv', self.root / 'Café Clip 2.mp4', 2, 12.5)
        self.assertIn('h264_qsv', command)
        self.assertEqual(command[command.index('-ss') + 1], '12.500')
        self.assertIn('0:2', command)

    def test_low_bandwidth_command_uses_configured_bitrate_and_safe_scale(self):
        command = self.server.transcode_command('qsv', self.root / 'Café Clip 2.mp4', 0, 0, low=True)
        self.assertIn('2M', command)
        self.assertIn('128k', command)
        self.assertIn('scale=', command[command.index('-vf') + 1])
        for backend in ('qsv', 'amf', 'nvenc', 'vaapi', 'software'):
            with self.subTest(backend=backend):
                command = self.server.transcode_command(backend, self.root / 'Café Clip 2.mp4', 0, 0, low=True)
                self.assertEqual(command[command.index('-b:v') + 1], '2M')
                if '-rc' in command:
                    self.assertNotEqual(command[command.index('-rc') + 1], 'cqp')

    def test_folder_preview_never_returns_excluded_cached_descendant(self):
        cache = self.server.cache_root_dir('thumbs', self.root) / 'Private'
        cache.mkdir(parents=True)
        self.server.Image.new('RGB', (4, 4)).save(cache / 'Hidden.mp4.jpg')
        response = self.client.get('/folder-preview/0/')
        self.assertEqual(response.status_code, 404)
        response.close()

    def test_private_metadata_and_images_are_not_browser_cached_between_profiles(self):
        self.warm()
        response = self.client.get('/api/browse/0/')
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))

    def test_index_actions_reject_non_object_json(self):
        self.assertEqual(self.client.post('/api/index/0', json=['start'], headers=self.csrf()).status_code, 400)


if __name__ == '__main__':
    unittest.main()
