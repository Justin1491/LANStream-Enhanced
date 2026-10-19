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
        self.installer.write_text("param([string]$ManifestPath,[string]$ConfigTemplatePath)\n"
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
            self.assertEqual(set(archive.namelist()), {prefix + name for name in
                [self.application.name, bundle.INSTALLER, 'Install Enhanced.cmd', 'config.txt', 'release-manifest.json', 'README-FIRST.txt']})
            self.assertEqual(archive.read(prefix + self.application.name), self.application.read_bytes())
            config = archive.read(prefix + 'config.txt').decode('utf-8')
            self.assertIn('port=8061\r\n', config)
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

    def test_unsafe_or_duplicate_mapping_cannot_become_config(self):
        for roots in ([], ['relative'], [r'C:\Media' + '\nport=8060'],
                      [r'\\NAS\A', r'\\nas\a' + '\\'], [r'C:\Media=setting'], [r'\\?\C:\Media'],
                      [r'C:\Media' + '\u2028Other']):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                bundle.config_text(roots)

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
