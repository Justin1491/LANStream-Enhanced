# Library Cleanup and saved video duration

Date: 2026-11-10

Status: Approved in chat on 2026-11-10; implementation authorized in the following user request.
Scope: Windows LANStream Enhanced, with existing Flask, SQLite, templates, JavaScript, and FFprobe.

## Intent and agreed direction

Provide a separate Library Cleanup page for finding and comparing files quickly, then moving selected files into recoverable Trash. Reuse LANStream's indexed filename search rather than walking media shares for each interaction. Add duration sorting to normal folder browsing without introducing cleanup controls into that workflow.

The user chose the separate page and recoverable Trash. Defaults identified below are design proposals, not additional user decisions. A companion browser mockup uses fictional files and only simulates actions.

Companion: [interactive standalone mockup](2026-11-10-library-cleanup-mockup.html). Open the HTML in a browser; no server is required. Try matching sizes and thumbnails, select a copy, move it to Trash, and restore it. `Browse preview` demonstrates the Duration sort selector. Reload resets the sample files. This is a layout/workflow preview, not a simulation of network failures, database performance, permissions, or recovery guarantees.

## Current implementation and constraints

- `directory_index.py` stores versioned folder snapshots with names, relative paths, kinds, sizes, modification times, and casefolded names. Only supported video, image, PDF, comic, and text files are indexed; arbitrary files are outside this feature.
- `DirectoryIndex.search()` already applies profile roots, exclusions, and folder/subtree scope. Search currently sorts by name. Folder listing supports name, modified date, size, and type; sorting precedes pagination.
- Duration is read by `app.py:probe()` when needed for media operations, not persisted in the directory index. The separate `ffprobe-cache` worktree has a short-lived in-memory probe cache; it is not a durable duration catalog and is not a prerequisite or merge target for this design.
- `explorer_write_check()` provides CSRF validation. Existing profiles restrict readable roots and excluded paths, but have no separate administrator role.
- `ImageScheduler` already pauses preparation when the stream tracker reports playback. Cached-thumbnail delivery is separate from the readiness endpoint, which can schedule generation.
- The directory index shares a SQLite connection and lock. Expensive cleanup queries, network operations, and FFprobe must not hold that shared lock.

## Approaches

| Approach | Benefit | Cost / decision |
| --- | --- | --- |
| Separate page within LANStream, sharing indexed data | Reuses search, profiles, cached thumbnails, and deployment | Selected by the user; isolate frontend assets and bound new background work |
| Extend the ordinary browser with cleanup controls | Less new UI code | Conflicts with the requested separate workflow; not selected |
| Standalone cleanup application | Independent lifecycle | Duplicates authentication/indexing or needs cross-process database coordination; not selected |

Trash is managed by LANStream on the source storage, rather than relying on the operating system's Recycle Bin. Do not depend on Windows share deletion being recoverable through Explorer.

## Product behavior

### Library Cleanup

Add a global `Library Cleanup` navigation link to `/cleanup`. Its own template, CSS, JavaScript, preferences, and selection state are loaded only on cleanup pages. A small `Open in Library Cleanup` link on folder browsing passes that exact folder as the initial scope; it does not change normal Search defaults.

The page has Library and Trash tabs. Library provides:

- A filename-contains search, using the existing Unicode casefold behavior, including an empty query.
- Scope: all accessible libraries, selected library, current folder, or current folder including subfolders. Pick folders through indexed breadcrumbs/folder navigation; folders are navigation entries, never deletion targets.
- Existing file-type categories. Default to all supported files.
- A table with selection checkbox, optional thumbnail, filename, location, size, video duration, modified date, and type. Names open the existing viewer; location opens the containing folder. Selecting a checkbox never opens a file.
- Sortable filename, location, size, duration, modified, and type columns. Server-side filtering and sorting apply to the entire result set before the existing 100-item pagination. Use stable name/path/root tie breakers. Unknown duration sorts last in both directions.
- Readable sizes plus exact byte counts on focus/hover and in the selected-file confirmation. Duration displays hours/minutes/seconds; unavailable values display an em dash with a reason where known.
- `Show matching sizes`, off by default. Apply scope, access restrictions, file type, and filename filter first; retain only exact-size groups with two or more distinct paths. Sort groups by size, then location/name. Group counts describe the complete filtered set, not just the visible page. Repeat the group label when a group spans pages.
- `Thumbnails`, off by default and stored under a cleanup-specific preference. When on, load only cached images for visible rows, with at most two concurrent requests. Missing images show a placeholder. Turning it off stops pending requests and creates no new image requests. Use explicit `Prepare thumbnails` for missing images in the selected folder, reusing the existing nonrecursive preparation behavior and clearly stating that scope.

Show indexing coverage and stale/offline status beside results. Do not imply that an incomplete index contains every duplicate. Same size, duration, or thumbnail is a comparison clue, never proof of identical contents. No automatic duplicate selection, hash scan, similarity matching, or duration-tolerance grouping in this version.

Selection is page-local: `Select this page` selects visible file rows only. Show count and total selected bytes in a persistent action bar. Sorting, changing scope/filter/page, and switching tabs clears selection. Background data refresh may retain unchanged selected identities, but removes or flags changed entries. There is no hidden selection across pages and no automatic selection of every search result.

`Move to Trash` opens a confirmation listing every selected filename and location, count, and selected bytes. The dialog explains that space is retained until permanent deletion. Nothing moves merely by checking a row. On success remove successful rows, recompute groups/counts, retain failures with their reasons, and offer `View Trash` for recovery. No repeated confirmation per file.

### Trash

Trash lists filename, original location, size, duration if known, trashed time, and status. It supports filename search, accessible root scope, stable sorting, and 100-item pages. No thumbnails or media playback from Trash in the first version.

`Restore selected` previews original destinations and restores to the original path only. An existing destination, missing parent folder, changed permissions, changed root mapping, excluded destination, or offline share produces a per-file failure. Never overwrite, choose a new name silently, or recreate a removed directory tree. Users can resolve a collision outside LANStream and retry.

`Permanently delete selected` requires a separate confirmation with filenames, locations, count, and bytes, and explicit text that it cannot be undone. Provide no automatic expiry, scheduled purge, or global Empty Trash action in the first version. This keeps every purge tied to a bounded, reviewable selection. A failed purge remains visible and recoverable if its payload still exists.

Trashing is a physical library change: any other profile accessing the same file also loses it from Library. Trash visibility and actions are based on current authorization to the original root/path, not possession of an opaque Trash ID or the identity of the user who first trashed it. Never reveal another profile's excluded filenames or locations through counts, jobs, errors, or Trash.

### Ordinary browsing

Keep the existing size sort. Add `Duration` to the existing folder sort selector and show saved duration for video files in Details view. Folders remain first; known video durations follow in the chosen direction, followed by unknown/non-video durations. Preserve existing URL/history handling and defaults. Display a short link to prepare missing durations in Library Cleanup when duration sorting has unknown values. Selecting Duration never starts FFprobe.

Normal Search and Favorites retain their existing UI and ordering. They must exclude trashed files; preserve favorite records so that restoring a file restores its favorite status. While trashed, suppress its favorite row without deleting the user's preference. Purged/missing favorite entries retain the existing missing-item behavior.

## Saved duration and preparation

Add a persistent `media_metadata` table alongside directory snapshots, keyed by stable root ID and relative path, with indexed size/mtime version, live nanosecond stat fingerprint, nullable finite nonnegative duration seconds, probe status, and last attempt/error. Preserve it across folder snapshot replacement. Metadata survives application restart; rebuilding a disposable index may require duration preparation again. Unknown/failed duration is never represented as zero.

Queries join duration only where the stored size/mtime version still matches the active snapshot. A changed size/mtime hides stale duration immediately when the new folder snapshot publishes. Before collecting and after FFprobe finishes, compare live size, nanosecond timestamps, and available filesystem identity; discard results if the source changed. Header metadata cannot detect an external edit that preserves every version field; make no stronger identity claim.

`Prepare durations` operates on the selected indexed library/folder scope and file type (videos only), independent of the current filename filter and matching-size toggle. State that scope in its confirmation. Walk indexed candidates in bounded batches without filesystem enumeration; visit only missing, changed, or explicitly retried failures. Reuse `probe()` and `duration_seconds()` with validation rather than adding another FFprobe command. Reuse the existing explicit video-detail/player probe results to save valid duration opportunistically, without probing anything extra.

Use one server-wide preparation job and at most one active metadata FFprobe process. It has progress counts, current scope, pause/resume, and stop. A second start returns the existing job rather than queues an unbounded workload. Job state is visible only to the initiating profile; candidate access is checked again before each item. Restart marks an interrupted job stopped; saved durations remain, and a new preparation skips valid rows. No automatic startup-wide duration crawl.

Reuse the existing stream-tracker signal: do not start the next probe while tracked playback is active. Allow an already-running probe its existing 30-second timeout, and explain that pause takes effect between files. Stop prevents further work; failed files do not loop automatically. User-requested Retry failures starts another bounded pass. Preparation closes resources and child processes on shutdown as the existing Windows runtime requires.

## Trash storage, journal, and operation lifecycle

### Storage

Use a reserved `.lanstream-trash` directory at the configured library root, with one unguessable item directory per move containing a payload with an opaque name and a versioned JSON manifest. The manifest records original root identity/path, original relative path, file attributes/fingerprint, operation ID, state, and trashed time; it never records passwords. Keep this manifest on the share beside its payload so loss of local cache does not orphan recoverable media.

The directory is created only on an explicit move, must not replace an existing unrecognized directory, and must not traverse a symlink/junction/reparse point. Reject an incompatible preexisting directory and report the conflict. Its Windows Hidden attribute is optional presentation, not an access control. It contains private data and inherits the library storage's permissions; documentation must include it in backup guidance and explain that external tools can still see it.

Reserve the directory name in every path component: crawlers, folder listings, Search, Favorites, viewers, raw media, cached thumbnails, thumbnail preparation, duration preparation, and mutation targets reject/skip it. A configured root inside a reserved Trash directory is rejected. Enforce this for nested/overlapping roots and old cached snapshots as well as new scans. Never rely on an HTML-hidden folder or filename extension for protection.

Moves use a same-filesystem, no-overwrite rename; do not copy and delete large videos. A cross-device or unsupported share response fails safely and leaves the source intact. There is no fallback to permanent deletion. The initial supported mutation target is Windows/local NTFS and validated Windows SMB shares. Other platforms may read the cleanup page, but mutations remain disabled until their no-overwrite and crash-recovery behavior is implemented and tested.

### Journal and reconciliation

Add a durable `trash_items` journal with operation ID, stable root/path, original attributes, Trash item ID, operation state, and per-file error. It may live in the existing SQLite database, but unlike folder snapshots it is not disposable. Backup/upgrade guidance must preserve it; the on-share manifest remains the recovery source if the local index is lost.

Filesystem rename and SQLite commit cannot form one transaction. For each file:

1. Verify access and the selected file version, then durably record move intent in SQLite and a safely published manifest before moving data. If either intent cannot be saved, do not move.
2. Invalidate active directory scans for every affected configured path alias so an older scan cannot republish a moved file. Under the existing coordination lock, expire affected work/publication generations; release the lock before network I/O. At publication, stale scans must be rejected.
3. Perform the no-overwrite rename into the unique item directory. Validate the moved file identity; if an external replacement raced the operation, mark recovery required and preserve all data rather than report success or purge it.
4. Durably mark the item trashed and update the index to hide the original entry and any known overlapping-root aliases immediately. Invalidate affected metadata and pending thumbnail/probe work; retain reusable cached images and duration in the manifest for restore validation. Refresh the parent folder asynchronously.

Restore and purge also journal intent before the filesystem action and record completion afterward. Restore uses a no-overwrite move to a currently authorized existing parent. Purge unlinks only the specific verified regular payload file; it never recursively deletes a supplied directory. Remove an item directory only when known metadata files and payload are safely accounted for.

On startup reconcile incomplete journal entries on demand through the bounded operation worker, not by blocking HTTP startup or walking entire libraries. Source present/payload absent means a move did not complete; payload present/source absent means reconcile the move as trashed. Both present, both absent, changed identity, invalid manifest, or uncertain share state means `Needs attention`; never infer permission to delete either copy. Apply the corresponding rules to restore/purge intents so retries cannot move or delete a newly created replacement file.

Ordinary Trash listing reads the journal without share enumeration. Provide explicit `Recover Trash records` for a chosen root after cache loss: enumerate only that root's reserved Trash directory in a background job, validate manifests and containment, and rebuild records. Offline roots retain records with a clear availability error. Never automatically purge orphan or malformed payloads. If the journal is unavailable while manifests may exist, fail mutations closed until reconciliation/recovery is complete. A root without a locally recorded Trash initialization must first check/recover its reserved directory through this worker before its first mutation; a freshly created empty database is not evidence of empty on-share Trash.

### Requests, concurrency, and authorization

All destructive actions use the existing login/profile/exclusion checks and CSRF mechanism. Proposed server setting: `library_cleanup_enabled=false` by default gates move/restore/purge. Reads remain available under existing access rules. Enabling writes also requires the existing password authentication mode; passwordless browsing continues as before, with cleanup read-only. This adds no new account system: when enabled, every authenticated profile can manage the files it already has access to. Document that consequence explicitly.

The server accepts stable root IDs and validated relative paths/opaque item IDs, never a client-supplied absolute filesystem path. Root configuration order is not an identity. Issue a server-bound preview/version token for each selected snapshot entry. Preflight checks live file attributes against that snapshot, captures nanosecond timestamps and available file ID, and binds the confirmation to the verified file. Revalidate on execution using no-follow path/handle checks appropriate for Windows; reject changed files, links, directories, traversals, alternate streams, and excluded targets. Treat external changes as conflicts, not a reason to retry against whatever now occupies the path.

Limit action requests to one displayed page (100 files), deduplicate targets across overlapping configured roots, and serialize filesystem mutations in one bounded server-wide worker. Job submission/status returns promptly; request threads never wait on SMB rename/stat calls. No shared database lock is held while accessing a share or running FFprobe. Batch operations report one result per file, permit partial success, and are idempotent by operation ID; disconnect/retry cannot repeat completed actions. Lock conflicting cleanup/preparation work by physical path within the application. Do not interrupt streams: return Busy for a file in a tracked stream or active metadata/thumbnail task, and let the user retry. OS sharing violations are per-file failures as well.

A running Windows/network call cannot always be interrupted safely. Show Pending rather than a false timeout success; never start a second conflicting action while its outcome is unknown. Other page reads must remain usable. Multiple LANStream processes writing the same libraries are outside first-version support; use a per-root exclusive lock held for an active mutation/recovery and fail Busy if another instance owns it. The implementation must test actual share lock semantics before enabling those writes.

## Query and performance boundaries

Use a separate read connection for Cleanup's database-only queries so matching-size grouping cannot hold `DirectoryIndex._lock`. Reuse common scope/exclusion construction rather than duplicating authorization SQL. Use WAL, short write transactions, bounded result pages, and cancellable/superseded cleanup queries. Add only indexes justified by representative query plans (size/scope and metadata lookup are candidates); do not create a second media index or a search service.

When existing configured roots overlap, collapse identical normalized absolute path entries before group counts and selection; never count two views of one path as duplicates. Distinct hard-link paths can still appear as candidates; selected-byte totals are not a promise of reclaimed space. Different filesystem/share aliases may not be recognizable from cached paths alone; preflight deduplicates live identities where available and declines uncertain conflicting targets.

Ordinary name/size/date/type browsing must retain its current path without a mandatory metadata join. Duration sort/Details request saved metadata in bounded SQL work. Normal browsing loads no cleanup scripts and starts no new preparation job. Reading Library Cleanup creates no FFprobe, thumbnail-generation, or filesystem-enumeration work. Explicit actions necessarily consume share/CPU resources; performance isolation is a measured goal, not a zero-cost guarantee.

## Change surface

| Area | Required reach |
| --- | --- |
| `directory_index.py` | Additive schema migration, saved duration query helpers, cleanup scope/sort/group queries, Trash suppression, safe scan invalidation/publication |
| `app.py` | Read routes and bounded action/status endpoints, existing auth/CSRF integration, cached-only thumbnails, probe-result saving, duration browse option |
| Focused new modules | Trash journal/filesystem operations and bounded duration preparation; keep filesystem mutation code outside the route handlers |
| `templates/base.html`, `templates/browse.html`, `static/explorer.js`, `static/explorer.css` | Navigation/context link; Duration selector and Details value, stable URL behavior |
| New cleanup template/assets | Table, scope picker, pagination, page-local selection, dialogs, Trash tab, job status and independent thumbnail preference |
| All source-serving and preparation routes | Reserved Trash-path rejection, current authorization, active-work conflicts, exclusion of trashed items |
| Tests and fictional fixtures | SQL behavior, duration lifecycle, real temporary-file Trash operations, interruption/recovery, browser interactions |
| Config, docs, packaging | Generic opt-in setting, recovery/backup/security limitations, new modules in Docker/source bundles; verify PyInstaller asset/module inclusion |

Do not merge the sibling worktree, edit real share mappings, rebuild installers, publish, deploy, or commit as part of this design request. Update old documentation that promises no media writes when implementation introduces the explicitly enabled cleanup feature. Existing unrelated packaging omissions are separate findings, not implied fixed here.

## Acceptance and verification

1. Database fixtures demonstrate partial Unicode filename search, all scope modes, profile/exclusion isolation, supported file types, exact size grouping, overlapping roots, stable sort ties, and correct totals across more than 100 results. Search/group routes must pass with filesystem enumeration and FFprobe replaced by failing sentinels.
2. Known, unknown, failed, zero-length, invalid/nonfinite, changed, and replaced media have correct duration display/order in both directions. Saved metadata survives restart, stays isolated from snapshot replacement, and never leaks a stale duration after a version change. Probe results are discarded if the file changes during preparation.
3. Preparation tests establish one worker, bounded batches, stream-triggered pause, stop/retry/restart behavior, profile changes, offline errors, and no extra probe on ordinary page load/sort.
4. Temporary-file integration tests move to Trash, list, restore, and purge real fictional payloads. Fault injection at every journal/manifest/rename/completion boundary verifies reconciliation and retry. Include DB/cache loss with manifest recovery, collisions, missing parents, cross-device errors, permission failures, share disconnect, malformed records, and a failed purge.
5. Reject forged tokens, missing CSRF/login, disabled writes, forbidden profiles, root reordering, absolute/traversal/alternate-stream paths, symlink/junction ancestors and payloads, reserved-path roots, changed selection versions, and repeated/conflicting requests. Show no inaccessible metadata in job/error responses.
6. A scan deliberately held before publication cannot resurrect a trashed entry. Shared/overlapping roots and Favorites exclude it immediately; restore repopulates it after safe refresh. Active reads/probes/thumbnail jobs yield Busy instead of being terminated.
7. Browser checks cover keyboard/table semantics, focus restoration in dialogs, selection reset, exact byte inspection, mobile scrolling, cached-thumbnail toggle with zero image requests while off, partial batch failures, and duration sort/history in normal browsing. All examples use fictional media.
8. Compare existing browse/search latency and request counts before/after with a large synthetic index, both idle and during cleanup queries/preparation. Record actual measurements and plans; investigate a reproducible material regression rather than claim instantaneous behavior for all libraries. Packaged smoke checks establish included assets/modules only; real N100, SMB rename/locking/recovery, and device playback checks remain separate release acceptance.

## Proposed defaults and remaining builder choices

Proposed defaults: thumbnails off; page-local selection; exact-size matching only; no automatic purge; restore to the original existing parent without overwrite; explicit metadata preparation; one preparation worker; opt-in authenticated writes; Windows mutation support first. These are reviewable calls made to keep the first version bounded and recoverable.

The builder chooses internal class/module names, additive schema version, exact SQL/indexes from query plans, endpoint spelling, tested Windows filesystem primitives, job implementation consistent with existing runtime cleanup, and cosmetic details within the existing theme. These choices cannot weaken no-overwrite, current authorization, recovery, bounded work, or the separation of browsing from cleanup.

Before implementation, approve or revise this design. Then produce an implementation plan with independent safety and integration review. The design document and mockup alone do not enable server deletion.

## Design review evidence

A fresh read-only `gpt-6.1-sol` reviewer, requested at high reasoning effort, returned `nothing important left` after checking the written design for questions that would block an implementation plan. Local mockup verification covered partial filename filtering, exact-size groups, thumbnail display, simulated move/restore confirmations and results, and the ordinary-browse Duration selector. JavaScript syntax and new-file whitespace checks are separate from product testing; no real media was accessed or changed. The saved preview screenshot shows a fictional matching-size comparison with one copy selected.
