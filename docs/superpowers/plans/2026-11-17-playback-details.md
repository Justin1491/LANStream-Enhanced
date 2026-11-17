# Playback and details implementation plan

**Goal:** Simplify playback controls, save an autoplay preference, and offer confirmed recoverable deletion from video details with touch-friendly controls.

**Architecture:** Reuse the Flask settings API/config writer and signed Cleanup selection/preview/move jobs. Keep existing authentication, CSRF, changed-file and busy-file checks. No new dependencies.

**Approved design:** Audio appears only for multiple tracks; play/pause use accessible icons; server-wide autoplay defaults on; Delete confirms the filename, moves to recoverable Trash, then returns to the folder. Buttons and clickable labels have targets at least 44 by 44 CSS pixels.

**Scope:** `app.py`, player/video/settings templates, `static/settings.js`, `static/style.css`, new detail deletion script, existing player/settings/Cleanup tests, new deletion UI tests, generic config and guide. Preserve unrelated changes in directory indexing and Rename.

- [x] Add regression tests for autoplay off/on, icon labels, saved settings and details deletion selection.
- [x] Observe failures, then implement the settings/player changes and existing Trash flow.
- [x] Test confirmation cancellation, error handling, signed-token deletion and return navigation.
- [x] Run focused and full existing test suites; inspect desktop and narrow touch layouts using fictional media.
- [x] Obtain independent read-only review of the changed code and verification evidence; resolve important findings.

**Review focus:** Autoplay blocking still permits tapping Play; disabled autoplay starts no encoder; hidden single audio retains the default stream; deletion requires server-issued confirmation tokens; failed/busy/changed files stay available; mobile controls do not clip or require hover. No commit, release build, push or deployment is authorized.

**Evidence:** 311 Python tests OK (2 skipped), 92 JavaScript tests passed. Installed Playwright/Chromium checked touch taps and target sizes at 1366x900, 390x844, 320x640 and 844x390. Autoplay-off left media unloaded; saved autoplay-on survived reload and browser blocking recovered with Play. Delete cancellation retained generated media; confirmed deletion returned to the folder and retained the file in recoverable Trash. No browser console/page errors. Temporary evidence: `C:/Users/Jman2/AppData/Local/Temp/lanstream-playback-20261117/verification.json`. Physical devices, packaged release and deployment remain unchecked.

**Independent review:** gpt-6.1-sol/high read the player, deletion script, detail gating/token rendering, settings persistence/API, touched control CSS, and existing Cleanup confirmation/busy/fingerprint chain directly after sandbox Git access failed. No actionable findings. Reviewer did not rerun tests or inspect packaging, docs or test diffs; the coordinator verified those changes and the test/browser evidence. The fixture server was stopped after verification.
