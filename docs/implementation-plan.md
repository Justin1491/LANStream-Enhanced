# LANStream Enhanced implementation plan

Goal: preserve folder-native playback while making large folders responsive.
Spec: ../LANStream_Enhanced_Project_Brief.md in the workspace; design.md here.
Stack: existing Flask/Pillow/PyMuPDF/Waitress plus Python's SQLite/unittest.

## Constraints and review focus

Preserve production C:\LANStream and port 8060; test only isolated data/config.
No SMB writes, enhanced-code pushes or upstream PRs. Commit milestones locally.
Review profile changes and excluded descendants before pagination; junctions
and encoded traversal; blocked SMB workers; stale refresh/version changes;
favorites after root reorder/removal and index rebuild. Verify playback command
compatibility and avoid any bulk startup scan or thumbnail generation.

- [ ] M0 — Audit and reproducible baseline: docs/design.md,
  scripts/benchmark.py, tests/test_benchmark.py. Generate 25,969 synthetic files
  outside the working source; measure upstream HTML size/items/time, directory
  enumeration, CPU and working set. Commit the harness and audit.
- [ ] M1 — directory_index.py and tests/test_directory_index.py: tests first
  for snapshot publication, persistence, deduplication, stale/offline snapshots,
  Unicode natural sorting, exclusions before count/page and traversal/link
  refusal. DirectoryIndex.listing schedules scans and returns bounded pages.
  Integrate browse/API authorization in app.py with route regression tests.
- [ ] M2 — templates/browse.html, static/explorer.js/css: 100-item pages,
  server filter/sort, Grid/List/Details, density, saved device preferences,
  breadcrumbs, loading/stale/offline states. Test rendered desktop/mobile
  controls, DOM bounds and back/forward navigation. Re-run benchmark.
- [ ] M3 — image_scheduler.py and tests: persistent bounded priority queue,
  one worker, source freshness, failure backoff, pause/resume and stream priority.
  Integrate with existing generator and cache paths; tests prove navigation
  retains work and stale jobs cannot publish a replacement file's old image.
- [ ] M4 — global search and profile-scoped favorites plus explicit recursive
  indexing controls. Test Unicode substring search, all/current-folder/subtree
  scopes, restart persistence, profile isolation and offline/missing results.
  Benchmark search p95 and document coverage limits.
- [ ] M5 — update README/manual, safe config example, Windows build/deploy
  checklist and measured report. Verify Python tests, browser interactions,
  media byte ranges and encoder commands. Build when tools are available;
  otherwise give exact build steps and target-only verification commands.
- [ ] Final read-only Sol 6.1 review of actual diff, fixes with regressions,
  clean diff checks and local milestone commits. No push.
