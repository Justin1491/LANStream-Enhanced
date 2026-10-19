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
        app.directory_index.close()
        app.directory_index = app.DirectoryIndex(cls.work / 'index.sqlite3', app.kind_for_suffix)
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

    def test_thumbnail_preparation_requires_csrf_and_respects_profile_folder_scope(self):
        import time
        from image_scheduler import ImageScheduler
        (self.root / 'Notes.txt').touch()
        self.warm()
        generated = []
        def generate(source, output, *args):
            generated.append(source.name)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'jpeg')
        scheduler = ImageScheduler(generate)
        try:
            with patch.object(self.server, 'image_scheduler', scheduler):
                url = '/api/prepare-thumbnails/0'
                self.assertEqual(self.client.post(url, json={'action': 'start'}).status_code, 403)
                self.assertEqual(self.client.post(url, json=[], headers=self.csrf()).status_code, 400)
                self.assertEqual(self.client.post(url, json={'action': 'unknown'}, headers=self.csrf()).status_code, 400)
                self.assertEqual(self.client.post(url + '?relative=Private', json={'action': 'start'}, headers=self.csrf()).status_code, 404)
                response = self.client.post(url, json={'action': 'start'}, headers=self.csrf())
                self.assertEqual(response.status_code, 200)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    preparation = self.client.get(url).get_json()['preparation']
                    if not preparation['running']:
                        break
                    time.sleep(.01)
                self.assertFalse(preparation['running'])
                self.assertEqual((preparation['ready'], preparation['failed']), (2, 0))
                self.assertEqual(set(generated), {'Café Clip 2.mp4', 'Café Clip 10.mp4'})
                bob = self.server.app.test_client()
                bob.post('/login', data={'password': 'bob'})
                self.assertIsNone(bob.get(url).get_json()['preparation'])
        finally:
            scheduler.close()

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

    def test_file_type_filter_in_browse_and_search_keeps_folders(self):
        (self.root / 'Albums').mkdir()
        for name in ('Photo.png', 'Guide.pdf', 'Story.cbz', 'Archive.cbr', 'Notes.txt', 'Readme.md', 'Ignored.bin'):
            (self.root / name).touch()
        (self.root / 'Private' / 'Hidden.png').touch()
        self.warm()
        all_names = {'Albums', 'Café Clip 2.mp4', 'Café Clip 10.mp4', 'Photo.png',
                     'Guide.pdf', 'Story.cbz', 'Archive.cbr', 'Notes.txt', 'Readme.md'}
        cases = {
            'all': all_names,
            'videos': {'Albums', 'Café Clip 2.mp4', 'Café Clip 10.mp4'},
            'pictures': {'Albums', 'Photo.png'},
            'documents': {'Albums', 'Guide.pdf', 'Story.cbz', 'Archive.cbr', 'Notes.txt', 'Readme.md'},
        }
        for endpoint in ('/api/browse/0/', '/api/search'):
            for file_type, names in cases.items():
                with self.subTest(endpoint=endpoint, file_type=file_type):
                    response = self.client.get(endpoint, query_string={'file_type': file_type})
                    self.assertEqual(response.status_code, 200)
                    result = response.get_json()
                    self.assertEqual({item['name'] for item in result['items']}, names)
                    self.assertEqual(result['total'], len(names))
            self.assertEqual(self.client.get(endpoint).get_json()['total'], 9)
            self.assertEqual(self.client.get(endpoint, query_string={'file_type': 'pictures', 'q': 'Guide'}).get_json()['total'], 0)
            self.assertEqual(self.client.get(endpoint, query_string={'file_type': 'documents', 'q': 'GUIDE'}).get_json()['items'][0]['name'], 'Guide.pdf')

    def test_file_type_filter_applies_before_pagination_and_sorting(self):
        for number in range(101):
            (self.root / f'Notes {number}.txt').touch()
        for name in ('Photo 2.png', 'Photo 10.png', 'Photo 20.png'):
            (self.root / name).touch()
        self.warm()
        for endpoint in ('/api/browse/0/', '/api/search'):
            result = self.client.get(endpoint, query_string={'file_type': 'pictures', 'page': 2}).get_json()
            self.assertEqual((result['total'], result['pages'], result['page']), (3, 1, 1))
            self.assertEqual([item['name'] for item in result['items']], ['Photo 2.png', 'Photo 10.png', 'Photo 20.png'])
            result = self.client.get(endpoint, query_string={'file_type': 'documents', 'page': 2}).get_json()
            self.assertEqual((result['total'], result['pages'], len(result['items'])), (101, 2, 1))
        result = self.client.get('/api/browse/0/', query_string={'file_type': 'pictures', 'direction': 'desc'}).get_json()
        self.assertEqual([item['name'] for item in result['items']], ['Photo 20.png', 'Photo 10.png', 'Photo 2.png'])

    def test_file_type_filter_in_favorites_with_multiple_roots(self):
        other = self.root / 'Other root'
        other.mkdir()
        (other / 'Other clip.mp4').touch()
        self.config.write_text(f'port=8061\n<alice>\n{self.root}\n{other}\n-/Private\n', encoding='utf-8')
        for root_index, relative, kind in ((0, 'Café Clip 2.mp4', 'video'), (0, 'Other root', 'folder'), (1, 'Other clip.mp4', 'video')):
            response = self.client.post('/api/favorites', json=dict(root_index=root_index, rel=relative, kind=kind, enabled=True), headers=self.csrf())
            self.assertEqual(response.status_code, 200)
        for file_type, names in (('all', {'Other root', 'Café Clip 2.mp4', 'Other clip.mp4'}),
                                 ('videos', {'Other root', 'Café Clip 2.mp4', 'Other clip.mp4'}),
                                 ('pictures', {'Other root'}), ('documents', {'Other root'})):
            with self.subTest(file_type=file_type):
                result = self.client.get('/api/favorites', query_string={'file_type': file_type}).get_json()
                self.assertEqual({item['name'] for item in result['items']}, names)
                self.assertEqual(result['total'], len(names))

    def test_invalid_file_type_is_rejected(self):
        for endpoint in ('/api/browse/0/', '/api/search', '/api/favorites'):
            for file_type in ('audio', '', "videos') OR 1=1 --"):
                with self.subTest(endpoint=endpoint, file_type=file_type):
                    self.assertEqual(self.client.get(endpoint, query_string={'file_type': file_type}).status_code, 400)

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
