"""Small synthetic bundles exercise packaging and the actual Windows launcher."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('n100_bundle', BASE / 'scripts' / 'build_n100_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class N100Bundle(unittest.TestCase):
    def setUp(self):
        parent = BASE / 'benchmark-results' / 'tmp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='bundle-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.installer = self.work / bundle.INSTALLER
        self.installer.write_text("param([string]$ZipPath,[string]$ManifestPath,[string]$ConfigTemplatePath,[switch]$Upgrade)\n"
                                  "if ((Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json).package_kind -eq 'application-update' -and (-not (Test-Path -LiteralPath $ZipPath))) { exit 7 }\n"
                                  "if ($Upgrade) { if ($ConfigTemplatePath) { exit 9 }; [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'upgrade-ok.txt'),'upgrade'); exit 0 }\n"
                                  "if (-not (Test-Path -LiteralPath $ConfigTemplatePath)) { exit 8 }\n"
                                  "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'launcher-ok.txt'),[IO.File]::ReadAllText($ConfigTemplatePath))\nexit 0\n", encoding='ascii')
        self.application = self.work / 'LANStream-Enhanced-Windows-x64-test.zip'
        with zipfile.ZipFile(self.application, 'w') as archive:
            archive.writestr('LANStream-Enhanced/config.txt', 'port=8061')
        self.manifest = self.work / 'release-manifest.json'
        self.manifest.write_text(json.dumps(dict(archive=self.application.name, sha256=bundle.sha256(self.application), revision='fictional-bundle-test')), encoding='utf-8')
        self.roots = [r'\\FICTIONAL-NAS\Movies', r'\\FICTIONAL-NAS\Pictures']

    def build(self):
        return bundle.build_bundle(self.work, self.roots, self.installer)

    def test_bundle_contains_matching_payload_and_private_config(self):
        original_example = (BASE / 'config.txt').read_bytes()
        output = self.build()
        prefix = bundle.FOLDER + '/'
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            for entry in archive.infolist():
                self.assertEqual(entry.create_system, 0, 'Use Windows attributes, not Unix ownership permissions')
                self.assertEqual(entry.external_attr, 0x20, 'Files must extract writable without read-only/system flags')
                self.assertEqual(entry.extra, b'', 'Do not carry filesystem-specific permission metadata')
                self.assertFalse(entry.flag_bits & 1, 'Setup files must not be encrypted')
            self.assertEqual(set(archive.namelist()), {prefix + name for name in
                [self.application.name, bundle.INSTALLER, 'Install Enhanced.cmd', 'Upgrade Enhanced.cmd', 'config.txt', 'release-manifest.json', 'README-FIRST.txt']})
            self.assertEqual(archive.read(prefix + self.application.name), self.application.read_bytes())
            config = archive.read(prefix + 'config.txt').decode('utf-8')
            self.assertIn('port=8061\r\n', config)
            self.assertIn('automatic_indexing=true\r\n', config)
            self.assertIn('<__LANSTREAM_SETUP_PASSWORD__>', config)
            for root in self.roots:
                self.assertIn(root, config)
            metadata = json.loads(archive.read(prefix + 'release-manifest.json'))
            self.assertEqual(metadata['installer']['sha256'], hashlib.sha256(archive.read(prefix + bundle.INSTALLER)).hexdigest())
            self.assertEqual((self.work / 'config.n100.txt').read_bytes(), archive.read(prefix + 'config.txt'))
        self.assertEqual((BASE / 'config.txt').read_bytes(), original_example)
        self.assertTrue(output.with_suffix('.zip.sha256').read_text().startswith(bundle.sha256(output)))

    def test_bad_application_hash_cannot_publish_bundle_or_config(self):
        self.application.write_bytes(b'Corrupt test payload')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.build()
        self.assertFalse((self.work / (bundle.FOLDER + '.zip')).exists())
        self.assertFalse((self.work / 'config.n100.txt').exists())

    def app_update_bundle(self):
        self.application = self.work / 'LANStream-Enhanced-Windows-x64-Update.zip'
        with zipfile.ZipFile(self.application, 'w') as archive:
            archive.writestr('LANStream-Enhanced/LANStream.exe', b'Fictional updated app')
            archive.writestr('LANStream-Enhanced/_internal/runtime', b'Fictional runtime')
        self.manifest.write_text(json.dumps(dict(archive=self.application.name,
            sha256=bundle.sha256(self.application), revision='fictional-update',
            package_kind='application-update')), encoding='utf-8')
        return bundle.build_bundle(self.work, [], self.installer, upgrade_only=True)

    def test_app_update_bundle_needs_no_private_config_or_first_install_launcher(self):
        output = self.app_update_bundle()
        with zipfile.ZipFile(output) as archive:
            prefix = 'LANStream-Enhanced-Upgrade/'
            self.assertEqual(set(archive.namelist()), {prefix + name for name in (
                self.application.name, bundle.INSTALLER, 'Upgrade Enhanced.cmd',
                'release-manifest.json', 'README-FIRST.txt')})
            self.assertEqual(archive.read(prefix + self.application.name), self.application.read_bytes())
            manifest = json.loads(archive.read(prefix + 'release-manifest.json'))
            self.assertEqual(manifest['installer']['sha256'],
                hashlib.sha256(archive.read(prefix + bundle.INSTALLER)).hexdigest())
        self.assertFalse((self.work / 'config.n100.txt').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows CMD launcher')
    def test_app_update_launcher_passes_the_verified_archive_without_a_config(self):
        with zipfile.ZipFile(self.app_update_bundle()) as archive:
            archive.extractall(self.work / 'Update & Café')
        folder = self.work / 'Update & Café' / 'LANStream-Enhanced-Upgrade'
        result = subprocess.run(['cmd', '/d', '/c', 'Upgrade Enhanced.cmd'], cwd=folder,
                                input=b'\r\n', capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors='replace'))
        self.assertEqual((folder / 'upgrade-ok.txt').read_text(), 'upgrade')

    def test_unsafe_or_duplicate_mapping_cannot_become_config(self):
        for roots in ([], ['relative'], [r'C:\Media' + '\nport=8060'],
                      [r'\\NAS\A', r'\\nas\a' + '\\'], [r'C:\Media=setting'], [r'\\?\C:\Media'],
                      [r'C:\Media' + '\u2028Other']):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                bundle.config_text(roots)

    @unittest.skipUnless(os.name == 'nt', 'Windows CMD launcher')
    def test_upgrade_launcher_preserves_existing_config_instead_of_using_setup_template(self):
        output = self.build()
        with zipfile.ZipFile(output) as archive:
            archive.extractall(self.work / 'Upgrade & Café')
        folder = self.work / 'Upgrade & Café' / bundle.FOLDER
        (folder / 'config.txt').write_text('Must not be passed to the upgrade installer', encoding='utf-8')
        result = subprocess.run(['cmd', '/d', '/c', 'Upgrade Enhanced.cmd'], cwd=folder,
                                input=b'\r\n', capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors='replace'))
        self.assertEqual((folder / 'upgrade-ok.txt').read_text(), 'upgrade')
        self.assertFalse((folder / 'launcher-ok.txt').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows CMD launcher')
    def test_double_click_launcher_handles_spaces_ampersand_and_unicode(self):
        output = self.build()
        extracted = self.work / 'Setup & Café'
        with zipfile.ZipFile(output) as archive:
            archive.extractall(extracted)
        folder = extracted / bundle.FOLDER
        result = subprocess.run(['cmd', '/d', '/c', 'Install Enhanced.cmd'], cwd=folder,
                                input=b'\r\n', capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode(errors='replace'))
        self.assertEqual((folder / 'launcher-ok.txt').read_text(), (folder / 'config.txt').read_text())

    @unittest.skipUnless(os.name == 'nt', 'Windows CMD launcher')
    def test_launcher_rejects_changed_installer_before_execution(self):
        output = self.build()
        with zipfile.ZipFile(output) as archive:
            archive.extractall(self.work / 'extracted')
        folder = self.work / 'extracted' / bundle.FOLDER
        with (folder / bundle.INSTALLER).open('a', encoding='ascii') as stream:
            stream.write('\n# Corrupted after packaging\n')
        result = subprocess.run(['cmd', '/d', '/c', 'Install Enhanced.cmd'], cwd=folder,
                                input=b'\r\n', capture_output=True, timeout=40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'checksum mismatch', result.stdout + result.stderr)
        self.assertFalse((folder / 'launcher-ok.txt').exists())


if __name__ == '__main__':
    unittest.main()
