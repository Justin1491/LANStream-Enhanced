# Mac and iPhone playback diagnostics

Open this repository in Codex on the Mac and paste [MAC_CODEX_PROMPT.md](MAC_CODEX_PROMPT.md).
Use the `main` branch from `https://github.com/Justin1491/LANStream-Enhanced.git`.
For a clone, record `git rev-parse HEAD` before testing. An extracted source
snapshot has no Git history; use the handoff ZIP's `SOURCE-IDENTITY.json` instead.
The Windows executable cannot run on macOS.

The private handoff ZIP also contains a source snapshot and five video controls.
Extract it and open its `source` folder in Codex. Its media is already beside
these scripts. **The ZIP contains your actual video. Keep it private.**
The Git repository contains no test video. When cloning instead, privately copy
the ZIP's `source/tools/mac-playback/media/` folder into this directory.

## Comparison player

Python 3.10 or newer is required. From this directory:

```sh
python3 -B -m unittest discover -p 'test_*.py'
python3 serve_tests.py
```

Open `http://127.0.0.1:8765/` in **Safari**. Start one case at a time, observe
picture and sound, and download the results JSON. The test server uses only
Python's standard library and never uploads results. It serves only the test
page and fixed media filenames. It does not serve source code or configuration.

The controls compare H.264 and HEVC, ordinary fast-start MP4 and fragmented MP4,
byte-range delivery and chunked delivery, and the previous codec header.
Chunked controls intentionally ignore Range requests. They serve pre-generated
files, so they do not reproduce live encoder timing or production HLS.

## Actual application

Use an isolated virtual environment with the existing application dependencies.
Have `ffmpeg` and `ffprobe` available on PATH. Ask before installing missing tools
if the Mac's instructions require approval; do not change global Python packages.
From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/mac-playback/run_lanstream.py
```

Open `http://127.0.0.1:8766/` in Safari. The wrapper creates an isolated
`tools/mac-playback/work/` configuration/cache, exposes only the sample media,
disables automatic indexing, and selects software encoding. It leaves your
normal LANStream installation/configuration alone. Stop it with Ctrl+C.

Test Normal, Low Bandwidth, and the optional H.265 mode. Record `video.currentSrc`
and network requests: generated playback in native-HLS browsers should request
an `.m3u8` playlist, initialization MP4, and `.m4s` segments. Other browsers use
the `/video.mp4` endpoint. Compatible MP4 files can play directly in Normal mode.
Mac software encoding does not validate the Windows server's Intel QSV encoder.

## Physical iPhone

Use a trusted private LAN. Restart each test server with an explicit bind address:

```sh
python3 serve_tests.py --host MAC_LAN_IP
# In another terminal, from the repository root:
.venv/bin/python tools/mac-playback/run_lanstream.py --host MAC_LAN_IP
```

Replace `MAC_LAN_IP` with the Mac's local IP, then open that address with port
8765 or 8766 in iPhone Safari. These diagnostic servers have no password, so
stop them when finished. Do not forward ports or upload the kit publicly.

For a failing case, inspect iPhone Safari from the Mac using Apple's
[iOS inspection guide](https://developer.apple.com/documentation/safari-developer-tools/inspecting-ios)
and WebKit's [Web Inspector setup](https://webkit.org/web-inspector/enabling-web-inspector/).
Follow the guide for your installed OS versions; do not change Safari security
settings or automation permissions without asking.

## Evidence boundaries

- The user reports iPhone browser error 4; desktop playback works. The exact
  installed revision from that failed test is not yet confirmed.
- The old fixed H.264 Baseline codec header did not describe the observed High
  profile output. It was corrected, but its role in the iPhone failure is unproven.
- Windows Chromium and Windows Playwright WebKit played the H.264 comparison
  streams with either header. Neither is physical Safari acceptance.
- Windows Chromium advanced HEVC playback time with **zero video dimensions**.
  That is not successful video playback. Require a moving picture and nonzero
  dimensions; verify sound manually before claiming audio works.
- Current source already includes native HLS for generated Safari playback.
  Do not implement a second HLS system before testing this one.
- The provided original is a roughly 7.3-second, 1080x1920, 8-bit SDR HEVC Main
  MKV with stereo AAC. The earlier captured server response was a different clip.
- No successful Mac Safari or physical iPhone playback is established by this kit.

Record results in [RESULTS-TEMPLATE.md](RESULTS-TEMPLATE.md). Keep private media,
results, credentials, logs, and share mappings out of commits.
