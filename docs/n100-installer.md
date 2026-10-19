# Isolated N100 installer and acceptance checks

Use the installer, release manifest and Windows ZIP distributed together. The
installer reads the manifest's revision and SHA256, verifies the ZIP before
extraction, and checks the installed File type filter from the latest version.
It does not download a different release or depend on Python.

Copy these three files into one folder on the N100:

- Install-LANStreamEnhanced.ps1
- LANStream-Enhanced-Windows-x64-test.zip
- release-manifest.json

Run under the Windows account that can read the media shares. In a PowerShell
window opened in that folder, run the script with explicitly permitted roots:

```powershell
.\Install-LANStreamEnhanced.ps1 -MediaRoot @('\\NAS\Movies', '\\NAS\Photos')
```

It prompts securely for a password and creates one **Enhanced-only test
profile** that can read those roots. Do not supply a password as plain text on
the command line. The application requires the password in its local config;
keep that file private. Existing household profiles/exclusions are a separate
manual acceptance check before broader use.

If script execution is restricted, open a separate process with a temporary
policy, then run the command above inside it:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned
```

A downloaded unsigned script may also need its Properties > Unblock checkbox
after reviewing the trusted script. No persistent execution policy change is
required. The installer creates no service, scheduled task or firewall rule.
Allow the new executable on Private networks when Windows prompts.

The default destination is C:\LANStream-Enhanced, with an independent config
and cache on port 8061. Existing destinations and an occupied 8061 are refused;
no old install is overwritten or stopped. To use another fresh local folder:

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
and the application's selected QSV backend. Search coverage is limited to
visited folders; no recursive crawl or bulk thumbnail generation is started.
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
