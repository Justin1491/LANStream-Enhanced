# Library Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Implement the approved separate cleanup page, recoverable Trash, persistent duration preparation, and normal-browse duration sorting.

**Architecture:** Retain Flask, the existing SQLite directory snapshots and current frontend. Separate metadata/index queries, durable Trash filesystem operations, and bounded background preparation; connect through explicit callbacks so request handlers do not perform share operations. Cleanup assets load on their own page.

**Tech Stack:** Python standard library, existing Flask/itsdangerous, SQLite WAL, FFprobe, vanilla JavaScript/CSS.

**Spec:** `docs/superpowers/specs/2026-11-10-library-cleanup-design.md` (approved in chat 2026-11-10).

## Global Constraints

- Do not commit, publish, deploy, touch real media, or merge the sibling FFprobe worktree.
- At most 100 selected files; page-local selection; thumbnails off by default; no automatic purge.
- Reads use cached data only. Filesystem mutation uses a bounded worker, CSRF, current-profile authorization, version validation, and a same-storage no-overwrite move.
- Restore never overwrites or creates missing parent trees. Preserve manifests beside payloads and fail closed on uncertain recovery.
- Duration sorting never probes media. Unknown values remain last in both directions; folders remain first in ordinary browsing.
- Windows mutations first; opt-in `library_cleanup_enabled=false`, existing password authentication required.

## Review Focus

- Index publication races must not resurrect a trashed file; test a scan held across hide/unhide.
- Replaced selections, forged IDs, links/junctions, and reserved-path aliases must never mutate or reveal unrelated files.
- Interrupted move/restore/purge and cache loss must preserve data and never silently overwrite a replacement.
- Offline SMB calls and long grouping queries must not hold the shared index lock or block listing requests.
- Current authorization must apply to jobs, counts, Trash records and recovery, including root reorder and profile exclusions.

## File ownership and interfaces

Task 1 owns `directory_index.py` and `tests/test_cleanup_index.py`. Task 2 owns `library_trash.py` and `tests/test_library_trash.py`. Coordinator owns `cleanup_jobs.py`, `cleanup_routes.py`, `app.py`, all templates/assets, route/browser tests, docs/config and packaging. Preserve others' changes. No nested delegation.

### Task 1: Indexed cleanup and saved durations

**Interfaces:** `DirectoryIndex.cleanup(roots, *, query='', excluded=None, scope_root=None, scope_relative='', recursive=True, page=1, page_size=100, sort='name', descending=False, file_type='all', matching_sizes=False)` returns the standard paginated shape plus `duration`, `metadata_status`, snapshot `version`, and `size_count` per file. `save_duration(root, relative, fingerprint, duration, error='')` accepts a dictionary containing `size, mtime, mtime_ns, ctime_ns, ino, dev`; `duration_candidates(...)` yields bounded candidate dictionaries under the same authorized scope, with `retry_failed=False`. `hide_file(root, relative, hidden=True)` invalidates pending scans and suppresses all known overlapping-root paths, including Favorites. `is_reserved_path(path)` rejects a `.lanstream-trash` component, case-insensitively. Ordinary `listing(..., include_duration=False)` joins metadata only for Details/duration sort.

- [x] Write meaningful tests for duration persistence/invalidation, full-result sort and exact-size grouping, scope/exclusions, aliases, old snapshots/reserved paths, and stale-scan suppression. Run to establish missing behavior.
- [x] Implement additive metadata/hidden-file schema and isolated read connection; reuse scope validation. Preserve existing signatures/default paths where possible.
- [x] Run `python -B -m unittest tests.test_directory_index tests.test_cleanup_index tests.test_automatic_indexing -q`; inspect results and final diff.

### Task 2: Recoverable Trash operations

**Interfaces:** `TrashManager(db_path, *, resolve, busy, changed, windows=None)` with `resolve(profile_id, root_id, relative)->Path` returning currently authorized configured root or raising; `busy(path)->bool`; `changed(root,relative,hidden)->None` coordinating index visibility. `submit(profile_id, action, items, operation_id)` returns a job; `status(profile_id, job_id=None)` only returns owned jobs; `listing(profile_id, roots, excluded, **filters)` returns paginated authorized journal rows. Supported actions: `preview_move`, `move`, `preview_restore`, `restore`, `preview_purge`, `purge`, `recover`. Preview captures live fingerprints and returns verified item tokens/IDs for confirmation. Expose worker `close()` and test-only idle wait. Precise payload schema is documented by worker before route integration.

- [x] Write temporary-file tests first for move/restore/purge, no-overwrite, stale attributes/identity, manifest recovery, fault boundaries, links, aliases, idempotency, disabled platform and unauthorized job access.
- [x] Implement separate durable `trash.sqlite3`, per-root lock, bounded single daemon worker, manifest and root marker, reserved-path validation, current auth before each action, and safe Windows filesystem primitives. No recursive purge, copy/delete fallback or destructive test against real media.
- [x] Verify fault recovery, reject uncertain cases, and run focused tests. Return documented schema and evidence.

### Task 3: Routes and bounded duration preparation

**Files:** create `cleanup_jobs.py`, `cleanup_routes.py`, `tests/test_cleanup_routes.py`; modify `app.py`.

- [x] Route tests first: cached cleanup queries, signed per-profile expiring snapshot tokens, CSRF and opt-in write guards, profile-safe jobs, reserved serving paths, and no probe on duration sorting.
- [x] Register a Flask blueprint with application helpers; use `URLSafeTimedSerializer` for snapshot selection tokens, no client absolute paths. Submit/preflight confirmations are asynchronous jobs. Validate 100-item cap, operation UUID, action and types.
- [x] Implement a single duration preparation worker with authorized indexed candidates, before/after live fingerprint checks, finite durations, pause/resume/stop/retry and stream signal. Save existing explicit video-detail/player probe results opportunistically without additional probing.
- [x] Exclude reserved paths centrally from media access and lexical cached context. Wire index hide callback and Busy checks for streams, thumbnails and probes. Keep network work outside request threads.
- [x] Run route/job tests and affected existing app tests.

### Task 4: Cleanup UI and browse duration

**Files:** create `templates/cleanup.html`, `static/cleanup.js`, `static/cleanup.css`, `tests/cleanup.test.cjs`; modify base/browse templates and explorer assets/tests.

- [x] Add focused JS coverage for selection resets/limits, confirmation payloads, background jobs and thumbnail toggle cancellation.
- [x] Implement page and Trash tab, indexed folder selection, scopes, search/category filters, sortable columns, size groups, exact bytes, independent cached-only thumbnails, accessible confirmations and per-file failure/job status.
- [x] Implement metadata controls, explicit current-folder thumbnail preparation and recovery. Add normal folder Duration sort/Details value, missing-duration link and context link into Cleanup. Preserve existing navigation preferences.
- [x] Run JS tests/syntax checks; render fictional server fixtures and check desktop/mobile actions, console errors and requests through browser tools.

### Task 5: Integration, documentation, review

- [x] Add generic config opt-in guidance and backup/Trash/recovery docs; include new modules in Docker/source packaging and verify Python/asset inclusion without rebuilding a release.
- [x] Run full Python and JS checks, meaningful large-index query timings and busy-worker read checks, final whitespace/status review.
- [x] Obtain read-only independent security/correctness review and resolve important issues with regression checks. Keep all product changes uncommitted in the feature worktree; report actual checks and real SMB/N100 limitations.

## Progress / rulings

- Approved implementation request supplies authorization to execute this plan without another planning approval round.
- Native managed worktree setup did not return; verified no new checkout then created the sibling Git worktree `library-cleanup` on `feature/library-cleanup` using the local fallback. No changes to source/main.
- Task reviews and final acceptance will use actual uncommitted diffs; commits are excluded by the user's Git instructions.

## Completion evidence

- Tasks 1–5 implemented in the feature checkout; no commits, merges, release builds, publication, deployment or real-media operations.
- Final focused run: `python -B -W error::ResourceWarning -m unittest tests.test_cleanup_routes tests.test_cleanup_jobs tests.test_library_trash tests.test_cleanup_index -q`: 63 tests passed. Native file operations used fictional Windows temporary files.
- Final index/automatic-indexing scope: 55 tests passed, one environment skip. Cleanup queries use independent WAL readers; synthetic 26,000-row normal listings measured about 18 ms with no hidden files and 43 ms with a hidden entry, versus an inspected original baseline around 8 ms. These are local fixtures, not N100/SMB benchmarks; ordinary listing SQL does not join duration unless requested.
- JavaScript: 40 tests passed; both frontend scripts passed syntax checks. New-file/working-diff whitespace checks, module AST syntax, packaging references/assets, and 17 relative README links passed.
- Full Python run before the last focused fixes: 224 tests, one existing `test_occupied_8061_is_preserved` failure and three skips, no remaining teardown errors. That installer failure was reproduced independently on unchanged source/main: the host's listener detection missed the synthetic listener and reached the deliberately invalid test executable. The unrelated test was not weakened or changed to mask it.
- Browser: actual isolated Flask fixture at `http://127.0.0.1:56629/cleanup`, fictional roots only; partial search, exact-size groups, cached thumbnails, confirmed native move and restore, return to indexed results, explicit duration preparation with automatic table refresh, normal Details/duration sorting, desktop and 390×844 mobile layout, and no relevant browser console warnings/errors. Viewport override reset. Screenshots and fixture helper live outside the Git checkout in `../cleanup-verification/`.
- Fresh read-only Astra/medium review initially identified three P2 issues, then a reused-path ownership edge case. Regression-backed fixes cover source verification before storage creation, malformed-manifest recovery, authorized failure identity, full-fingerprint/pending scan fencing, newer active Trash generation guards, and trusted restored-duration seeding. Final scoped review reported no important issues remaining.
- Journal/manifest backup and opt-in authenticated-write instructions are in `docs/library-cleanup.md`. Windows SMB lock/rename/disconnect/durability, target N100 load, actual device playback and rebuilt-artifact acceptance remain separate checks.

## Authorized release follow-through

The subsequent user request authorizes committing the complete feature, merging it into `main`, rebuilding Windows packages, and pushing `main` to `origin`.

- The earlier occupied-port test exposed missing listener enumeration on this Windows host. Added an exclusive IPv4/IPv6 bind preflight before new-install writes and before launch, retaining existing enumeration/ownership checks. The unchanged occupied-port test and new bound-socket/probe-release regressions pass; independent read-only review found no blocking issue.
- Full release suite: 235 Python tests passed with three expected platform/opt-in skips. All 40 JavaScript tests passed. An initial HLS test teardown hit a transient Windows log handle; the focused rerun and the full release rerun both passed. Direct installed media tools were selected for the release rerun.
- Rebuilt the frozen application with the existing build environment. Frozen startup/binary-identity smoke passed. A second frozen acceptance test verified the actual Cleanup assets, indexed size groups, saved duration sorting, native move/restore/purge, Favorites suppression/restoration, reuse of restored duration, and preservation of another fictional file.
- Packaging uses the rebuilt executable, reviewed tracked source, generic configuration with cleanup writes disabled, and existing installed media tools. Public update bundles preserve installed configuration/tools; first-install templates use a clearly generic replacement share. Release artifacts stay under ignored `artifacts/`, separate from source history and private setup mappings.
- These checks use fictional local media. Actual N100/SMB durability and physical-device acceptance remain separate; this request does not deploy to the running server.
