# LANStream Enhanced operations and acceptance guide

This edition enhances [candre23/LANStream](https://github.com/candre23/LANStream).
It keeps original filenames, folder hierarchy, media routes and hardware
transcoding. Its local SQLite cache replaces repeated complete folder listings.
It requires no cloud service. Use a private LAN or VPN only.

## Separate Windows test install

1. Leave the running `C:\LANStream` installation, config, cache and port 8060
   untouched. Extract the enhanced portable folder to `C:\LANStream-Enhanced`.
2. Edit only the enhanced folder's config.txt. The distributed example is empty
   and uses port **8061**. Add one small test UNC root first. Use read-only media
   permissions. Existing password profiles and `-/Subfolder` exclusions work.
3. Run the enhanced LANStream.exe. Visit `http://127.0.0.1:8061` on that machine,
   then its private LAN IP at port 8061. Allow only private-network access if a
   firewall prompt appears. No public port forwarding is required.
4. After small-share checks pass, add the large share, then the other roots.
   Do not copy a running production cache/database into the enhanced install.
   Optional thumbnail migration: with both copies stopped, copy only `thumbs/`
   and `posters/` into the enhanced cache. Media-root spelling should match.
5. Roll back by stopping the enhanced process. Production remains available at
   its original location and port. No production service changes are needed.

Example (fictional paths; put your own roots in a local config):

```text
port=8061
transcoder=auto
directory_cache_ttl=300
directory_scan_timeout=30
automatic_indexing=true
thumbnail_workers=2
low_bandwidth_resolution=1280x720
low_bandwidth_bitrate=3M

<HouseholdPassword>
\\NAS\Media
-/Restricted
```

Defaults remain 854x480 / 2M for Low Bandwidth. Settings are read at startup;
root/profile changes apply to later requests. Environment variables for config,
cache, port, session secret and transcoder remain supported. Do not share logs
or config files containing passwords or private media paths.

## Try H.265 playback

For HEVC/H.265 sources, including Media Optimizer MKV output, select **H.265**
in the player to keep the original video and repackage it into an MP4 stream
tagged hvc1. The selected audio is converted to stereo AAC. No video encoder
is needed, and the source file is never changed. This mode requires an HEVC
capable browser/device; use **Normal** if playback fails. It preserves the
original video bitrate, resolution and HDR characteristics; it does not tone map
HDR for SDR displays. Seeking lands near keyframes, keeping audio and video
together; use Normal for precise seeking.

Browsers with native HLS support, including Safari, receive generated playback
as a playlist and short MP4 segments. This applies to MKV playback and to
**Low Bandwidth** or **H.265** for any supported source. Compatible MOV/MP4 files
still play directly in Normal mode. Other browsers retain streamed MP4 delivery
through URLs ending in `/video.mp4`. The original filename and media stay unchanged.

**Low Bandwidth** encodes H.264 at the configured size and bitrate, using Intel
Quick Sync when selected. HLS delivery does not change the encoder. Select Low
Bandwidth manually for a slower connection; this release does not automatically
switch quality. Cellular access still requires a private VPN to the server.
H.265 keeps the source bitrate and uses no video encoder.

HLS encoders run near playback speed with a short temporary segment window.
Seeking, quality changes, and resuming a paused stream start a fresh window at
the selected position. Abandoned streams stop after 45 seconds without requests;
their temporary files are removed. Windows stops owned HLS encoders when the
application exits, including during an upgrade. Physical Safari and Intel QSV
acceptance must be checked on the target device after installing the package.
An abrupt process termination can leave temporary HLS folders in the cache;
automatic cleanup covers streams managed by the running application, not
orphaned folders from a previous process. Stop the application before manually
removing those folders.

For Mac Safari and physical iPhone diagnosis, use the isolated comparison player
and ready-to-paste Codex prompt in [tools/mac-playback](../tools/mac-playback/README.md).
The private sample media is supplied separately and must never be committed.

## Browse, search and favorites

Choose List, Grid or Details. Mobile defaults to List; the device remembers
the last choice and Grid density. List previews are 64 by 40 pixels. Filenames
wrap and retain their original spelling. Folders sort before files. Name order
is natural (Clip 2 before Clip 10); date, size and type sorting apply across the
whole cached directory. Folder filtering also searches the whole directory.
Use **File type** in Browse, Search or Favorites to choose **All**, **Videos**,
**Pictures** or **Documents**. Documents include PDFs, CBZ/CBR comics, and
text/Markdown files. Folders remain visible for navigation. The filter combines
with filename filtering and applies before pagination; changing it returns to
page 1. The URL preserves the selection for reloads and Browser Back/Forward.
Previous/Next replaces a page of at most 100 items, keeping the DOM bounded.
Browser Back restores the page/query; view switching keeps the same items.

The first open returns a loading shell and starts a scan. Cold SMB enumeration
can take seconds. Warm revisits use the SSD snapshot. After 300 seconds the
last snapshot is shown while a refresh runs. Refresh folder requests another
scan; existing files are not modified. Offline/failed scans retain the last
good snapshot and report an error. Use Refresh after connectivity returns;
failed folders do not retry on every visit. Windows filesystem calls can remain
blocked after the application's timeout. At most two scan workers stay occupied
until Windows returns; the UI and cached responses remain available.

Search matches case-insensitive **filename substrings**, including Unicode,
across visited/indexed directories. Results show the root label and complete
relative containing-folder path, plus Open folder. Use All indexed roots,
Current subtree or Current folder.
Search is partial until discovery finishes. Counts show cached coverage, never
an invented percentage; offline results can be stale.

With **automatic_indexing=true** (the default), all configured libraries and
their subfolders are indexed automatically at
server startup, including libraries for profiles that have not signed in.
Background refresh passes discover added, renamed and removed files/folders
without a browser being open. The interval uses **directory_cache_ttl** (300
seconds/five minutes by default); a large or slow library can take longer to
finish a pass. Root/profile configuration changes are picked up on the next
background check. Exclusions and filesystem containment checks still apply.

Set **automatic_indexing=false** in config.txt and restart to disable automatic
discovery and periodic library refresh. Browsing still scans folders on demand,
and manual indexing remains available. Existing snapshots and favorites are kept.
Recursive indexing uses lower queue priority than browsing and folder refreshes;
opening a queued folder promotes its scan. A full background queue yields space
to an interactive scan. Already-running scans finish normally; this setting does
not change the operating system's process priority.

Expand **Background indexing** in any folder to see the current library's
progress. **Pause library indexing** stops new traversal work for your profile;
an in-flight scan may finish. **Resume / refresh library index** resumes pending
work, or refreshes the entire library after a pass has finished. These controls
apply to the library root even when opened inside a subfolder. Automatic
indexing resumes after a server restart when enabled, including previously paused tasks.
Browsing remains available while discovery runs. Offline failures retain the
last good snapshots and are retried on a later pass. No mass thumbnail
generation starts automatically.

The star saves videos and folders. Stars appear in all views, search, Favorites,
video details and the player. Favorites persist in SQLite by profile/root/path,
independent of root ordering. Missing or renamed items stay at their original
location and can be removed; they are not silently rematched. Access is checked
again on reads and playback. Changing a profile password changes its identity
and therefore starts a new Favorites set for that profile.

## Thumbnails and media safety

Visible/near-visible items request thumbnails automatically. Existing local thumbs and
posters are reused. The browser requests cached images directly and only polls
for readiness when an image is missing. Automatic folder and search updates keep
loaded thumbnails and pending downloads for unchanged files, so new results do
not restart that work. Transient download failures retry even after generation finishes.
Individual thumbnail downloads and readiness requests time out after ten seconds
so an unavailable file cannot hold up the remaining thumbnails. Large pictures are
reduced before orientation processing to limit decoding and memory overhead.
At most two previews load at once; navigation cancels pending browser requests.
The queue holds at most 64 pending/active jobs, uses two
workers by default (`thumbnail_workers=1` selects one), prioritizes visible/detail work and
retains useful jobs after navigation. High-priority posters can displace lower
priority pending jobs. Background thumbnails pause during active playback;
priority-zero detail posters remain eligible. Failures back off for 60 seconds.
The cache checks source freshness and prevents changed sources from publishing
old queued work. Legacy cached images use file timestamps after restart; if a
replacement deliberately preserves its old timestamp, remove its old cached
image manually. Video candidates use one FFmpeg invocation to check blackness
and extract the JPEG. Readiness checks start at 250 ms, then back off to 1.5
seconds for slower jobs and stop after 90 seconds from the first download attempt;
waiting for a browser request slot does not use that budget. Revisiting retries them.

To prepare thumbnails before browsing, open a folder and click **Prepare
thumbnails**. This covers supported videos, pictures, PDFs and comics in the
entire folder, including later pages, regardless of the current filter. It
does not descend into subfolders. Preparation runs on the server after leaving
the page and reuses cached images. Return to that folder to see ready/failed
counts or click **Stop preparation**. Jobs already generating may finish.
Preparation has lower priority than browsing, pauses during tracked playback,
and feeds at most two items at a time through the same bounded queue. Only one
folder can be prepared at a time. Preparation stops on application shutdown;
start it again to continue using the images already cached. Keep the cache on
local storage. On an N100, check playback and NAS load; select one worker if
two compete with streaming. Existing explicit worker settings are retained.

Media roots are read-only. The database, thumbnails and crawl frontier reside
under the local cache. Symlinks, Windows junctions and reparse entries are
omitted from new listings/crawls. Existing direct media routes recheck resolved
filesystem containment and profile access. Do not use symlink aliases to work
around profile exclusions. The private-network upstream authentication model
is retained; this is not an internet-facing identity service.

## Images, documents and text

Images support next/previous links, arrow keys and swipes. Fullscreen image
navigation stays inside fullscreen. PDF, CBZ and CBR viewers support page
buttons, arrow/Page Up/Page Down keys and swipes, with an exit button inside
fullscreen. Comic pages use natural filename order (1, 2, 10). CBR requires the
bundled 7-Zip backend. Text and Markdown display as escaped plain text.

TIFF conversions and rendered document pages use source modification time and
size in their cache keys, so ordinary file replacements refresh the view and
removed pages are rejected. Replacements that preserve both values require
manual removal of that file's rendered cache. Rendering is serialized to avoid
duplicate publication and concurrent PyMuPDF access. Damaged images/pages show
a readable error; document errors do not expose backend paths. Old render
versions can remain under cache/image-view and cache/document-pages. If space
is needed, stop Enhanced before removing those regenerable subdirectories;
preserve cache/index.sqlite3 and its companions containing Favorites.

## Backup, schema and index recovery

For routine updates, extract the new N100 setup ZIP and run **Upgrade Enhanced.cmd**.
It preserves the existing configuration and full cache, keeps the previous app
as a dated sibling backup, and restores it if the replacement fails to start.
It stops only the selected Enhanced app and leaves unrelated installs alone.
Use **Install Enhanced.cmd** only for a first installation. See the
[upgrade instructions](n100-installer.md#updating-an-existing-enhanced-installation)
for custom folders, backup space, recovery behavior and limits.

The database is **cache/index.sqlite3**, schema version 1, using SQLite WAL.
It contains regenerable snapshots/frontiers and durable favorites. Preserve
config.txt and the complete cache directory when upgrading. Stop the enhanced
process before copying its database, including any `-wal` and `-shm` companions.
Never delete the entire cache as a thumbnail cleanup operation.

Automatic library scans refresh metadata periodically. For an immediate
refresh, use Refresh folder or Resume / refresh library index. This preserves
Favorites. Removed directory snapshots are
retired when a successful parent refresh no longer sees that directory.
Offline failures retain data. Unknown schema versions or corrupt databases
fail closed rather than silently deleting favorites. Restore a known-good
database backup first. If none exists, stop Enhanced, move index.sqlite3 and its
WAL/SHM companions together into a dated backup directory, then restart to
create a clean index. This last resort loses access to favorites in that old
database unless it is later recovered; preserve the backup for recovery.

## Reproducible tests and benchmarks

From the source checkout on Windows:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test tests/explorer.test.cjs tests/player.test.cjs
.\.venv\Scripts\python.exe scripts\benchmark.py --count 25969 --repeats 20 --output benchmark-results\local.json
```

For a real share, use `--root '\\NAS\Media'` instead of `--count`. This never
writes media files. It uses an isolated temporary config/database and reads
directory metadata; synthetic media mode creates/removes only its temporary
fixture. If a sandbox's OS temp directory rejects access, set TMP and TEMP to
an existing writable scratch directory first. Do not benchmark every share at
once. Record separately each root, hardware, connectivity, cold first page,
warm p95, CPU seconds and cache hit counts. `--trace-memory` measures the Python
heap with significant timing overhead; do not compare those timings with
untraced runs. Search uses benchmarked SQLite Unicode substring matching,
without FTS dependencies. Results are in [status.md](status.md).

`scripts/browser_benchmark.cjs` uses an already installed Playwright and browser.
Run it only against a **synthetic fixture server**: it changes a test favorite.
Pass URL and output directory. Set LANSTREAM_PLAYWRIGHT to an installed module
path and LANSTREAM_BROWSER to Chrome/Edge if needed. It measures browser first
render/DOM count and exercises views, paging, filter, search and favorites.

For non-video viewer checks, start a separate fictional fixture server:

```powershell
.\.venv\Scripts\python.exe scripts\viewer_smoke_server.py --work-parent benchmark-results\viewers
```

In another terminal, with the installed Playwright/browser paths configured as
above, run:

```powershell
node scripts\viewer_browser_check.cjs benchmark-results\viewers\viewer-manifest.json benchmark-results\viewer-browser
```

This server binds only loopback port 8061 and uses isolated generated files,
profiles and cache. Stop it with Ctrl+C afterward. To check a built portable
release, append `--executable dist\LANStream\LANStream.exe` to the server command.
The browser script asserts rendered page colors/order, navigation, fullscreen,
viewport bounds, UTF-8/HTML escaping and deliberate corrupt-file error states;
it saves screenshots and result.json. It does not test physical Safari.

## Windows packaging

The existing `build_windows.bat` / `LANStream.spec` workflow is used. Put native
Windows ffmpeg.exe and ffprobe.exe in tools/, plus standalone 7zz.exe or 7z.exe
and 7z.dll. Run `build_windows.bat` in a Windows terminal. It creates an isolated
.venv-build and dist/LANStream. Binary tools and build outputs are Git-ignored.
The output includes independent config/cache plus templates/static resources.
Keep LICENSE, THIRD_PARTY_LICENSES.md and third-party license files with any
distributed copy. Match FFmpeg source/license obligations to the exact bundled
binary; the local build's tool versions appear in the measured status report.

## JSERVER acceptance checklist

Run these checks on the Windows N100; a local development pass cannot prove
its SMB latency, actual encode speed or phone/VPN playback:

1. With Enhanced on port 8061, test the smallest supplied share first, then
   the 25,969-entry share, then the remaining root. Record cold and warm results
   from the read-only benchmark command above. Target warm first-page p95 is
   under 500 ms; target warm substring search p95 is under 300 ms.
2. Navigate back/revisit and restart Enhanced: snapshots should survive and
   revisits must not trigger full enumeration before TTL. Refresh, disconnect
   a test share, observe cached/error state, reconnect and manually refresh.
3. Check each password profile, exclusions, direct denied URLs, Favorites,
   recursive indexing pause/resume and search scope/coverage.
4. Open `/transcoder-status`, force a retest, confirm the Intel QSV **real encode
   succeeds**, then play representative HEVC/MKV content. Check actual encoding
   FPS exceeds playback rate under thumbnail load. An encoder list is not proof.
5. Test a direct MP4 with byte-range seeking, start/seek/stop transcoding,
   audio selection and text subtitles. Low Bandwidth now applies its configured
   scale/bitrate; test 1280x720 / 3M if that is the desired remote profile.
6. Test desktop Chrome/Edge, iPhone Safari on Wi-Fi and over cellular through
   UniFi Teleport. Check all views, readable filenames, small actual List thumbs,
   favorite persistence, search paths, video seeking and remote bandwidth.
7. Record peak RAM, CPU, network throughput and transcoding FPS on the N100
   (Task Manager/Resource Monitor and FFmpeg diagnostics). No public ports or
   changes to production are necessary.
