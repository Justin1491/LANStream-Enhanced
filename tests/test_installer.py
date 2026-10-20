"""Exercise installer guardrails in Windows PowerShell without production writes."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import zipfile


BASE = Path(__file__).resolve().parents[1]
SCRIPT = BASE / 'scripts' / 'Install-LANStreamEnhanced.ps1'
POWERSHELL = shutil.which('powershell.exe')


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == 'nt' and POWERSHELL, 'Windows PowerShell required')
class InstallerSafety(unittest.TestCase):
    def setUp(self):
        temp = BASE / 'benchmark-results' / 'tmp'
        temp.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='installer-', dir=temp)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.media = self.work / 'Fictional media'
        self.media.mkdir()
        (self.media / 'Notes.txt').write_text('Fictional media only', encoding='utf-8')
        self.target = self.work / 'Enhanced'

    def run_ps(self, body):
        self.assertTrue(SCRIPT.is_file(), 'The installer script must exist')
        command = "$ErrorActionPreference='Stop'; . " + literal(SCRIPT) + '; ' + body
        encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
        environment = {key: value for key, value in os.environ.items() if not key.startswith('LANSTREAM_')}
        environment.update(TMP=str(self.work), TEMP=str(self.work))
        # Keep a PowerShell 7 parent from supplying incompatible core modules to 5.1.
        environment['PSModulePath'] = str(Path(POWERSHELL).parent / 'Modules')
        return subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned', '-EncodedCommand', encoded],
                              capture_output=True, timeout=35, env=environment)

    def assert_rejected(self, body, message):
        result = self.run_ps("try { " + body + "; throw 'GUARD DID NOT REJECT' } catch { if ($_.Exception.Message -eq 'GUARD DID NOT REJECT') { throw }; Write-Output $_.Exception.Message }")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(message.lower(), result.stdout.decode(errors='replace').lower())
        self.assertFalse(self.target.exists())

    def paths(self, target=None, media=None):
        return ('Assert-LanStreamInstallPaths -InstallDirectory ' + literal(target or self.target)
                + ' -MediaRoot @(' + literal(media or self.media) + ')')

    def test_production_directory_and_descendants_are_rejected(self):
        for target in (Path('C:/LANStream'), Path('C:/LANStream/Enhanced')):
            with self.subTest(target=target):
                self.assert_rejected(self.paths(target), 'protected')

    def test_existing_target_is_preserved(self):
        self.target.mkdir()
        marker = self.target / 'config.txt'
        marker.write_bytes(b'Existing config must survive')
        result = self.run_ps("try { " + self.paths() + "; throw 'OVERWROTE TARGET' } catch { Write-Output $_.Exception.Message }")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'already exists', result.stdout)
        self.assertEqual(marker.read_bytes(), b'Existing config must survive')
        self.assertEqual(list(self.target.iterdir()), [marker])

    def test_drive_roots_relative_paths_and_config_injection_are_rejected(self):
        for target in ('C:\\', 'relative/install', str(self.target) + '\nport=8060',
                       str(self.target) + '\u2028Other'):
            with self.subTest(target=target):
                result = self.run_ps("try { " + self.paths(target) + "; exit 3 } catch { exit 0 }")
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertFalse(self.target.exists())

    def test_media_cannot_include_install_or_production(self):
        self.assert_rejected(self.paths(media=self.work), 'overlap')
        self.assert_rejected(self.paths(media=Path('C:/')), 'overlap')

    def test_valid_sibling_directories_are_accepted_without_writes(self):
        result = self.run_ps(self.paths() + ' | ConvertTo-Json -Compress')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        data = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertEqual(Path(data['InstallDirectory']), self.target)
        self.assertFalse(self.target.exists())

    def test_junction_ancestor_is_rejected(self):
        alias = self.work / 'alias'
        link = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(self.media)], capture_output=True)
        if link.returncode:
            self.skipTest('junction creation unavailable')
        try:
            self.assert_rejected(self.paths(target=alias / 'Enhanced'), 'reparse')
            self.assertEqual([path.name for path in self.media.iterdir()], ['Notes.txt'])
        finally:
            alias.rmdir()

    def test_unsafe_zip_paths_are_rejected_before_extraction(self):
        for entry in ('LANStream-Enhanced/../escape.txt', '../escape.txt',
                      'LANStream-Enhanced/C:/escape.txt', 'LANStream-Enhanced/CON.txt',
                      'LANStream-Enhanced/escape. /note.txt', 'Other/LANStream.exe'):
            with self.subTest(entry=entry):
                archive = self.work / 'unsafe.zip'
                with zipfile.ZipFile(archive, 'w') as package:
                    package.writestr(entry, b'Untrusted archive entry')
                self.assert_rejected("Add-Type -AssemblyName System.IO.Compression.FileSystem; $z=[IO.Compression.ZipFile]::OpenRead(" + literal(archive) + "); try { Assert-LanStreamZipEntries -Archive $z } finally { $z.Dispose() }", 'unsafe')
        self.assertFalse((self.work / 'escape.txt').exists())

    def test_symlink_and_case_collision_zip_entries_are_rejected(self):
        for symbolic in (True, False):
            archive = self.work / 'unsafe.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                if symbolic:
                    entry = zipfile.ZipInfo('LANStream-Enhanced/link')
                    entry.create_system = 3
                    entry.external_attr = 0o120777 << 16
                    package.writestr(entry, 'C:/LANStream')
                else:
                    package.writestr('LANStream-Enhanced/config.txt', 'first')
                    package.writestr('LANStream-Enhanced/CONFIG.txt', 'second')
            expected = 'unsafe' if symbolic else 'duplicate'
            self.assert_rejected("Add-Type -AssemblyName System.IO.Compression.FileSystem; $z=[IO.Compression.ZipFile]::OpenRead(" + literal(archive) + "); try { Assert-LanStreamZipEntries -Archive $z } finally { $z.Dispose() }", expected)

    def test_percentile_uses_nearest_rank(self):
        result = self.run_ps('Get-LanStreamPercentile95 -Values @(1..20)')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(float(result.stdout.strip()), 19)

    def test_listing_accepts_large_library_across_bounded_pages(self):
        for count in (100, 1, 0):
            listing = dict(items=[dict(kind='folder')] * count, total=201, pages=3,
                           state='ready', refreshing=False, error=None)
            result = self.run_ps('Assert-LanStreamListing (ConvertFrom-Json ' + literal(json.dumps(listing)) + ')')
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_listing_errors_distinguish_scan_failure_from_page_size(self):
        cases = [
            (dict(items=[], state='offline', error='Directory unavailable. Try refreshing later.'), b'offline', False),
            (dict(items=[], state='error', error='Directory scan timed out. Try refreshing later.'), b'timed out', False),
            (dict(state='ready', error=None), b'items', False),
            (dict(items=[{}] * 101, state='ready', error=None), b'101', True),
        ]
        for listing, expected, page_error in cases:
            with self.subTest(expected=expected):
                result = self.run_ps('try { Assert-LanStreamListing (ConvertFrom-Json ' + literal(json.dumps(listing))
                                     + "); throw 'UNEXPECTED SUCCESS' } catch { Write-Output $_.Exception.Message }")
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                self.assertIn(expected, result.stdout)
                if not page_error:
                    self.assertNotIn(b'100', result.stdout)

    def test_listing_allows_pending_only_during_initial_scan(self):
        listing = literal(json.dumps(dict(items=[], state='loading', error=None, refreshing=True)))
        allowed = self.run_ps('Assert-LanStreamListing (ConvertFrom-Json ' + listing + ') -AllowPending')
        self.assertEqual(allowed.returncode, 0, allowed.stderr.decode(errors='replace'))
        rejected = self.run_ps('try { Assert-LanStreamListing (ConvertFrom-Json ' + listing
                               + "); throw 'UNEXPECTED SUCCESS' } catch { Write-Output $_.Exception.Message }")
        self.assertIn(b'still loading', rejected.stdout)

    def installer(self, digest=None, archive_name='release.zip', password='FictionalTestPassword'):
        archive = self.work / 'release.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('LANStream-Enhanced/config.txt', 'port=8061')
            for name in ('LANStream.exe', 'ffmpeg.exe', 'ffprobe.exe', '7z.exe', '7z.dll', '_internal/fixture'):
                package.writestr('LANStream-Enhanced/' + name, b'Fictional package component')
        manifest = self.work / 'release-manifest.json'
        manifest.write_text(json.dumps(dict(archive=archive_name,
                            sha256=digest or hashlib.sha256(archive.read_bytes()).hexdigest(),
                            revision='fictional-installer-test')), encoding='utf-8')
        return ('Invoke-LanStreamInstaller -ZipPath ' + literal(archive)
                + ' -ManifestPath ' + literal(manifest) + ' -InstallDirectory ' + literal(self.target)
                + ' -MediaRoot @(' + literal(self.media) + ') -Password (ConvertTo-SecureString '
                + literal(password) + ' -AsPlainText -Force)')

    def test_password_line_separators_cannot_create_a_broken_profile(self):
        for separator in ('\x85', '\u2028', '\u2029'):
            with self.subTest(separator=repr(separator)):
                result = self.run_ps('exit (' + self.installer(password='Fictional' + separator + 'password') + ')')
                self.assertEqual(result.returncode, 1)
                self.assertIn(b'Password must', result.stdout + result.stderr)
                self.assertFalse(self.target.exists())

    def test_hash_mismatch_cannot_install(self):
        result = self.run_ps('exit (' + self.installer(digest='0' * 64) + ')')
        self.assertEqual(result.returncode, 1, result.stdout.decode(errors='replace'))
        self.assertIn(b'SHA256', result.stdout + result.stderr)
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.work.glob('.LANStream-Enhanced-install-*')))

    def test_manifest_cannot_identify_another_zip(self):
        result = self.run_ps('exit (' + self.installer(archive_name='another.zip') + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'manifest', (result.stdout + result.stderr).lower())
        self.assertFalse(self.target.exists())

    def test_occupied_8061_is_preserved(self):
        with socket.socket() as listener:
            try:
                listener.bind(('127.0.0.1', 8061))
            except OSError:
                self.skipTest('Port 8061 already belongs to another local test')
            listener.listen(1)
            result = self.run_ps('exit (' + self.installer() + ')')
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'already in use', result.stdout + result.stderr)
            self.assertFalse(self.target.exists())
            self.assertEqual(listener.getsockname()[1], 8061)

    def test_tool_timeout_is_reported_and_owned_child_is_stopped(self):
        prefix = self.work / 'tool'
        body = ('Invoke-LanStreamTool -FilePath ' + literal(POWERSHELL)
                + " -Arguments @('-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 20')"
                + ' -LogPrefix ' + literal(prefix) + ' -TimeoutSeconds 1 | ConvertTo-Json -Compress')
        result = self.run_ps(body)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        data = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertTrue(data['TimedOut'])
        self.assertLess(data['Seconds'], 10)
        self.assertTrue(prefix.with_suffix('.stdout.txt').is_file())

    def template(self):
        return ('# Fictional settings\r\nport=8061\r\nuse_master_password=false\r\n'
                'master_password=\r\ntranscoder=auto\r\nautomatic_indexing=false\r\nthumbnail_workers=1\r\n'
                'low_bandwidth_resolution=1280x720\r\nlow_bandwidth_bitrate=3M\r\n'
                '<__LANSTREAM_SETUP_PASSWORD__>\r\n' + str(self.media) + '\r\n-/Private\r\n')

    def test_template_preserves_mapping_settings_and_exclusions(self):
        path = self.work / 'config-template.txt'
        text = self.template()
        path.write_bytes(text.encode('utf-8'))
        result = self.run_ps('Read-LanStreamConfigTemplate -Path ' + literal(path) + ' | ConvertTo-Json -Compress')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        data = json.loads(result.stdout.decode('utf-8-sig'))
        self.assertEqual(data['Text'], text)
        self.assertEqual(data['MediaRoot'], [str(self.media)])
        self.assertEqual(data['Text'].count('__LANSTREAM_SETUP_PASSWORD__'), 1)

    def test_template_rejects_wrong_port_extra_profiles_and_settings_injection(self):
        for text in (self.template().replace('port=8061', 'port=8060'),
                     self.template() + '<another-password>\r\nC:\\Other\r\n',
                     self.template().replace('port=8061', 'port=8061\r\nport=8061'),
                     self.template().replace('<__LANSTREAM_SETUP_PASSWORD__>', str(self.media) + '\r\n<__LANSTREAM_SETUP_PASSWORD__>'),
                     self.template() + 'port=8060\r\n',
                     self.template().replace('transcoder=auto', 'transcoder=software'),
                     self.template().replace('automatic_indexing=false', 'automatic_indexing=maybe')):
            with self.subTest(text=text[:70]):
                path = self.work / 'invalid-config.txt'
                path.write_bytes(text.encode('utf-8'))
                self.assert_rejected('Read-LanStreamConfigTemplate -Path ' + literal(path), 'template')

    def test_template_removes_utf8_bom_without_changing_settings(self):
        path = self.work / 'bom-config.txt'
        text = self.template()
        path.write_bytes(text.encode('utf-8-sig'))
        result = self.run_ps('(Read-LanStreamConfigTemplate -Path ' + literal(path) + ').Text | ConvertTo-Json -Compress')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(json.loads(result.stdout.decode('utf-8-sig')), text)

    def existing_upgrade(self):
        self.target.mkdir()
        for name in ('LANStream.exe', 'ffmpeg.exe', 'ffprobe.exe', '7z.exe', '7z.dll'):
            (self.target / name).write_bytes(b'Existing fictional component')
        (self.target / '_internal').mkdir()
        (self.target / '_internal' / 'runtime').write_bytes(b'Existing runtime')
        (self.target / 'cache').mkdir()
        (self.target / 'cache' / 'index.sqlite3').write_bytes(b'SQLite format 3\x00' + b'Fictional DB')
        (self.target / 'config.txt').write_bytes(self.template().replace('__LANSTREAM_SETUP_PASSWORD__', 'ExistingPassword').encode('utf-8'))
        return {str(p.relative_to(self.target)): p.read_bytes() for p in self.target.rglob('*') if p.is_file()}

    def assert_upgrade_unchanged(self, before):
        self.assertEqual(before, {str(p.relative_to(self.target)): p.read_bytes()
                                  for p in self.target.rglob('*') if p.is_file()})
        self.assertFalse(list(self.work.glob('Enhanced.backup-*')))

    def upgrade(self, **kwargs):
        return self.installer(**kwargs).split(' -MediaRoot @(', 1)[0] + ' -Upgrade'

    def test_upgrade_requires_existing_enhanced_markers(self):
        result = self.run_ps('exit (' + self.upgrade() + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'existing Enhanced', result.stdout + result.stderr)
        before = self.existing_upgrade()
        (self.target / '7z.dll').unlink()
        before.pop('7z.dll')
        result = self.run_ps('exit (' + self.upgrade() + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'marker', result.stdout + result.stderr)
        self.assert_upgrade_unchanged(before)

    def test_upgrade_rejects_inputs_that_replace_saved_settings(self):
        before = self.existing_upgrade()
        for option in (" -MediaRoot @()", " -ConfigTemplatePath ''", " -Password $null", " -SampleVideo ''"):
            with self.subTest(option=option):
                result = self.run_ps('exit (' + self.upgrade() + option + ')')
                self.assertEqual(result.returncode, 1)
                self.assertIn(b'cannot be supplied with -Upgrade', result.stdout + result.stderr)
                self.assert_upgrade_unchanged(before)

    def test_upgrade_hash_and_unsafe_zip_fail_before_original_changes(self):
        before = self.existing_upgrade()
        result = self.run_ps('exit (' + self.upgrade(digest='0' * 64) + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'SHA256', result.stdout + result.stderr)
        self.assert_upgrade_unchanged(before)
        command = self.upgrade()
        archive = self.work / 'release.zip'
        with zipfile.ZipFile(archive, 'a') as package:
            package.writestr('LANStream-Enhanced/../escape.txt', 'unsafe')
        manifest = self.work / 'release-manifest.json'
        data = json.loads(manifest.read_text())
        data['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(data))
        result = self.run_ps('exit (' + command + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'unsafe', result.stdout + result.stderr)
        self.assert_upgrade_unchanged(before)
        self.assertFalse((self.work / 'escape.txt').exists())

    def test_upgrade_wrong_port_and_external_cache_are_rejected(self):
        before = self.existing_upgrade()
        config = self.target / 'config.txt'
        config.write_bytes(config.read_bytes().replace(b'port=8061', b'port=8060'))
        before['config.txt'] = config.read_bytes()
        result = self.run_ps('exit (' + self.upgrade() + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'8061', result.stdout + result.stderr)
        self.assert_upgrade_unchanged(before)
        config.write_bytes(config.read_bytes().replace(b'port=8060', b'port=8061'))
        before['config.txt'] = config.read_bytes()
        result = self.run_ps('$env:LANSTREAM_CACHE_DIR=' + literal(self.media) + '; exit (' + self.upgrade() + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'external', result.stdout + result.stderr)
        self.assert_upgrade_unchanged(before)

    def test_upgrade_rejects_protected_target_and_descendant_junction(self):
        self.assert_rejected('Assert-LanStreamUpgradePaths -InstallDirectory C:\\LANStream', 'protected')
        before = self.existing_upgrade()
        alias = self.target / 'cache' / 'alias'
        link = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(self.media)], capture_output=True)
        if link.returncode:
            self.skipTest('junction creation unavailable')
        try:
            result = self.run_ps('exit (' + self.upgrade() + ')')
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'reparse', result.stdout + result.stderr)
            self.assertEqual((self.media / 'Notes.txt').read_text(), 'Fictional media only')
        finally:
            alias.rmdir()
        self.assert_upgrade_unchanged(before)

    def test_upgrade_cache_copy_preserves_all_sqlite_companions_and_previews(self):
        self.existing_upgrade()
        cache = self.target / 'cache'
        for name in ('index.sqlite3-wal', 'index.sqlite3-shm', 'index.sqlite3-journal', 'preview.jpg'):
            (cache / name).write_bytes(b'Fictional preserved ' + name.encode())
        (cache / 'nested').mkdir()
        (cache / 'nested' / 'old-format.bin').write_bytes(b'Unknown cache format is retained')
        destination = self.work / 'copied-cache'
        result = self.run_ps('Copy-LanStreamUpgradeCache ' + literal(cache) + ' ' + literal(destination))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        expected = {str(p.relative_to(cache)): p.read_bytes() for p in cache.rglob('*') if p.is_file()}
        actual = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file()}
        self.assertEqual(actual, expected)

    def test_upgrade_never_stops_process_at_another_executable_path(self):
        result = self.run_ps("$p=Start-Process -FilePath " + literal(POWERSHELL)
                             + " -ArgumentList '-NoProfile -NonInteractive -Command Start-Sleep -Seconds 20' -WindowStyle Hidden -PassThru; "
                             + "try { try { Stop-LanStreamUpgradeProcess $p " + literal(self.target)
                             + "; throw 'WRONG PROCESS STOPPED' } catch { if ($_.Exception.Message -eq 'WRONG PROCESS STOPPED') { throw }; "
                             + "$p.Refresh(); if ($p.HasExited) { throw 'WRONG PROCESS EXITED' }; Write-Output $_.Exception.Message } } "
                             + "finally { $p.Refresh(); if (-not $p.HasExited) { $p.Kill(); $p.WaitForExit(5000) | Out-Null } }")
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'exact verified', result.stdout)

    def test_upgrade_refuses_conflicting_runtime_environment_before_changes(self):
        before = self.existing_upgrade()
        for key, value in (('LANSTREAM_PASSWORD', 'DifferentPassword'),
                           ('LANSTREAM_USE_MASTER_PASSWORD', 'true'),
                           ('LANSTREAM_TRANSCODER', 'software'), ('LANSTREAM_PORT', '8062')):
            with self.subTest(key=key):
                result = self.run_ps('$env:' + key + '=' + literal(value) + '; exit (' + self.upgrade() + ')')
                self.assertEqual(result.returncode, 1)
                self.assertIn(b'environment overrides', result.stdout + result.stderr)
                self.assert_upgrade_unchanged(before)

    def test_upgrade_does_not_contact_offline_unc_media(self):
        self.existing_upgrade()
        config = self.target / 'config.txt'
        config.write_bytes(config.read_bytes().replace(str(self.media).encode(), b'\\\\unreachable-fictional-server\\offline-share'))
        result = self.run_ps("$realCheck=${function:Assert-LanStreamNoReparsePoint}; function Assert-LanStreamNoReparsePoint { param([string]$Path); "
                             + "if ($Path.StartsWith('\\\\')) { throw 'CONTACTED OFFLINE SHARE' }; & $realCheck $Path }; "
                             + 'Assert-LanStreamUpgradePaths ' + literal(self.target))
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_upgrade_health_accepts_login_and_passwordless_home(self):
        for redirect in (False, True):
            with self.subTest(passwordless=redirect):
                result = self.run_ps('$script:paths=@(); $script:passwordless=$' + str(redirect).lower()
                                     + "; function Invoke-LanStreamHttp { param($Process,$Path,$Cookies,$TimeoutSeconds); "
                                     + "$script:paths += $Path; if ($script:passwordless -and $Path -eq '/login') { "
                                     + "return [pscustomobject]@{Status=302;Body='redirect'} }; return [pscustomobject]@{Status=200;Body='healthy'} }; "
                                     + 'Wait-LanStreamUpgradeHealthy ([Diagnostics.Process]::GetCurrentProcess()); $script:paths | ConvertTo-Json -Compress')
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                expected = ['/login', '/'] if redirect else '/login'
                self.assertEqual(json.loads(result.stdout.decode('utf-8-sig')), expected)

    def test_upgrade_refuses_another_operation_on_same_physical_target(self):
        before = self.existing_upgrade()
        signal = self.work / 'mutex-ready'
        command = ("$ErrorActionPreference='Stop'; . " + literal(SCRIPT)
                   + '; $m=New-Object Threading.Mutex($false,(Get-LanStreamUpgradeMutexName '
                   + literal(self.target) + ')); try { $null=$m.WaitOne(); [IO.File]::WriteAllText('
                   + literal(signal) + ",'ready'); Start-Sleep -Seconds 20 } finally { $m.ReleaseMutex(); $m.Dispose() }")
        encoded = base64.b64encode(command.encode('utf-16le')).decode('ascii')
        holder = subprocess.Popen([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'RemoteSigned', '-EncodedCommand', encoded],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=dict(os.environ, TMP=str(self.work), TEMP=str(self.work)))
        try:
            deadline = time.monotonic() + 10
            while not signal.exists() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(.1)
            self.assertTrue(signal.exists(), 'Mutex holder did not become ready')
            result = self.run_ps('exit (' + self.upgrade() + ')')
            self.assertEqual(result.returncode, 1)
            self.assertIn(b'Another upgrade', result.stdout + result.stderr)
            self.assert_upgrade_unchanged(before)
        finally:
            holder.terminate()
            holder.communicate(timeout=10)

    def test_template_and_media_root_cannot_be_combined(self):
        path = self.work / 'config-template.txt'
        path.write_bytes(self.template().encode('utf-8'))
        result = self.run_ps('exit (' + self.installer() + ' -ConfigTemplatePath ' + literal(path) + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'not both', (result.stdout + result.stderr).lower())
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
