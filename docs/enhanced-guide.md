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
thumbnail_workers=1
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

## Browse, search and favorites

Choose List, Grid or Details. Mobile defaults to List; the device remembers
the last choice and Grid density. List previews are 64 by 40 pixels. Filenames
wrap and retain their original spelling. Folders sort before files. Name order
is natural (Clip 2 before Clip 10); date, size and type sorting apply across the
whole cached directory. Folder filtering also searches the whole directory.
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
Current subtree or Current folder. Search from here carries the current folder.
Search is partial until discovery finishes. Counts show cached coverage, never
an invented percentage; offline results can be stale.

Expand Background indexing in a folder to index its subtree explicitly. Pause
stops new traversal work; an in-flight scan may finish. Start resumes pending
work. Starting a completed task refreshes its subtree to discover additions.
The frontier is durable, but persisted tasks are **paused after a restart**;
resume them explicitly. Browsing remains available without exhaustive indexing.
No recursive crawl or mass thumbnail generation starts automatically.

The star saves videos and folders. Stars appear in all views, search, Favorites,
video details and the player. Favorites persist in SQLite by profile/root/path,
independent of root ordering. Missing or renamed items stay at their original
location and can be removed; they are not silently rematched. Access is checked
again on reads and playback. Changing a profile password changes its identity
and therefore starts a new Favorites set for that profile.

## Thumbnails and media safety

Only visible/near-visible items request thumbnails. Existing local thumbs and
posters are reused. The queue holds at most 64 pending/active jobs, uses one
worker by default (configurable to two), prioritizes visible/detail work and
retains useful jobs after navigation. High-priority posters can displace lower
priority pending jobs. Background thumbnails pause during active playback;
priority-zero detail posters remain eligible. Failures back off for 60 seconds.
The cache checks source freshness and prevents changed sources from publishing
old queued work. Legacy cached images use file timestamps after restart; if a
replacement deliberately preserves its old timestamp, remove its old cached
image manually. Optional bulk thumbnail pre-generation is not included.

Media roots are read-only. The database, thumbnails and crawl frontier reside
under the local cache. Symlinks, Windows junctions and reparse entries are
omitted from new listings/crawls. Existing direct media routes recheck resolved
filesystem containment and profile access. Do not use symlink aliases to work
around profile exclusions. The private-network upstream authentication model
is retained; this is not an internet-facing identity service.

## Backup, schema and index recovery

The database is **cache/index.sqlite3**, schema version 1, using SQLite WAL.
It contains regenerable snapshots/frontiers and durable favorites. Preserve
config.txt and the complete cache directory when upgrading. Stop the enhanced
process before copying its database, including any `-wal` and `-shm` companions.
Never delete the entire cache as a thumbnail cleanup operation.

To refresh stale metadata, use Refresh folder or explicitly restart a completed
indexing task. This preserves Favorites. Removed directory snapshots are
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
