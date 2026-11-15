# Automatic thumbnails and settings implementation plan

**Goal:** Prepare cached previews automatically while nobody uses LANStream, and show preparation, indexing, software update and disk usage status on Settings.

**Architecture:** Reuse the directory index and bounded image scheduler. A single idle feeder adds lowest-priority work; server activity and client activity heartbeats pause it. Settings reads in-memory status, with explicit background storage scans and update checks.

**Authorized design:** User approved automatic preparation on 2026-11-14 and defined idle as LANStream inactivity. Use 60 seconds, one extraction, existing profile/exclusion/Trash protections, cached JPEGs retained across restarts. Software updates are checked only, never installed. No commits or deployment requested.

## Work

- [x] Scheduler and indexed recursive candidate feed: test idle, stream protection, one background job, foreground promotion, backpressure, cache reuse and exclusions; implement without changing manual preparation semantics.
- [x] Settings support: bounded background disk scan of cache and application files, excluding media and reparse points; honest software revision status from existing build identity; accessible page, explicit actions, cheap polling and failure states. Unit-test storage and mocked network handling.
- [x] App integration: config default and durable enable switch; authenticated and CSRF-protected routes; actual client activity without counting status polls; cancellable automatic FFprobe/FFmpeg/7-Zip with a total 30-second budget; shared navigation and standalone player heartbeat. Test request validation, activity, privacy and process cancellation.
- [x] Verify focused tests, existing Python/JavaScript suites, rendered desktop/mobile Settings and controls where available; independent read-only review; resolve material findings and document actual limitations.

## Review focus

Buffered playback and reading must count as use; routine polling must not. A queued idle job must not start after new activity. Config edits must preserve passwords/profiles/exclusions and fail without changing runtime state. Storage must never follow media aliases or double-count cache. Update status must distinguish unknown, unreachable and differing revisions.

## Decisions

Keep current checkout (initially clean), no dependency additions, no installer changes. Retain existing authentication model; status exposes category names and byte totals, not private paths or credentials. Completed JPEGs provide restart continuation; no separate durable thumbnail database. Automatic generation may leave an in-flight nonvideo library decode to finish safely; subprocess work is cancelled promptly.

## Verification

Build environment Python suite: 286 tests, 284 passed and 2 opt-in checks skipped. Node suite: 61 passed. Final marked-poll route regression and explorer tests passed after the full run. Isolated real nested-image preparation succeeded. Browser checks verified storage totals, persisted enable switch, error handling, no console warnings/errors, and no horizontal overflow at 390px. Independent review found a CBR cancellation gap; regression reproduced it, fix passed extraction/process tests, and follow-up review found no material issues. Live GitHub request returned an error (the UI showed that error correctly); successful response handling was tested with mocked HTTP. No package build, deployment, physical N100 or real SMB measurement performed.
