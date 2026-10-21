# Windows development handoff

Prepared October 21, 2026, for continuing this product in Codex on Windows.

## Start here

Repository: <https://github.com/Justin1491/LANStream-Enhanced>.
Continue from **main**. Both `codex/windows-hls-startup` and
`codex/windows-upgrade-bundle` are already included in main; neither needs to be
merged again. At handoff preparation, the latest implementation commit was
`a9d8285`, tagged **v1.1.3**. This document is a later documentation commit.

The Windows Intel QSV playback problem was reproduced, its cause was identified,
and the fix was verified with Mac Safari and a physical iPhone. Do not restart
the investigation on the assumption that Windows QSV cannot encode this input.
The decisive failure was delayed HLS segment publication.

The user requested that further development move to Windows. They explicitly
declined more software-encoding comparison tests and the optional H.265 playback
mode. Keep future investigation focused on the actual failing backend and layer.
The earlier Mac/Safari work had already introduced HLS delivery at `d015901`;
the Windows startup investigation below builds on that transport change.

## What failed and what fixed it

The Windows app selected Intel QSV. Safari requested the initial HLS playlist,
but the old route returned HTTP 503 after its 20-second wait. Safari could then
show browser error 4. That browser error alone did not identify a codec failure.

The instrumented Windows build showed QSV continuing to encode at approximately
real time, with no reported encoder error, while no playlist, initialization
file or media segment had been published. First readiness took 42.4–43.1 seconds.
The first playlist finally returned HTTP 200 with `TARGETDURATION:42` and
`EXTINF:42.042000`, despite the requested two-second HLS interval.

The existing command scheduled forced frames with
`-force_key_frames expr:gte(t,n_forced*2)`. QSV's default forced I frames did not
provide the IDR/random-access packet boundaries needed for the intended HLS
cuts. The targeted fix adds **`-forced_idr 1` for QSV HLS**, retaining the existing
two-second schedule. It applies to Normal and Low Bandwidth. It is not applied
to every backend or to the separate fragmented-MP4 output path.

The same Windows hardware, representative input and audio track then produced:

| Trial | First publication/readiness | First segment | Observed result |
| --- | --- | --- | --- |
| Before IDR fix, Normal | 42.4–43.1 s | 42.042 s | Exceeded the old 20 s playlist timeout. |
| Updated Normal | 2.5 s | 2.002 s | Mac Safari decoded advancing 1080×1920 video without a media error. |
| Updated Low Bandwidth | 5.0 s | 2.002 s | Mac Safari decoded advancing 270×480 video without a media error. |
| Updated Low, seek forward 30 s | 4.8 s | 2.002 s | Playback resumed at the requested position. |

These are publication timings, not measurements of the full tap-to-picture
latency. Updated first playlists returned HTTP 200 with target duration 2.
The user also confirmed moving picture **and sound in both modes** on their
physical iPhone, reported as an iPhone 17 Pro Max running iOS 27.0.1.
That phone check used manual observations, not USB Web Inspector.

The decisive hardware test used commit `7f1be19`; its running executable SHA-256
was verified as
`39e63cc9383ccf9b09ef04a7850d18e9c788d132c96eb8ede7b964683ccc94f8`.
See [the detailed playback evidence](windows-playback-diagnostics.md) and
[its Windows build](https://github.com/Justin1491/LANStream-Enhanced/actions/runs/39593003827).
This establishes the fix for the tested Windows QSV setup and representative
input, not every GPU, source, browser or operating system.

## Other adjustments made during the investigation

| Area | Adjustment and reason |
| --- | --- |
| Safari startup | The player polls the authenticated HLS route with `prepare=1` once per second for up to 90 seconds. It loads the media URL only when playlist, init and a segment exist. This handles preparation explicitly; the IDR change fixes the long segment that caused the observed delay. |
| Stale player requests | Generation checks prevent an old preparation response from restarting a replaced stream. An autoplay denial can be retried inside the next Play gesture without unnecessarily preparing a new encoder. |
| Publication race | `HLSStream.wait_for()` and `diagnostics()` sample process exit before inspecting files, so a final publication made as the encoder exits is visible. This was independently reproduced with fixtures; it was not established as the cause of the observed 42-second Windows delay. |
| Diagnostics | Playback details expose backend, phase, elapsed time, completed progress records, output file counts/sizes, exit status and a bounded redacted error excerpt. Build/source fingerprints and per-process instance IDs distinguish servers. |
| Windows listening | `SO_EXCLUSIVEADDRUSE` prevents overlapping listeners on the same port. The app reports bind failure without killing another instance. Overlapping listeners were a possible explanation of inconsistent instance observations, not the proven HLS root cause. |
| Encoder selection | An explicit QSV preference tests only QSV and does not test/fall back to software. `auto` retains normal multi-backend availability checks. |
| Optional H.265 mode | Removed the passthrough option and rejected its old `hevc=1` route usage. **HEVC source files remain supported** through H.264/AAC transcoding in Normal and Low Bandwidth. |
| Process inspection | Added the interactive Windows process/build inspector with path/identity checks and guarded stopping. It protects the original installation/port 8060. |
| Windows packaging | Added native fixture checks, PyInstaller builds, actual packaged-executable startup checks and checksummed application/runtime update artifacts. |

Existing HLS ownership, generation limits, cleanup and Windows Job Object
behavior remain important constraints. Do not replace exact process ownership
with a broad kill of every process named `LANStream.exe` or `ffmpeg.exe`.

### Two separate Windows launch issues

The first diagnostic ZIP failed before Python/app startup with
`Failed to load Python DLL ... python312.dll` / `LoadLibrary: Access is denied`.
The user found Windows' **Unblock** checkbox on the original downloaded ZIP.
Unblocking it and extracting into a fresh folder resolved that launch failure.
No antivirus exclusion or global security-policy change was needed.

The next attempt failed with OS error 10048 because 8061 was occupied. Moving
only the diagnostic instance to 8062 resolved that bind conflict. The launcher
now defaults to 8062. Neither issue was the HLS segment-boundary failure.
An advertised `10.5.x.x` address in a startup screenshot belonged to the user's
NordVPN connection; that alone did not establish a VPN playback fault.

## Installed app, diagnostics and source checkout are separate

| Purpose | Default location / port | Handling |
| --- | --- | --- |
| Original installation | `C:\LANStream`, port 8060 | Preserve it and its settings/cache. |
| Enhanced installation | `C:\LANStream-Enhanced`, port 8061 | Updated by Upgrade Enhanced.cmd. |
| Temporary QSV diagnostics | Fresh extracted diagnostic folder, port 8062 | Reads existing Enhanced config/tools, uses its own diagnostic cache, forces QSV. |
| Development checkout | A separate Git checkout chosen on Windows | Use private config and cache overrides; do not develop directly inside the installed application. |

Verify actual executable paths, ports and build fingerprints rather than assuming
the foreground window or the newest download owns a request. Use
[the process inspector](n100-process-check.md) when needed.
Restarting the app normally creates a new session secret, so a fresh browser
login may be required. Never copy/export browser cookies into diagnostic tools.

## Current upgrade release

[v1.1.3](https://github.com/Justin1491/LANStream-Enhanced/releases/tag/v1.1.3)
provides **LANStream-Enhanced-Upgrade.zip**. Unblock the downloaded ZIP if shown,
extract all five files into a fresh folder outside the installation, and run
**Upgrade Enhanced.cmd**. Leave the inner application ZIP compressed.

The upgrade-only bundle contains the launcher, installer script, release
manifest, README and inner application/runtime ZIP. It contains no private
configuration, passwords, media roots, cache or media.

The upgrader:

1. Verifies installer and inner ZIP checksums, archive paths and target identity.
2. Stops only the app at the verified Enhanced installation path.
3. Retains the old installation as a dated sibling backup.
4. Preserves `config.txt` byte-for-byte and copies the complete cache/Favorites.
5. Retains installed FFmpeg/FFprobe, optional NVENC executable and 7-Zip tools
   rather than replacing the tool versions used in the playback investigation.
6. Starts the new app, verifies its owned listener and HTTP startup, and restores
   the previous installation on startup failure, restarting it if previously running.

Backups/logs contain private data; keep them local. The process does not run the
first-install encoder benchmark or change services, firewall rules or global
execution policy. It refuses unsafe paths, conflicting environment overrides,
another simultaneous upgrade or an unrelated process occupying 8061.
See [installer/rollback details](n100-installer.md).
The current existing-install guard expects `7z.exe` plus `7z.dll`; the portable
app/build script's separate support for standalone `7zz.exe` should not be read
as proof that every such installation layout passes the upgrader's marker checks.

Release identity snapshot:

- Source/tag target: `a9d82856474eec7b6f65bf2efe6dd986e6cd74e2` / `v1.1.3`.
- Outer upgrade ZIP SHA-256: `b3231c157aeef91a4b534a24d0b8ed6f7f5581a85ebb4ced2aabb65a8d367b5f`.
- Released EXE SHA-256: `7c0ba3b2e0d31a3feb8bb416d0e089f917270f65940597d4570fba79c6cbc9da`.

The published bundle's hashes, nested manifest, runtime and source identity were
checked. Actual execution of **v1.1.3 on the user's installed machine has not been
confirmed in this conversation**. Its packaged runtime and upgrade/rollback were
tested on Windows CI. Do not relabel those checks as a new physical-device test.
`real_qsv_playback_verified: false` in a generated build identity describes that
new build's CI coverage; it does not erase the earlier `7f1be19` hardware result.

## Continue development on Windows

### Get the latest source

For a new checkout:

```powershell
git clone https://github.com/Justin1491/LANStream-Enhanced.git
Set-Location .\LANStream-Enhanced
```

For an existing checkout, first preserve any local edits, then:

```powershell
git status --short --branch
git remote -v
git fetch origin --tags
git switch main
git pull --ff-only origin main
git log -1 --oneline
```

If Git reports local edits, divergence or a different remote, inspect and preserve
that work before updating. Do not use `reset --hard`, force pushes or wholesale
cleanup to make a pull succeed. Pulling source does not replace the installed EXE.

### Development environment and isolated run

The Windows workflow uses Python 3.12 x64 and Node 22. Install the Python
requirements into a virtual environment; Node is needed for the JS tests.
The source app finds FFmpeg/FFprobe beside `app.py` or on PATH. A local portable
build expects the Windows tools under `tools/`; those EXEs/DLLs are ignored by Git.
Use matching FFmpeg/FFprobe versions and a supported 7-Zip console backend.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
```

Create/edit **config.local.txt** privately, using the tracked generic config as a
starting point only if that private file does not already exist. Use disposable
media/config/cache for development. For an Intel QSV reproduction, a loopback-only
run can use these process-scoped overrides after configuring the private file:

```powershell
$env:LANSTREAM_CONFIG_FILE = (Resolve-Path .\config.local.txt).Path
$env:LANSTREAM_CACHE_DIR = Join-Path (Get-Location).Path 'cache\development'
$env:LANSTREAM_PORT = '8062'
$env:LANSTREAM_HOST = '127.0.0.1'
$env:LANSTREAM_TRANSCODER = 'qsv'
# Add a trusted directory containing ffmpeg.exe/ffprobe.exe to this process PATH.
.\.venv\Scripts\python.exe .\launcher.py
```

Use an unused port. QSV requires the appropriate Windows Intel hardware/driver;
CI fixture success does not provide it. A loopback-only run is not reachable
from an iPhone. Any later LAN test needs deliberate binding/access and the
user's manual picture/sound observation. These overrides affect this PowerShell
session; clear them or open a fresh shell before using the installed app/upgrader.

Never commit real config, passwords, share mappings, source clips, cookies,
generated caches or private playback reports. Keep tracked `config.txt` generic.
Git ignores `config.local.txt`, `config.n100*.txt`, `cache/`, `artifacts/`,
`benchmark-results/`, virtual environments and Windows tool binaries.

### Verification and builds

[The Windows workflow](../.github/workflows/windows-playback.yml) is the canonical
release path. It builds with PyInstaller and publishes diagnostic and release
artifacts. A workflow run **does not automatically publish a GitHub Release**;
review/verify the artifact and publish a deliberately versioned release separately.
Do not move or overwrite existing version tags/assets to disguise a different build.

Useful local checks, with fictional inputs:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
node --test tests/player.test.cjs tests/explorer.test.cjs
powershell.exe -NoProfile -File .\tests\process_check_tests.ps1
```

Read test setup first: the full suite has platform/opt-in tests and a real
thumbnail fixture test that runs if `tools/ffmpeg.exe` is present. The recent
packaging CI avoided real encoding: the runtime smoke test had no media tools,
and upgrade integration used local Windows tool stand-ins with QSV selected.
Use the workflow's explicit test list for the equivalent no-encoding check.
Do not run extra software encoding comparisons merely to re-prove the QSV fix.

For local packaging, [build_windows.bat](../build_windows.bat) builds a full
portable folder and requires FFmpeg/FFprobe and 7-Zip under `tools/`. Review its
config-copy step; never distribute a private config copied into the build.
The workflow builds a public app-only ZIP without configuration or media tools,
then `scripts/build_n100_bundle.py --upgrade-only --artifact-directory release`
wraps it using a matching `release-manifest.json` with
`package_kind: application-update`. This is different from the old private
first-install bundle that accepts `--root` and creates a config template.

## Verification state and known limits

The [main build for a9d8285](https://github.com/Justin1491/LANStream-Enhanced/actions/runs/39599644685)
passed. The exact published artifact came from
[the same-commit branch build](https://github.com/Justin1491/LANStream-Enhanced/actions/runs/39599235050),
which also passed:

- 72 native Windows Python checks, including installer/launcher guards.
- 31 JavaScript player/explorer checks and the PowerShell process-inspector checks.
- The actual frozen EXE's HTTP startup and binary fingerprint check.
- A real isolated portable upgrade preserving config, authentication, Favorites,
  cache and media-tool hashes; forced startup failure restored/restarted the old
  app while unrelated processes and fictional media remained unchanged.

The latest broader Mac Python run had 168 tests, 46 skips and eight failure
records, exactly matching the retained pre-packaging failure names/subcases:

| Test | Unchanged failure |
| --- | --- |
| `test_thumbnail_preparation_requires_csrf_and_respects_profile_folder_scope` | Expected two ready thumbnails, observed zero. |
| `test_cbr_is_real_rar_and_uses_real_7zip` | No local 7-Zip CBR backend. |
| `test_corrupt_and_empty_documents_have_readable_private_errors` | Two `corrupt_cbr` route subcases returned 503 with no CBR backend. |
| `test_documents_page_counts_dimensions_and_natural_order` | CBR subcase returned 503. |
| `test_page_bounds_and_invalid_page_arguments` | Three CBR page subcases returned 503. |

Do not describe the entire repository suite as green. Re-evaluate these cases on
the Windows checkout with its actual dependencies. General library/SMB, every
codec/GPU, subtitle/audio-track combinations, Teleport/VPN and broad household
acceptance remain separate from the demonstrated startup fix. Earlier documents
such as `docs/status.md` and the Mac investigation prompt contain historical
snapshots; do not mistake their older pending items for a reversal of this result.

## Code map and delivery history

| File | Relevant responsibility |
| --- | --- |
| `app.py` | `transcode_command()`, HLS preparation/routes, explicit encoder selection and server fingerprint. |
| `hls_streams.py` | Owned encoder lifetime, readiness/files/progress/error snapshots, publication race and Windows Job Objects. |
| `templates/player.html` | Native HLS preparation polling, generations, seeking/quality changes, autoplay recovery and Playback details. |
| `launcher.py` | Windows exclusive binding and startup identity/port reporting. |
| `scripts/Check-LANStreamProcesses.ps1` | Interactive inventory, build comparison and guarded process stopping. |
| `scripts/Install-LANStreamEnhanced.ps1` | Verified upgrade, application-update archive rules, preservation, backup and rollback. |
| `scripts/build_n100_bundle.py` | Upgrade-only/public bundle versus private first-install bundle. |
| `tests/test_hls_streams.py`, `tests/test_app.py`, `tests/player.test.cjs` | HLS/publication/player regressions, including QSV forced IDR. |
| `tests/test_installer.py`, `tests/test_n100_bundle.py`, `tests/test_upgrade.py`, `tests/test_windows_runtime.py` | Archive/launcher guards, real upgrade rollback and frozen-runtime verification. |

Key commits already merged into main:

- `d015901`: Safari HLS transport and Mac investigation tools (starting baseline).
- `b85f35e`: readiness polling, diagnostics, publication race fix and H.265 option removal.
- `7f1be19`: decisive QSV forced-IDR change and regression; Windows runtime/diagnostic packaging improvements.
- `0c7149b`: Windows process/build inspector.
- `42c0d49`: recorded Windows/physical iPhone validation.
- `7e3c925`: main application/runtime update packaging, released as v1.1.2.
- `a9d8285`: Upgrade Enhanced.cmd app-only preservation/rollback bundle, released as v1.1.3.

For a new failure, identify the exact running build and backend, reproduce the
same source/mode/audio selection, and inspect the first failing layer. Distinguish
process launch, first frame, first segment, playlist publication, retrieval and
browser decoding. Preserve the known working IDR boundaries, generation handling,
private-data boundaries and upgrade rollback while making the smallest supported fix.
