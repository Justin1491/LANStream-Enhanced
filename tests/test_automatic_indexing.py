"""Automatic discovery and refresh use only temporary, fictional libraries."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from directory_index import DirectoryIndex, root_id


class AutomaticIndexingTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(__file__).resolve().parents[1] / 'benchmark-results' / 'tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='automatic-index-', dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.root = self.work / 'library'
        (self.root / 'nested' / 'deep').mkdir(parents=True)
        (self.root / 'nested' / 'deep' / 'original.mp4').touch()
        (self.root / 'private').mkdir()
        (self.root / 'private' / 'secret.mp4').touch()
        self.profiles = [dict(id='p', roots=[self.root], excluded=[self.root / 'private'])]

    def start(self):
        index = DirectoryIndex(self.work / 'index.sqlite3',
                               lambda suffix: 'video' if suffix == '.mp4' else None,
                               ttl=.6, profiles=lambda: self.profiles)
        self.addCleanup(index.close)
        return index

    def eventually(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.025)
        self.fail('automatic indexing did not reach the expected state')

    def test_indexes_every_root_and_nested_folder_without_browsing(self):
        other = self.work / 'other'
        other.mkdir()
        (other / 'other.mp4').touch()
        self.profiles[0]['roots'].append(other)
        with patch('directory_index.os.scandir', wraps=os.scandir) as scans:
            index = self.start()
            self.eventually(lambda: index.search([self.root, other], query='.mp4')['total'] == 2)
            self.assertNotIn(self.root / 'private', [call.args[0] for call in scans.call_args_list])
        self.assertEqual(index.search([self.root], query='secret')['total'], 0)

    def test_refresh_discovers_additions_and_removes_deleted_subtrees_and_files(self):
        index = self.start()
        self.eventually(lambda: index.search([self.root], query='original')['total'] == 1)
        index.set_favorite('p', self.root, 'nested/deep/original.mp4', 'video', 'original.mp4', True)
        (self.root / 'new' / 'child').mkdir(parents=True)
        (self.root / 'new' / 'child' / 'added.mp4').touch()
        shutil.rmtree(self.root / 'nested')
        self.eventually(lambda: index.search([self.root], query='added')['total'] == 1
                        and index.search([self.root], query='original')['total'] == 0)
        (self.root / 'new' / 'child' / 'added.mp4').unlink()
        self.eventually(lambda: index.search([self.root], query='added')['total'] == 0)
        self.assertEqual(index.favorites('p', [self.root])['total'], 1)

    def test_shared_root_search_still_applies_each_profiles_exclusions(self):
        self.profiles.append(dict(id='unrestricted', roots=[self.root], excluded=[]))
        index = self.start()
        self.eventually(lambda: index.search([self.root], query='secret')['total'] == 1)
        restricted = index.search([self.root], query='secret',
                                  excluded={root_id(self.root): (self.root / 'private',)})
        self.assertEqual(restricted['total'], 0)
        self.assertEqual(index.search([self.root], query='secret')['total'], 1)

    def test_offline_refresh_keeps_snapshot_and_retries_without_a_request(self):
        index = self.start()
        self.eventually(lambda: index.search([self.root], query='original')['total'] == 1)
        original_target = index._safe_target
        offline = threading.Event()
        offline.set()

        def target(root, relative):
            if offline.is_set():
                raise OSError('fictional share offline')
            return original_target(root, relative)

        with patch.object(index, '_safe_target', side_effect=target):
            self.eventually(lambda: index.metrics()['scans_failed'] > 0)
            self.assertEqual(index.search([self.root], query='original')['total'], 1)
            (self.root / 'recovered.mp4').touch()
            offline.clear()
            self.eventually(lambda: index.search([self.root], query='recovered')['total'] == 1)

    def test_configuration_changes_replace_roots_and_exclusions(self):
        index = self.start()
        self.eventually(lambda: index.search([self.root], query='original')['total'] == 1)
        self.profiles = [dict(id='p', roots=[self.root], excluded=[])]
        self.eventually(lambda: index.search([self.root], query='secret')['total'] == 1)
        other = self.work / 'replacement'
        other.mkdir()
        (other / 'replacement.mp4').touch()
        self.profiles = [dict(id='p', roots=[other], excluded=[])]
        self.eventually(lambda: index.search([other], query='replacement')['total'] == 1)
        self.assertEqual(index.crawl_status('p', [self.root]), [])

    def test_pause_is_honored_between_refreshes_and_restart_resumes_automatically(self):
        index = self.start()
        self.eventually(lambda: index.search([self.root], query='original')['total'] == 1)
        index.crawl_pause('p', self.root)
        self.assertTrue(index.wait_for_idle())
        (self.root / 'later.mp4').touch()
        time.sleep(1.3)
        self.assertEqual(index.search([self.root], query='later')['total'], 0)
        self.assertTrue(index.crawl_status('p', [self.root])[0]['paused'])
        index.close()
        index = self.start()
        self.eventually(lambda: index.search([self.root], query='later')['total'] == 1)

    def test_blocked_share_does_not_prevent_another_library_from_indexing(self):
        other = self.work / 'healthy'
        other.mkdir()
        (other / 'healthy.mp4').touch()
        gate, started = threading.Event(), threading.Event()
        real_scandir = os.scandir

        def scandir(path):
            if path == self.root:
                started.set()
                gate.wait(10)
            return real_scandir(path)

        with patch.object(DirectoryIndex, '_host', side_effect=lambda root: root.name), \
                patch('directory_index.os.scandir', side_effect=scandir):
            index = self.start()
            try:
                self.assertTrue(started.wait(2))
                self.profiles = [dict(id='p', roots=[self.root, other], excluded=[])]
                self.eventually(lambda: index.search([other], query='healthy')['total'] == 1)
                self.assertFalse(gate.is_set())
            finally:
                gate.set()
                index.wait_for_idle()

    def run_server(self, code, settings=''):
        config = self.work / 'config.txt'
        config.write_text(f'port=8061\n{settings}<fictional>\n{self.root}\n-/private\n', encoding='utf-8')
        result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                                env={**os.environ, 'LANSTREAM_CONFIG_FILE': str(config),
                                     'LANSTREAM_CACHE_DIR': str(self.work / 'server-cache')},
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_disabling_automatic_indexing_keeps_browsing_and_manual_indexing(self):
        self.run_server('''
import time
import app
try:
    assert app.SETTINGS['automatic_indexing'] == 'false'
    root = app.load_profiles()[0]['roots'][0]
    time.sleep(.3)
    assert app.directory_index.metrics()['scans_started'] == 0
    assert app.directory_index.crawl_status(app.load_profiles()[0]['id'], [root]) == []
    client = app.app.test_client()
    client.post('/login', data={'password': 'fictional'})
    page = client.get('/browse/0/').get_data(as_text=True)
    assert 'data-automatic-indexing="false"' in page
    assert 'Automatic indexing is disabled.' in page
    client.get('/api/browse/0/')
    assert app.directory_index.wait_for_idle()
    assert client.get('/api/browse/0/').get_json()['state'] == 'ready'
    client.get('/')
    with client.session_transaction() as session:
        headers = {'X-LANStream-CSRF': session['csrf_token']}
    response = client.post('/api/index/0?relative=nested', json={'action': 'start'}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()['crawls'][0]['relative'] == '', 'manual library indexing was narrowed to a subfolder'
    deadline = time.monotonic() + 8
    while app.directory_index.search([root], query='original')['total'] != 1:
        assert time.monotonic() < deadline, 'manual indexing did not reach nested media'
        time.sleep(.025)
    assert app.directory_index.search([root], query='secret')['total'] == 0
finally:
    app.directory_index.close()
    app.image_scheduler.close()
''', settings='automatic_indexing=false\n')

    def test_server_starts_profile_indexing_without_login_or_browse(self):
        code = '''
import time
import app
try:
    deadline = time.monotonic() + 8
    root = app.load_profiles()[0]['roots'][0]
    while time.monotonic() < deadline:
        if app.directory_index.search([root], query='original')['total'] == 1:
            break
        time.sleep(.025)
    else:
        raise AssertionError('startup did not index nested media')
    assert app.directory_index.search([root], query='secret')['total'] == 0
    client = app.app.test_client()
    client.post('/login', data={'password': 'fictional'})
    page = client.get('/browse/0/').get_data(as_text=True)
    assert 'data-automatic-indexing="true"' in page
    client.get('/')
    with client.session_transaction() as session:
        headers = {'X-LANStream-CSRF': session['csrf_token']}
    response = client.post('/api/index/0?relative=nested', json={'action': 'start'}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()['crawls'][0]['relative'] == '', 'resume narrowed automatic indexing to a subfolder'
finally:
    app.directory_index.close()
    app.image_scheduler.close()
'''
        self.run_server(code)


if __name__ == '__main__':
    unittest.main()
