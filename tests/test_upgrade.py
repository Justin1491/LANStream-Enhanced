"""Opt-in real portable upgrade test; run alone with LANSTREAM_UPGRADE_ZIP set.

Uses port 8061 and retains its fictional installation, backups, and logs under
benchmark-results/tmp for inspection. It never uses household configuration.
"""
import base64
from contextlib import closing
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import urllib.request
import zipfile

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
from directory_index import DirectoryIndex
from tests.test_installer import POWERSHELL, SCRIPT, literal


@unittest.skipUnless(os.name == 'nt' and POWERSHELL and os.environ.get('LANSTREAM_UPGRADE_ZIP'),
                     'Opt-in isolated Windows portable upgrade integration')
class PortableUpgrade(unittest.TestCase):
    def run_ps(self, body, timeout=180):
        command = "$ErrorActionPreference='Stop'; . " + literal(SCRIPT) + '; ' + body
        encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
        environment = {k: v for k, v in os.environ.items() if not k.startswith('LANSTREAM_')}
        environment.update(TMP=str(self.work), TEMP=str(self.work),
                           PSModulePath=str(Path(POWERSHELL).parent / 'Modules'))
        self.log_number += 1
        stdout = self.work / f'check-{self.log_number}.stdout.txt'
        stderr = self.work / f'check-{self.log_number}.stderr.txt'
        # A retained Windows child can inherit pipe handles after PowerShell exits.
        with stdout.open('wb') as out, stderr.open('wb') as err:
            result = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                     'RemoteSigned', '-EncodedCommand', encoded], stdout=out, stderr=err,
                                    timeout=timeout, env=environment)
        result.stdout, result.stderr = stdout.read_bytes(), stderr.read_bytes()
        return result

    def assert_result(self, result, status=0):
        self.assertEqual(result.returncode, status, (result.stdout + result.stderr).decode(errors='replace'))

    def upgrade(self):
        return ('Invoke-LanStreamInstaller -Upgrade -InstallDirectory ' + literal(self.target)
                + ' -ZipPath ' + literal(self.archive) + ' -ManifestPath ' + literal(self.manifest))

    def fingerprint(self, root, exclude_sqlite=False):
        files = {}
        for path in root.rglob('*'):
            if path.is_file() and not (exclude_sqlite and path.name.startswith('index.sqlite3')):
                with path.open('rb') as source:
                    files[str(path.relative_to(root))] = hashlib.file_digest(source, 'sha256').hexdigest()
        return files

    def assert_preserved(self, edit_live_favorite=False):
        self.assertEqual((self.target / 'config.txt').read_bytes(), self.config)
        for name, content in self.preview_files.items():
            self.assertEqual((self.target / 'cache' / name).read_bytes(), content)
        with closing(sqlite3.connect(self.target / 'cache' / 'index.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT profile,rel,kind,name FROM favorites ORDER BY profile').fetchall(),
                             self.favorites)
        # Authentication and per-profile favorites prove credentials/exclusions survived startup.
        for password, filename in self.profiles:
            cookies = http.cookiejar.CookieJar()
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies))
            with client.open('http://127.0.0.1:8061/login', urllib.parse.urlencode({'password': password}).encode(), timeout=10) as response:
                self.assertEqual(response.status, 200)
                home = response.read().decode('utf-8')
            if edit_live_favorite:
                csrf = re.search(r'<meta name="lanstream-csrf" content="([^"]+)"', home).group(1)
                for enabled in (False, True):
                    request = urllib.request.Request('http://127.0.0.1:8061/api/favorites',
                                data=json.dumps(dict(root_index=0, rel=filename, kind='video', enabled=enabled)).encode(),
                                headers={'Content-Type': 'application/json', 'X-LANStream-CSRF': csrf})
                    with client.open(request, timeout=10) as response:
                        self.assertEqual(json.load(response)['favorite'], enabled)
            with client.open('http://127.0.0.1:8061/api/favorites', timeout=10) as response:
                data = json.load(response)
            self.assertEqual([item['name'] for item in data['items']], [filename])

    def test_real_upgrade_preserves_state_and_failed_startup_rolls_back(self):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', 8061))
            except OSError:
                self.skipTest('Port 8061 is occupied; never stop its current owner')
        parent = BASE / 'benchmark-results' / 'tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.work = Path(tempfile.mkdtemp(prefix='upgrade-integration-', dir=parent))
        self.log_number = 0
        print(f'Fictional upgrade evidence retained: {self.work}', flush=True)
        self.archive = Path(os.environ['LANSTREAM_UPGRADE_ZIP']).resolve()
        self.manifest = Path(os.environ.get('LANSTREAM_UPGRADE_MANIFEST', self.archive.parent / 'release-manifest.json')).resolve()
        with zipfile.ZipFile(self.archive) as package:
            package.extractall(self.work / 'unpacked')
        self.target = self.work / 'Enhanced'
        (self.work / 'unpacked' / 'LANStream-Enhanced').rename(self.target)
        application_update = json.loads(self.manifest.read_text(encoding='utf-8-sig')).get('package_kind') == 'application-update'
        if application_update:
            # Local Windows tool stand-ins exit without encoding; only app startup is tested.
            stand_in = Path(os.environ['SystemRoot']) / 'System32' / 'where.exe'
            for tool in ('ffmpeg.exe', 'ffprobe.exe', '7z.exe', '7z.dll', 'ffmpeg-nvenc.exe'):
                shutil.copyfile(stand_in, self.target / tool)
        preserved_tools = {tool: hashlib.sha256((self.target / tool).read_bytes()).hexdigest()
                           for tool in ('ffmpeg.exe', 'ffprobe.exe', '7z.exe', '7z.dll', 'ffmpeg-nvenc.exe')
                           if (self.target / tool).exists()}
        media = self.work / 'Fictional media'
        media.mkdir()
        for filename in ('first.mp4', 'second.mp4', 'private.txt'):
            (media / filename).write_text('Fictional media only', encoding='utf-8')
        media_before = self.fingerprint(media)
        self.profiles = [('FictionalFirstPassword', 'first.mp4'), ('FictionalSecondPassword', 'second.mp4')]
        self.config = ('# Exact bytes, custom settings, two private profiles\r\nport=8061\r\n'
                       'use_master_password=false\r\nmaster_password=\r\ntranscoder=qsv\r\n'
                       'automatic_indexing=false\r\nthumbnail_workers=1\r\n'
                       'directory_cache_ttl=123\r\ndirectory_scan_timeout=19\r\n'
                       'low_bandwidth_resolution=1280x720\r\nlow_bandwidth_bitrate=3M\r\n'
                       + ''.join(f'<{password}>\r\n{media}\r\n-/private.txt\r\n' for password, _ in self.profiles)).encode('utf-8-sig')
        (self.target / 'config.txt').write_bytes(self.config)
        (self.target / 'old-only.private').write_bytes(b'Full original install must stay in backup')
        cache = self.target / 'cache'
        cache.mkdir(exist_ok=True)
        index = DirectoryIndex(cache / 'index.sqlite3', lambda suffix: 'video')
        try:
            for password, filename in self.profiles:
                index.set_favorite(hashlib.sha256(password.encode()).hexdigest(), media, filename, 'video', filename, True)
        finally:
            index.close()
        with closing(sqlite3.connect(cache / 'index.sqlite3')) as db:
            self.favorites = db.execute('SELECT profile,rel,kind,name FROM favorites ORDER BY profile').fetchall()
        self.preview_files = {'previews/fictional.jpg': b'Existing preview bytes',
                              'thumbnails/nested/legacy.bin': b'Unknown legacy cache bytes are kept',
                              '.private-sentinel': b'Hidden cache file stays'}
        for name, content in self.preview_files.items():
            path = cache / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        original_files = self.fingerprint(self.target, exclude_sqlite=True)
        # A second process named LANStream.exe must survive both upgrade and rollback.
        unrelated_exe = self.work / 'LANStream.exe'
        shutil.copyfile(Path(os.environ['SystemRoot']) / 'System32' / 'cmd.exe', unrelated_exe)
        unrelated = subprocess.Popen([str(unrelated_exe), '/d', '/q', '/k'], stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 8061))
                listener.listen(1)
                denied = self.run_ps('exit (' + self.upgrade() + ')')
                self.assert_result(denied, 1)
                self.assertIn(b'unrelated', denied.stdout + denied.stderr)
                self.assertFalse(list(self.work.glob('Enhanced.backup-*')))
            started = self.run_ps('$p=Start-LanStreamUpgradeProcess ' + literal(self.target)
                                  + ' ' + literal(self.work / 'original') + '; Wait-LanStreamUpgradeHealthy $p; Write-Output $p.Id')
            self.assert_result(started)
            original_pid = int(started.stdout.strip())
            self.assert_preserved(edit_live_favorite=True)
            self.assertGreater((cache / 'index.sqlite3-wal').stat().st_size, 0)
            upgraded = self.run_ps('exit (' + self.upgrade() + ')')
            self.assert_result(upgraded)
            self.assertIn(b'Upgrade PASS', upgraded.stdout)
            backup, = self.work.glob('Enhanced.backup-*')
            saved_files = self.fingerprint(backup, exclude_sqlite=True)
            for name, digest in original_files.items():
                self.assertEqual(saved_files[name], digest)
            self.assertFalse((self.target / 'old-only.private').exists())
            self.assert_preserved()
            for tool, digest in preserved_tools.items():
                self.assertEqual(hashlib.sha256((self.target / tool).read_bytes()).hexdigest(), digest)
            self.assertIsNone(unrelated.poll())
            backup_before = self.fingerprint(backup)
            # Fail after the real replacement binds 8061, proving owned-process stop and restart.
            fail_once = ("$realWait=${function:Wait-LanStreamUpgradeHealthy}; $script:waitCalls=0; "
                         "function Wait-LanStreamUpgradeHealthy { param([Diagnostics.Process]$Process); "
                         "& $realWait $Process; $script:waitCalls++; if ($script:waitCalls -eq 1) { throw 'Forced integration startup failure' } }; ")
            failed = self.run_ps(fail_once + 'exit (' + self.upgrade() + ')')
            self.assert_result(failed, 1)
            self.assertIn(b'original server restarted', failed.stdout + failed.stderr)
            self.assertEqual(len(list(self.work.glob('Enhanced.failed-*'))), 1)
            self.assertEqual(list(self.work.glob('Enhanced.backup-*')), [backup])
            self.assertEqual(self.fingerprint(backup), backup_before)
            self.assert_preserved()
            self.assertIsNone(unrelated.poll())
            self.assertEqual(self.fingerprint(media), media_before)
            pid_check = self.run_ps('@(Get-LanStreamUpgradeProcesses ' + literal(self.target) + ') | ForEach-Object { $_.Id }')
            self.assert_result(pid_check)
            self.assertNotEqual(int(pid_check.stdout.strip()), original_pid)
            # The same real startup probe must also accept an intentional passwordless install.
            stopped = self.run_ps('@(Get-LanStreamUpgradeProcesses ' + literal(self.target)
                                  + ') | ForEach-Object { Stop-LanStreamUpgradeProcess $_ ' + literal(self.target) + ' }')
            self.assert_result(stopped)
            (self.target / 'config.txt').write_text('port=8061\nuse_master_password=false\nmaster_password=\n'
                                                  'transcoder=qsv\nautomatic_indexing=false\n'
                                                  + str(media) + '\n', encoding='utf-8')
            passwordless = self.run_ps('$p=Start-LanStreamUpgradeProcess ' + literal(self.target)
                                       + ' ' + literal(self.work / 'passwordless') + '; Wait-LanStreamUpgradeHealthy $p')
            self.assert_result(passwordless)
            (self.work / 'result.json').write_text(json.dumps({'status': 'PASS', 'backup': str(backup),
                    'config_sha256': hashlib.sha256(self.config).hexdigest(), 'favorites': len(self.favorites),
                    'rollback': 'original restored and restarted', 'unrelated_process_preserved': True,
                    'media_unchanged': True, 'passwordless_health': True}, indent=2), encoding='utf-8')
        finally:
            try:
                stopped = self.run_ps('@(Get-LanStreamUpgradeProcesses ' + literal(self.target)
                                      + ') | ForEach-Object { Stop-LanStreamUpgradeProcess $_ ' + literal(self.target) + ' }')
                self.assert_result(stopped)
            finally:
                unrelated.terminate()
                unrelated.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
