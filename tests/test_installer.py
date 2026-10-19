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
        environment = dict(os.environ, TMP=str(self.work), TEMP=str(self.work))
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
                'master_password=\r\ntranscoder=auto\r\nthumbnail_workers=1\r\n'
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
                     self.template().replace('transcoder=auto', 'transcoder=software')):
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

    def test_template_and_media_root_cannot_be_combined(self):
        path = self.work / 'config-template.txt'
        path.write_bytes(self.template().encode('utf-8'))
        result = self.run_ps('exit (' + self.installer() + ' -ConfigTemplatePath ' + literal(path) + ')')
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'not both', (result.stdout + result.stderr).lower())
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    unittest.main()
