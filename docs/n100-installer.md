# Isolated N100 installer and acceptance checks

Copy LANStream-Enhanced-N100-Setup.zip to the N100 and **extract the entire ZIP
to a local folder you can write to**, such as Downloads, outside the existing
installation. Open the extracted LANStream-Enhanced-N100-Setup folder
and double-click **Install Enhanced.cmd** for a first install, or **Upgrade
Enhanced.cmd** to update an existing Enhanced install. Use the Windows account
that can read the media shares. Keep all seven included files together; leave the inner
application ZIP compressed.

## Updating an existing Enhanced installation

For routine releases, extract the new setup ZIP and run **Upgrade Enhanced.cmd**.
It updates `C:\LANStream-Enhanced`, verifies the package before changing anything,
and stops only the app running from that installation. It keeps your existing
`config.txt` byte-for-byte and copies the complete `cache` directory, including
Favorites, directory snapshots and generated previews, into the new app.
The setup template and password prompt are not used during upgrades. Existing
profile passwords, roots, exclusions and preferences remain unchanged; new
settings use the application's defaults when absent from your config.

The previous installation is retained as a dated sibling backup. Allow free
disk space for the new app and another copy of your cache. Backups are not
deleted automatically and contain private configuration and Favorites. Keep
them until the new version has been checked, then manage older backups yourself.
The console prints the exact `<install-folder>.backup-...` path and rollback status.
Private upgrade logs remain in the printed `.LANStream-Enhanced-upgrade-...`
sibling folder. A failed replacement is retained as `<install-folder>.failed-...`.

The installer starts the replacement app and checks its listener and HTTP
response. If startup fails, it keeps the failed replacement for diagnosis,
restores the previous installation, and restarts it if it was running before.
An unsupported schema or a database error that prevents startup causes rollback,
never a silent reset. Startup checks do not replace a full database integrity
check. A future release that requires a data migration needs explicit migration
support; routine upgrades do not erase data.

For a custom Enhanced installation folder:

```powershell
.\Install-LANStreamEnhanced.ps1 -Upgrade -InstallDirectory 'C:\LANStream-Enhanced-Trial'
```

Do not combine `-Upgrade` with `-MediaRoot`, `-ConfigTemplatePath`, `-Password` or
`-SampleVideo`. The upgrade uses the existing configuration and supports the
isolated Enhanced port 8061. It refuses unrelated listeners and unsafe paths.
Conflicting custom runtime environment overrides are refused before changes.
Only one upgrade can operate on the same installation at a time.
It does not run the first-install QSV benchmark; check actual playback afterward.

## First installation

The launcher checks the installer SHA256 against the included manifest before
unblocking that script, then runs it with a process-only RemoteSigned policy.
The installer verifies the application ZIP before extraction and checks the
installed File type filter. It does not download a different release or depend
on Python. No persistent execution policy change is made.

The included **config.txt** supplies the UNC mappings directly; mapped drive
letters are unnecessary. Edit its roots and optional `-/Subfolder` exclusions
before running setup if needed. Keep the single
`<__LANSTREAM_SETUP_PASSWORD__>` placeholder: setup prompts for a password and
substitutes it only in the new installation. Settings and exclusions are
preserved. This creates one Enhanced-only test profile; review household
permissions before broader use.

Personal bundle/config outputs belong in Git-ignored artifacts/. The tracked
source/config.txt remains a generic example. config.n100*.txt and
config.local.txt are also ignored for private local copies. Never copy personal
settings into the tracked example or force-add private release files to Git.
The installed config contains the profile password and must remain private.

For manual PowerShell use with the same template:

```powershell
.\Install-LANStreamEnhanced.ps1 -ConfigTemplatePath (Join-Path $PWD 'config.txt')
```

Or supply roots explicitly instead of using a template:

```powershell
.\Install-LANStreamEnhanced.ps1 -MediaRoot @('\\NAS\Movies', '\\NAS\Photos')
```

Do not combine ConfigTemplatePath and MediaRoot, or supply a password as plain
text on the command line. Setup prompts securely in either mode.

If script execution is restricted, open a separate process with a temporary
policy, then run the command above inside it:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned
```

A downloaded script run manually may need its Properties > Unblock checkbox
after review; the bundled launcher handles its verified installer. The
installer creates no service, scheduled task or firewall rule. Allow the new
executable on Private networks when Windows prompts.

The default destination is C:\LANStream-Enhanced, with an independent config
and cache on port 8061. First installations refuse existing destinations and
an occupied 8061; use Upgrade Enhanced.cmd for an existing Enhanced install.
To use another fresh local folder:

```powershell
.\Install-LANStreamEnhanced.ps1 -InstallDirectory 'C:\LANStream-Enhanced-Trial' -MediaRoot @('\\NAS\Movies')
```

C:\LANStream and descendants, junction/reparse paths, mapped network install
locations, broad media roots containing either installation, unsafe ZIP paths,
and mismatched manifests/hashes are rejected. The installer does not request,
bind or control port 8060. It pins the child's config/cache/port environment,
restores the calling process's environment, and sends HTTP checks only after
confirming that its retained child owns port 8061.

The automated checks cover bundled tool versions, authentication, home/static
assets, each root's cold listing and 20 warm samples, 100-item bounds, new File
type categories, indexed filename search, a bounded byte-range read when a
video appears on a tested first page, actual QSV encoding of synthetic frames,
and the application's selected QSV backend. The application automatically indexes
all configured libraries in the background and refreshes them periodically;
search coverage is partial until discovery finishes. The acceptance checks do
not wait for an exhaustive crawl or start bulk thumbnail generation.
Cold means a new application cache; Windows/SMB caches are not flushed.

Warm browse p95 must be at most 500 ms and search p95 at most 300 ms. Real QSV
must work: software fallback does not pass N100 acceptance. An inaccessible
root, failed hardware test or missed target produces a failing overall result.
Windows may delay filesystem preflight on an unreachable SMB server.

To also read and encode up to 60 seconds of a representative video to the null
sink, pass a file inside one of the explicit roots:

```powershell
.\Install-LANStreamEnhanced.ps1 -MediaRoot @('\\NAS\Movies') -SampleVideo '\\NAS\Movies\Example.mkv'
```

The sample probe scales to 1280x720 and records encoding speed. It is a bounded
QSV diagnostic, not a replacement for checking the browser's actual playback
profile. Media sources are never written.

Exit code 0 means the automated checks passed; Enhanced stays running. Exit
code 1 means failure; only the process started by this invocation is stopped.
The new installation remains for diagnosis. A failed extraction can leave its
uniquely named staging folder. Preserve results before choosing another fresh
destination for a retry.

Read acceptance-report.json and acceptance-logs/ inside the new installation.
The report distinguishes PASS/FAIL/SKIP and lists manual work. Config and logs
may contain credentials or private media paths; do not share them. The script
does not stop any process by the shared name LANStream.exe.

Still check real browser HEVC playback, audio/subtitles, seeking, Low Bandwidth,
physical Safari/Teleport, household exclusions, Favorites after restart and
recovery using a disposable test share. Do not disconnect production networks
or shares to simulate an outage. The existing installation can remain running.
Use a separate browser profile or private window for Enhanced so cookies on
the same hostname do not interfere with the existing browser session. Observe
N100 CPU, RAM, GPU video-engine load and network throughput during playback.

After a successful run the server has a hidden window. To stop only the
Enhanced instance, verify its path before stopping the port's owner:

```powershell
$install = 'C:\LANStream-Enhanced'
$ownerIds = @(Get-NetTCPConnection -LocalPort 8061 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($ownerId in $ownerIds) {
    $process = Get-Process -Id $ownerId -ErrorAction Stop
    if ($process.Path -ine (Join-Path $install 'LANStream.exe')) { throw 'Unexpected port owner; no stop requested.' }
    Stop-Process -InputObject $process
}
```
