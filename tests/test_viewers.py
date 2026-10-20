"""Non-video viewer regressions using only isolated fictional media."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.parse import quote
import zipfile

import pymupdf
from PIL import Image

from viewer_fixtures import create_viewer_fixtures


class ViewerRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This workspace temp is accessible to both the sandbox and 7-Zip.
        temp_root = Path(__file__).resolve().parents[1] / 'benchmark-results' / 'tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='viewers-', dir=temp_root)
        cls.work = Path(cls.temp.name)
        cls.config = cls.work / 'config.txt'
        cls.config.write_text('port=8061\n', encoding='utf-8')
        cls.imported_app = 'app' not in sys.modules
        with patch.dict(os.environ, {'LANSTREAM_CONFIG_FILE': str(cls.config),
                                    'LANSTREAM_CACHE_DIR': str(cls.work / 'cache')}):
            import app
        from directory_index import DirectoryIndex
        from image_scheduler import ImageScheduler
        cls.server = app
        cls.original_index, cls.original_scheduler = app.directory_index, app.image_scheduler
        cls.index = DirectoryIndex(cls.work / 'index.sqlite3', app.kind_for_suffix)
        cls.scheduler = ImageScheduler(lambda *args: None)
        cls.globals = patch.multiple(app, CONFIG_FILE=cls.config, CACHE_DIR=cls.work / 'cache',
                                     directory_index=cls.index, image_scheduler=cls.scheduler)
        cls.globals.start()
        cls.testing = app.app.config['TESTING']
        app.app.config['TESTING'] = True

    @classmethod
    def tearDownClass(cls):
        cls.server.app.config['TESTING'] = cls.testing
        cls.globals.stop()
        cls.scheduler.close()
        cls.index.close()
        if cls.imported_app:
            cls.original_scheduler.close()
            cls.original_index.close()
        cls.temp.cleanup()

    def setUp(self):
        self.root = self.work / self.id().rsplit('.', 1)[-1]
        self.files = create_viewer_fixtures(self.root)
        self.config.write_text(f'port=8061\n<alice>\n{self.root}\n-/Private\n<bob>\n{self.root}\n', encoding='utf-8')
        self.client = self.server.app.test_client()
        self.client.post('/login', data={'password': 'alice'})

    def url(self, route, key, query=''):
        return f'/{route}/0/{quote(self.files[key], safe="/")}{query}'

    def fetch(self, route, key, query='', client=None):
        response = (client or self.client).get(self.url(route, key, query))
        response.get_data()
        response.close()
        return response

    def jpeg(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        with Image.open(io.BytesIO(response.data)) as image:
            image.load()
            self.assertEqual(image.format, 'JPEG')
            return image.size, image.getpixel((image.width // 2, image.height // 2))

    def test_images_unicode_navigation_and_tiff(self):
        for key in ('image_first', 'image_next', 'image_tiff'):
            with self.subTest(key=key):
                self.assertEqual(self.fetch('image', key).status_code, 200)
        payload = self.fetch('api/image-navigation', 'image_first').get_json()
        self.assertIsNone(payload['previous'])
        self.assertEqual(payload['next']['relpath'], self.files['image_next'])
        with self.client.get(payload['image_url']) as response:
            self.assertEqual(response.status_code, 200)
        payload = self.fetch('api/image-navigation', 'image_next').get_json()
        self.assertEqual(payload['previous']['relpath'], self.files['image_first'])
        self.assertEqual(payload['next']['relpath'], self.files['image_tiff'])
        self.assertIsNone(self.fetch('api/image-navigation', 'image_tiff').get_json()['next'])
        response = self.fetch('image-file', 'image_first')
        with Image.open(io.BytesIO(response.data)) as image:
            image.load()
            self.assertEqual(image.size, (240, 320))
        self.jpeg(self.fetch('image-file', 'image_next'))
        self.jpeg(self.fetch('image-file', 'image_tiff'))

    def test_documents_page_counts_dimensions_and_natural_order(self):
        for key in ('pdf', 'cbz', 'cbr'):
            with self.subTest(key=key):
                response = self.fetch('document', key)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'const count=3', response.data)
                for page in range(3):
                    size, color = self.jpeg(self.fetch('document-page', key, f'?page={page}'))
                    self.assertGreater(color[page], max(value for i, value in enumerate(color) if i != page))
                    self.assertEqual(size[0] > size[1], page == 1)

    def test_cbr_is_real_rar_and_uses_real_7zip(self):
        source = self.root / self.files['cbr']
        self.assertTrue(source.read_bytes().startswith(b'Rar!\x1a\x07\x00'))
        self.assertTrue(self.server.SEVEN_ZIP, 'Local CBR backend must be installed')
        result = subprocess.run([self.server.SEVEN_ZIP, 't', str(source)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(self.server._seven_zip_names(source), ['page1.png', 'page2.jpg', 'page10.png'])
        with Image.open(io.BytesIO(self.server._seven_zip_read(source, 'page2.jpg'))) as image:
            self.assertEqual(image.size, (360, 220))

    def test_page_bounds_and_invalid_page_arguments(self):
        for key in ('pdf', 'cbz', 'cbr'):
            for page, status in [('-1', 404), ('3', 404), ('bad', 400)]:
                with self.subTest(key=key, page=page):
                    self.assertEqual(self.fetch('document-page', key, '?page=' + page).status_code, status)

    def test_text_and_markdown_escape_literal_html(self):
        for key in ('text', 'markdown'):
            with self.subTest(key=key):
                response = self.fetch('text', key)
                self.assertEqual(response.status_code, 200)
                self.assertIn('Fictional Café notes', response.get_data(as_text=True))
                self.assertIn(b'&lt;script&gt;', response.data)
                self.assertIn(b'A &amp; B &lt;example&gt;', response.data)
                self.assertNotIn(b'<script>alert(', response.data)

    def test_excluded_viewers_and_direct_data_remain_private(self):
        for key, routes in [('private_image', ('image', 'image-file', 'api/image-navigation')),
                            ('private_pdf', ('document', 'document-page', 'media')),
                            ('private_cbz', ('document', 'document-page', 'media')),
                            ('private_cbr', ('document', 'document-page', 'media')),
                            ('private_text', ('text', 'media'))]:
            for route in routes:
                with self.subTest(key=key, route=route):
                    self.assertEqual(self.fetch(route, key).status_code, 404)
        bob = self.server.app.test_client()
        bob.post('/login', data={'password': 'bob'})
        self.jpeg(self.fetch('document-page', 'private_pdf', client=bob))
        self.assertEqual(self.fetch('document-page', 'private_pdf').status_code, 404)

    def test_unauthenticated_viewers_and_data_are_denied(self):
        client = self.server.app.test_client()
        for route, key in [('image', 'image_first'), ('image-file', 'image_first'),
                           ('api/image-navigation', 'image_first'), ('document', 'pdf'),
                           ('document-page', 'pdf'), ('text', 'text'), ('media', 'pdf')]:
            with self.subTest(route=route):
                self.assertIn(self.fetch(route, key, client=client).status_code, (302, 401))

    def test_tiff_cache_refreshes_after_source_replacement(self):
        before = self.fetch('image-file', 'image_tiff').data
        Image.new('RGB', (90, 110), 'yellow').save(self.root / self.files['image_tiff'])
        response = self.fetch('image-file', 'image_tiff')
        self.assertEqual(self.jpeg(response)[0], (90, 110))
        self.assertTrue(before != response.data, 'TIFF conversion still contains the original image')

    def test_document_cache_refreshes_and_removed_cached_pages_are_rejected(self):
        for key in ('pdf', 'cbz'):
            with self.subTest(key=key):
                before = self.fetch('document-page', key).data
                self.jpeg(self.fetch('document-page', key, '?page=2'))
                source = self.root / self.files[key]
                if key == 'pdf':
                    replacement = source.with_suffix('.replacement.pdf')
                    with pymupdf.open() as document:
                        page = document.new_page(width=200, height=250)
                        page.draw_rect(page.rect, color=(1, 1, 0), fill=(1, 1, 0))
                        document.save(replacement)
                    replacement.replace(source)
                else:
                    output = io.BytesIO()
                    Image.new('RGB', (200, 250), 'yellow').save(output, format='PNG')
                    with zipfile.ZipFile(source, 'w') as archive:
                        archive.writestr('page1.png', output.getvalue())
                response = self.fetch('document-page', key)
                self.jpeg(response)
                self.assertTrue(response.data != before, 'Document page still contains the original image')
                self.assertEqual(self.fetch('document-page', key, '?page=2').status_code, 404)
                self.assertIn(b'const count=1', self.fetch('document', key).data)

    def test_simultaneous_requests_render_same_pdf_page_and_tiff(self):
        for route, key in [('document-page', 'pdf'), ('image-file', 'image_tiff')]:
            with self.subTest(key=key):
                ready = threading.Barrier(4)

                def request_image(_):
                    client = self.server.app.test_client()
                    client.post('/login', data={'password': 'alice'})
                    ready.wait(timeout=10)
                    return self.fetch(route, key, client=client)

                with ThreadPoolExecutor(max_workers=4) as pool:
                    responses = list(pool.map(request_image, range(4)))
                for response in responses:
                    self.jpeg(response)
                self.assertTrue(all(response.data == responses[0].data for response in responses))
                self.assertFalse(list((self.work / 'cache').rglob('*.tmp.jpg')))

    def test_corrupt_and_empty_documents_have_readable_private_errors(self):
        for key in ('corrupt_pdf', 'corrupt_cbz', 'corrupt_cbr', 'empty_cbz'):
            for route in ('document', 'document-page'):
                with self.subTest(key=key, route=route):
                    response = self.fetch(route, key)
                    self.assertGreaterEqual(response.status_code, 400)
                    self.assertLess(response.status_code, 500)
                    self.assertIn('text/html', response.content_type)
                    self.assertNotIn(str(self.root).encode(), response.data)
        response = self.fetch('image-file', 'corrupt_image')
        self.assertEqual(response.status_code, 415)
        self.assertNotIn(str(self.root).encode(), response.data)

    def test_corrupt_comic_page_has_readable_private_error(self):
        self.assertEqual(self.fetch('document', 'corrupt_page_cbz').status_code, 200)
        response = self.fetch('document-page', 'corrupt_page_cbz')
        self.assertEqual(response.status_code, 415)
        self.assertIn(b'Unable to read this page', response.data)
        self.assertNotIn(str(self.root).encode(), response.data)

    def test_missing_cbr_backend_explains_dependency_without_private_paths(self):
        with patch.object(self.server, 'SEVEN_ZIP', None):
            for route in ('document', 'document-page'):
                response = self.fetch(route, 'cbr')
                self.assertEqual(response.status_code, 503)
                self.assertIn(b'7-Zip', response.data)
                self.assertNotIn(str(self.root).encode(), response.data)

    def test_unreadable_text_does_not_disclose_server_path(self):
        source = self.root / self.files['text']
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == source:
                raise PermissionError(str(source))
            return read_text(path, *args, **kwargs)

        with patch.object(Path, 'read_text', read):
            response = self.fetch('text', 'text')
        self.assertEqual(response.status_code, 500)
        self.assertIn(b'Unable to read', response.data)
        self.assertNotIn(str(self.root).encode(), response.data)


if __name__ == '__main__':
    unittest.main()
