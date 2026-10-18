# LANStream Enhanced design and source audit

Source: candre23/LANStream at b837a21. Contract: the supplied
LANStream_Enhanced_Project_Brief.md (October 18, 2026). Attribution and licenses
remain in LICENSE and THIRD_PARTY_LICENSES.md.

## Observed bottlenecks

`browse()` lists every directory entry, repeatedly resolves paths and reads
metadata, looks up folder preview caches, sorts all entries, and renders all
cards. `browse.html` then builds another array over the entire DOM. Home probes
each root and recursively walks thumbnail caches. ImageScheduler starts two
workers and increments a global generation on navigation, abandoning work.
There are no upstream automated tests. The Windows build requires external
FFmpeg, FFprobe and 7-Zip binaries; none are included in the source checkout.

The documented Low Bandwidth toggle sends a quality argument, but this source
revision's transcode command ignores it. This needs a small shared-command fix
and regression tests; the existing QSV detection already performs a real encode.

## Chosen design

Keep Flask, existing media routes, the player, FFmpeg discovery and thumbnail
layout. Add a standard-library SQLite directory index in cache/index.sqlite3.
Raw directory snapshots are shared; every query applies the current profile's
root and exclusion restrictions before counting, ordering and paging. Root IDs
are hashes of normalized absolute configured paths, independent of root order.
Paths returned to clients are relative and never include raw configuration.

Scans run in a bounded daemon pool, deduplicated per directory and serialized
per SMB host. Each scan uses scandir and small SQLite batches, publishes an
atomic snapshot and retains the previous snapshot if it fails. TTL defaults to
300 seconds; navigation serves fresh or stale snapshots immediately. Manual
refresh schedules work. Cold navigation shows loading; no fictional percent.
Windows can block within a filesystem call beyond an application deadline;
the request remains responsive and worker concurrency stays capped, but a dead
share can occupy a worker until Windows returns.

Cached listing authorization uses validated lexical root-relative paths and
exclusion prefixes without network calls. Scans reject symlinks, junctions,
reparse entries and link ancestors. All actual media/thumbnail access retains
safe_path filesystem containment and current-profile checks. Directory aliases
configured as roots are separate identities. Cache metadata is eventual, and
renames require refresh. Do not treat the cache as proof a source still exists.

The UI renders at most 100 result rows/cards, replacing pages rather than
appending without a limit. SQL sorts and filters the whole snapshot. Grid,
List and Details share items, accessible links and favorite controls. Preferences
live in localStorage; mobile defaults to List. Thumbnail requests are limited
to visible/near-visible items. One bounded thumbnail worker keeps useful jobs
across navigation, promotes visible work and backs off failures.

Search uses Unicode casefolded filename substrings over cached snapshots.
The SQL strategy is benchmarked before claiming target performance. Only
visited directories are indexed automatically. An explicit bounded recursive
index task persists pending directories locally and supports pause/resume.
Favorites live in a separate SQLite table scoped to the authenticated profile,
root ID and relative path. They survive index refresh/rebuild and missing media.

## Safety and release

No media writes, production changes, cloud backend or port-forward changes.
All development data uses synthetic media and an isolated cache/config. The
enhanced example uses port 8061. Builds remain portable Windows PyInstaller
builds. Actual N100 QSV, SMB latency, Safari and Teleport playback require the
target environment and are reported separately from local verification.
