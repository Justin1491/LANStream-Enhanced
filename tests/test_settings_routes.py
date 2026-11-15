"""Settings integration uses an isolated server and fictional configuration."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class SettingsRoutesTests(unittest.TestCase):
    def test_settings_activity_authorization_and_durable_toggle(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            config = work / 'config.txt'
            original = '# preserve comments\nport=8061\nautomatic_thumbnails=true\n<alice>\n' + str(work / 'media') + '\n-/Private\n'
            config.write_text(original, encoding='utf-8')
            script = r'''
from pathlib import Path
from unittest.mock import patch
import app
try:
    client = app.app.test_client()
    assert client.get('/settings').status_code == 302
    assert client.get('/api/settings').status_code == 401
    client.post('/login', data={'password': 'alice'})
    page = client.get('/settings')
    assert page.status_code == 200 and b'Thumbnails' in page.data
    assert b'activity.js' in page.data
    with client.session_transaction() as session:
        headers = {'X-LANStream-CSRF': session['csrf_token']}
    assert client.post('/api/activity').status_code == 403
    assert client.post('/api/settings', json={'action': 'enable_thumbnails', 'enabled': False}).status_code == 403
    with patch.object(app.automatic_thumbnails, 'touch') as touch:
        assert client.get('/api/settings').status_code == 200
        assert client.get('/api/prepare-thumbnails/0').status_code == 200
        assert client.get('/api/index/0').status_code == 200
        assert client.get('/api/browse/0/', headers={'X-LANStream-Poll':'1'}).status_code == 200
        assert not touch.called, 'status polling reset idle timer'
        assert client.post('/api/activity', headers=headers).status_code == 204
        assert touch.call_count == 1
        assert client.get('/settings').status_code == 200
        assert touch.call_count == 2
    for payload in ([], {'action':'bad'}, {'action':'enable_thumbnails','enabled':'false'}):
        assert client.post('/api/settings', json=payload, headers=headers).status_code == 400
    data = client.get('/api/settings').get_json()
    assert {'thumbnails','scheduler','indexing','storage','updates'} <= data.keys()
    assert str(app.CONFIG_FILE.parent) not in str(data)
    assert client.post('/api/settings', json={'action':'enable_thumbnails','enabled':False}, headers=headers).status_code == 200
    assert app.automatic_thumbnails.status()['enabled'] is False
    text = app.CONFIG_FILE.read_text(encoding='utf-8')
    assert text == ORIGINAL.replace('automatic_thumbnails=true', 'automatic_thumbnails=false')
    assert app.load_settings()['automatic_thumbnails'] == 'false'
    with patch.object(Path, 'replace', side_effect=OSError('read only')):
        assert client.post('/api/settings', json={'action':'enable_thumbnails','enabled':True}, headers=headers).status_code == 503
    assert app.automatic_thumbnails.status()['enabled'] is False
    assert app.CONFIG_FILE.read_text(encoding='utf-8') == text
finally:
    app.automatic_thumbnails.close(wait=True)
    app.storage_status.close()
    app.update_status.close()
    app.cleanup_service.close()
    app.image_scheduler.close(wait=True)
    app.directory_index.close()
'''
            result = subprocess.run(
                [os.sys.executable, '-B', '-c', 'ORIGINAL = ' + repr(original) + '\n' + script],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
                env={**os.environ, 'LANSTREAM_CONFIG_FILE': str(config), 'LANSTREAM_CACHE_DIR': str(work / 'cache')})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
