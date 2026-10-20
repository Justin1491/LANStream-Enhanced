"""Bundle a verified release with a launcher and private, local share mappings."""
import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import zipfile


BASE = Path(__file__).resolve().parents[1]
FOLDER = 'LANStream-Enhanced-N100-Setup'
INSTALLER = 'Install-LANStreamEnhanced.ps1'
BOOTSTRAP = r"""
$ErrorActionPreference='Stop';
try {
    $folder=$env:LANSTREAM_SETUP_FOLDER;
    $manifestPath=Join-Path $folder 'release-manifest.json';
    $manifest=[IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json;
    if ($manifest.installer.script -cne 'Install-LANStreamEnhanced.ps1' -or $manifest.installer.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid installer identity in release manifest.' };
    $installer=Join-Path $folder 'Install-LANStreamEnhanced.ps1';
    $stream=[IO.File]::Open($installer,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);
    $hasher=[Security.Cryptography.SHA256]::Create();
    try { $hash=[BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-','') } finally { $hasher.Dispose(); $stream.Dispose() };
    if ($hash -ine $manifest.installer.sha256) { throw 'Installer checksum mismatch. Nothing was installed.' };
    Unblock-File -LiteralPath $installer;
    if ($env:LANSTREAM_SETUP_UPGRADE -eq '1') { & $installer -ManifestPath $manifestPath -Upgrade }
    else { & $installer -ManifestPath $manifestPath -ConfigTemplatePath (Join-Path $folder 'config.txt') };
    exit $LASTEXITCODE
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
"""

README = """LANStream Enhanced N100 setup

1. Extract this entire outer ZIP to a writable local folder on the N100,
   such as Downloads, outside the existing installation.
2. For an existing Enhanced installation, double-click Upgrade Enhanced.cmd.
   For a first installation, double-click Install Enhanced.cmd.
3. Keep all files together. Only a first installation asks for a new password.

UPGRADES: Upgrade Enhanced.cmd updates C:\\LANStream-Enhanced on port 8061.
It verifies the package, stops only that installation's running app, preserves
config.txt and the complete cache (including Favorites), and retains the old
installation as a dated sibling backup. It starts the new app and restores the
old installation if startup fails. Your passwords, roots and exclusions stay
unchanged. The setup config.txt template is ignored during an upgrade.
Backups contain private settings and Favorites; keep them private. Leave room
for the old application plus a second copy of its cache. No backup is deleted
automatically. Database errors that prevent startup cause rollback, never a reset.

For a first install, config.txt contains your UNC mappings and a password
placeholder. Install Enhanced.cmd uses that template and writes the password
into C:\\LANStream-Enhanced\\config.txt before starting the app on port 8061.
Existing household profiles/exclusions require review before broader use.
Edit mappings/exclusions in the template before setup if needed. Do not
replace the password placeholder manually or add another profile.

Setup checks the installer and application ZIP hashes, then runs automated
acceptance tests for a first install. First installs refuse existing folders;
upgrades require an existing Enhanced installation. Unrelated port8061 owners
are refused. Routine upgrades check startup without requiring QSV acceptance.
C:\\LANStream and port8060 are protected. No service, scheduled task, firewall
rule, global execution policy or NAS files are changed. The launcher unblocks
only its checksum-verified installer script, using a temporary process policy.

PASS leaves Enhanced running. Upgrade failures restore the previous install;
it is restarted only if it was running before the upgrade.
Upgrade prints backup/recovery status and its private log folder. For a first
installation, read C:\\LANStream-Enhanced\\acceptance-report.json. A failed new
install remains for diagnosis; preserve it before retrying a fresh install.
Windows may ask to allow Private-network access for the new executable.

These tests share CPU/GPU/network with existing apps; run while production
is idle. Physical Safari/Teleport and real browser playback remain manual.
Use a private browser window for Enhanced to keep existing cookies separate.

PRIVATE: this bundle/config contains personal share paths. The installed
config also contains the password. Keep config/logs private. Personal bundle
outputs belong only in Git-ignored artifacts/, never in tracked source files.
"""


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def config_text(roots):
    seen = set()
    normalized = []
    for root in roots:
        path = PureWindowsPath(root)
        if (not root or root != root.strip() or not path.is_absolute()
                or any(ord(char) < 32 or char in '\x85\u2028\u2029<>|"=*?' for char in root)
                or root.startswith(('\\\\?\\', '\\\\.\\'))):
            raise ValueError('Use absolute Windows/UNC media paths without config control characters')
        value = str(path)
        if value.casefold() in seen:
            raise ValueError('Duplicate media root')
        seen.add(value.casefold())
        normalized.append(value)
    if not normalized:
        raise ValueError('At least one media root is required')
    lines = [
        '# Private N100 setup template. Setup prompts for the application password.',
        'port=8061', 'use_master_password=false', 'master_password=', 'transcoder=auto',
        'directory_cache_ttl=300', 'directory_scan_timeout=30', 'automatic_indexing=true', 'thumbnail_workers=2',
        'low_bandwidth_resolution=854x480', 'low_bandwidth_bitrate=2M', '',
        '<__LANSTREAM_SETUP_PASSWORD__>', *normalized, '',
    ]
    return '\r\n'.join(lines)


def build_bundle(artifact_directory, roots, installer_path=BASE / 'scripts' / INSTALLER):
    artifact_directory = Path(artifact_directory).resolve()
    manifest_path = artifact_directory / 'release-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    archive_name = manifest['archive']
    if archive_name != 'LANStream-Enhanced-Windows-x64-test.zip':
        raise ValueError('Unexpected application archive name')
    application = artifact_directory / archive_name
    if sha256(application) != manifest['sha256'].lower():
        raise ValueError('Application ZIP checksum mismatch')
    config = config_text(roots).encode('utf-8')
    installer_bytes = Path(installer_path).read_bytes()
    manifest['installer'] = dict(script=INSTALLER, sha256=hashlib.sha256(installer_bytes).hexdigest())
    manifest_bytes = (json.dumps(manifest, indent=2) + '\n').encode('utf-8')
    bootstrap = ' '.join(line.strip() for line in BOOTSTRAP.splitlines() if line.strip())
    def launcher(upgrade):
        return '\r\n'.join([
            '@echo off', 'setlocal DisableDelayedExpansion', 'set "LANSTREAM_SETUP_FOLDER=%~dp0"',
            'set "LANSTREAM_SETUP_UPGRADE=' + ('1' if upgrade else '0') + '"',
            '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -Command "' + bootstrap + '"',
            'set "setupResult=%errorlevel%"', 'echo.',
            'if not "%setupResult%"=="0" echo Setup failed. Read the messages above for backup or recovery details.',
            'pause', 'exit /b %setupResult%', '',
        ]).encode('ascii')
    entries = {'Install Enhanced.cmd': launcher(False), 'Upgrade Enhanced.cmd': launcher(True), INSTALLER: installer_bytes,
               'release-manifest.json': manifest_bytes, 'config.txt': config,
               'README-FIRST.txt': README.encode('utf-8')}
    output = artifact_directory / (FOLDER + '.zip')
    temporary = artifact_directory / (FOLDER + '.next.zip')
    with zipfile.ZipFile(temporary, 'w') as bundle:
        info = zipfile.ZipInfo.from_file(application, FOLDER + '/' + archive_name)
        info.create_system, info.external_attr, info.extra = 0, 0x20, b''
        with application.open('rb') as source, bundle.open(info, 'w') as target:
            shutil.copyfileobj(source, target)
        for name, data in entries.items():
            info = zipfile.ZipInfo(FOLDER + '/' + name)
            info.create_system, info.external_attr, info.extra = 0, 0x20, b''
            bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(temporary) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError('Bundle CRC verification failed')
    temporary.replace(output)
    # Personal output stays alongside the ignored release artifacts, never source/config.txt.
    (artifact_directory / 'config.n100.txt').write_bytes(config)
    (artifact_directory / INSTALLER).write_bytes(installer_bytes)
    manifest_path.write_bytes(manifest_bytes)
    digest = sha256(output)
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n', encoding='utf-8')
    (artifact_directory / (INSTALLER + '.sha256')).write_text(f"{manifest['installer']['sha256']}  {INSTALLER}\n", encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', action='append', required=True, help='Private root; saved only under artifacts/')
    args = parser.parse_args()
    output = build_bundle(BASE / 'artifacts', args.root)
    print(json.dumps(dict(bundle=str(output), bytes=output.stat().st_size, sha256=sha256(output))))


if __name__ == '__main__':
    main()
