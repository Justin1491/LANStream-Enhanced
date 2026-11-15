import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from system_status import StorageStatus, UpdateStatus


class SystemStatusTests(unittest.TestCase):
    def test_storage_counts_cache_once_and_only_runtime_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'cache').mkdir()
            (root / 'cache' / 'index.sqlite3').write_bytes(b'123')
            (root / 'cache' / 'index.sqlite3-wal').write_bytes(b'12')
            (root / 'static').mkdir()
            (root / 'static' / 'site.js').write_bytes(b'1234')
            (root / 'artifacts').mkdir()
            (root / 'artifacts' / 'large').write_bytes(b'x' * 100)
            result = StorageStatus(root, root, root / 'cache')._scan()
            self.assertEqual(result['cache']['index'], 5)
            self.assertEqual(sum(result['system'].values()), 4)
            self.assertFalse(result['partial'])

    def test_packaged_runtime_deduplicates_resources_and_nested_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            resource = root / '_internal'
            (resource / 'static').mkdir(parents=True)
            (resource / 'static' / 'site.js').write_bytes(b'1234')
            (resource / 'dependency.dll').write_bytes(b'12345')
            cache = resource / 'cache'
            cache.mkdir()
            (cache / 'index.sqlite3').write_bytes(b'123')
            (root / 'LANStream.exe').write_bytes(b'12')
            (root / '7z.exe').write_bytes(b'123456')
            (root / '7z.dll').write_bytes(b'1')
            (root / 'server.log').write_bytes(b'1234567')
            (root / 'acceptance-logs').mkdir()
            (root / 'acceptance-logs' / 'report.txt').write_bytes(b'123')
            result = StorageStatus(root, resource, cache)._scan()
            self.assertEqual(result['system']['Runtime'], 11)
            self.assertEqual(result['system']['Resources'], 0)
            self.assertEqual(result['system']['Tools'], 7)
            self.assertEqual(result['system']['Configuration and logs'], 10)
            self.assertEqual(result['cache']['index'], 3)

    def test_symlinked_cache_child_is_not_followed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / 'cache'
            cache.mkdir()
            external = root / 'media'
            external.mkdir()
            (external / 'movie').write_bytes(b'x' * 100)
            try:
                (cache / 'linked').symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest('Creating symlinks is unavailable')
            result = StorageStatus(root, root, cache)._scan()
            self.assertEqual(result['cache']['linked'], 0)
            self.assertEqual(sum(result['system'].values()), 0)

    def test_identity_and_mocked_update_check(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'BUILD-IDENTITY.json').write_text(json.dumps({'source_commit': 'a' * 40}))
            update = UpdateStatus(root)
            self.assertEqual(update.status()['current'], 'a' * 40)
            class Response:
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def read(self, limit): return json.dumps({'sha': 'b' * 40}).encode()
            with patch('system_status.urlopen', return_value=Response()):
                self.assertEqual(update._check()['latest'], 'b' * 40)
            with patch('system_status.urlopen', side_effect=OSError):
                self.assertIsNotNone(update._check()['error'])

    def test_missing_identity_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(UpdateStatus(folder).status()['current'])


if __name__ == '__main__':
    unittest.main()
