# LANStream Enhanced measured status

October 18, 2026. Based on candre23/LANStream b837a21, branch
`feature/fast-smb-explorer`. User fork:
https://github.com/Justin1491/LANStream-Enhanced. Enhanced commits remain local;
no enhanced changes were pushed and no upstream PR was opened.

## Implemented and locally verified

Persistent atomic directory snapshots; deduplicated/background/host-bounded
scans; stale/offline responses and manual refresh; whole-folder sorting and
filtering; bounded 100-item List/Grid/Details pages; device view preferences;
64x40 List video previews; prioritized navigation-persistent thumbnails;
Unicode substring search with containing paths and coverage; explicit durable
recursive indexing with pause/resume; profile-scoped video/folder Favorites,
including details/player controls; local Windows packaging and guide.

The latest build adds All/Videos/Pictures/Documents filtering before counts,
sorting and pagination in browse, search and Favorites. Folders remain
navigable. The [N100 installer](n100-installer.md) verifies a matching release
manifest and ZIP, creates an isolated Enhanced test install, and runs bounded
acceptance checks without controlling production.

The source audit found the upstream Low Bandwidth toggle was ignored by its
backend. The shared encoder command now applies configured scale/bitrate,
retains aspect ratio and even dimensions, avoids upscaling, and uses 128 kbps
audio in that mode. Normal commands remain unchanged.

## Local benchmark evidence

These are development-Windows measurements, **not JSERVER/N100/SMB acceptance**.
Synthetic roots contain 25,969 immediate video filenames on a local filesystem.
Latency runs disable Python heap tracing. Upstream has 3 warm samples; Enhanced
has 20. OS caches and the development sandbox affect absolute timings.

| Measurement | Upstream | Enhanced |
|---|---:|---:|
| Cold HTML response | 91,407.965 ms (entire folder) | 23.399 ms (loading shell) |
| Cold completed first page | Included in full response above | 3,367.503 ms |
| Warm response p95 | 90,289.712 ms (entire-folder HTML) | 19.603 ms (100-item API) |
| Warm HTML shell p95 | Same full response | 3.125 ms |
| Search p95 | No global index | 49.609 ms |
| Response HTML bytes | 9,871,139 | 3,265, plus bounded JSON/assets |
| HTML markup elements | 129,868 | 64 before client rendering |
| Rendered item bound | 25,969 | 100 |
| Scans during measured Enhanced warm requests | Every upstream visit enumerates | 0; one cold scan total |

The user-supplied SMB enumeration baseline (25,969 entries in 9.81 seconds) is
a separate measurement and is not replaced by the synthetic results. Cold
SMB latency cannot be eliminated by caching. The local request speedup does
not predict an identical ratio on the N100.

Raw evidence: [upstream untraced](baseline-untraced.json),
[Enhanced untraced](enhanced-local.json), [initial upstream traced](baseline-local.json),
[Enhanced traced](enhanced-traced.json). The initial upstream run enabled heap
tracing; its timings must not be used as the untraced baseline. Traced peak
Python heap was 99.42 MiB upstream and 0.81 MiB Enhanced; native SQLite,
FFmpeg, OS and browser memory are not included. CPU totals cover different
numbers of requests and must not be interpreted as equivalent workloads.

## Verification

- Application suite: **59 tests passed, no skips**, including Windows junctions and
  symlinks, 0/1/100/25,969/100,000 entry cases, concurrent scans, stale/offline
  snapshots, interrupted publication, Unicode/natural ordering, exclusions,
  traversal, profile isolation, persistent favorites/crawls, corruption/schema
  refusal and thumbnail replacement/priority/backoff cases.
- Windows PowerShell 5.1 installer guardrails: **18 tests passed** for protected
  paths, existing installs, junctions, unsafe ZIPs, SHA256/manifest mismatches,
  occupied port 8061, bounded tool shutdown, template preservation/validation,
  password line separators and latency statistics. No test
  writes production data or controls its process.
- N100 setup bundle: **5 tests passed**, including matching payload/config hashes,
  rejection of modified installers, and the actual CMD launcher in a folder
  containing spaces, an ampersand and Unicode. Personal mappings are generated
  only in ignored release artifacts; the tracked config remains generic.
- Combined suite: **82 tests passed, no skips**. Actual ZIP installation,
  authenticated HTTP checks, inherited environment isolation and preservation
  of an unrelated LANStream process passed locally. Local QSV failure correctly
  fails acceptance and stops only the installer-owned server. The keep-running
  success branch was tested with QSV results explicitly simulated, not counted
  as N100 hardware acceptance. The mapped-template path also passed actual
  executable installation/login, preserving settings and exclusions while
  substituting the prompted password. See [installer evidence](n100-installer-local.json).
- Added **14 non-video viewer regressions** and exercised the source server and
  rebuilt portable executable in Chrome at 1440x1000 and 390x844. PNG/JPEG/TIFF,
  PDF, CBZ, genuine CBR through 7-Zip, UTF-8 text and Markdown passed navigation,
  page ordering/aspect/bounds, fullscreen, keyboard/swipe, folder return and
  literal HTML escaping. Corrupt-file cases returned deliberate 415 responses
  with readable viewer messages; there were no unexpected console/HTTP errors.
  Independent 844x390 and 1024x600 geometry checks also passed. Regressions fixed
  stale converted/page caches, simultaneous renderer publication, damaged
  document errors, leaked paths in text read errors, portrait clipping, mobile
  counters and missing document fullscreen exit controls. Missing 7-Zip has
  explicit installation guidance. See [viewer evidence](viewers-local.json).
- Chrome desktop 1440x1000 and mobile viewport 390x844: bounded 100 items,
  **1,164 DOM elements**, actual 64px List thumbnail, all views/density,
  pagination, full-folder filter, browser Back/Forward, search, Favorites, preference persistence,
  detail/player favorite and actual direct H.264 playback. No console errors
  or horizontal mobile overflow. Warm first render: **1,249 ms** in the final
  browser pass, including browser/network/render overhead.
- Real software FFmpeg Low Bandwidth encode succeeded on a synthetic two-second
  clip; output 320x180 (no upscale), 369,075 bytes. Byte-range/seek and QSV
  command construction are automated. See [encode evidence](encode-local.json).
- Sol 6.1/high read-only review found four lifecycle/priority issues; all were
  reproduced with failing regressions and fixed. Additional route checks fixed
  excluded folder-preview disclosure, cross-profile browser caching, JSON
  validation and SQLite handles left open after failed initialization.
- Existing Windows build workflow succeeded using Python 3.14.7,
  PyInstaller 6.22.3, FFmpeg 9.0.2 Gyan full build and 7-Zip 26.04. Tool binaries,
  temporary fixtures, caches and virtual environments are not committed.

## Remaining acceptance and deliberate limits

The portable package is a local private test build. Install separately at
`C:\LANStream-Enhanced`, port 8061, using the [operations guide](enhanced-guide.md).
No production install or media share was modified or used as a test fixture.

Still requires JSERVER: the three real UNC-root cold/warm benchmarks, N100 peak
RAM/CPU/network/transcoding FPS, **actual Intel QSV** encode and representative
HEVC/MKV seeking/audio/subtitle/Low Bandwidth playback, plus physical iPhone
Safari and UniFi Teleport cellular checks. Chrome mobile emulation does not
prove Safari compatibility. Docker source copying is updated; a Docker build
was not run. Non-video viewers passed locally using fictional fixtures; real
large/encrypted/unusual documents and physical Safari remain target checks.

Optional bulk thumbnail pre-generation is omitted. New/changed sources with
deliberately preserved timestamps can require manual removal of legacy cache
images after a restart. The guide documents this, blocked Windows filesystem
calls, partial index coverage, profile-password identity changes and safe
database recovery. Do not delete the whole cache: it holds durable Favorites.

The rebuilt standalone executable also passed an isolated loopback-port-8061
smoke test: home, 25,970-entry synthetic root (25,969 videos plus one folder),
100-item pages, indexed search, static assets, byte-range responses and real
software-encoder startup. See [portable evidence](portable-local.json) and
[browser evidence](browser-local.json). The test executable was stopped afterward.
