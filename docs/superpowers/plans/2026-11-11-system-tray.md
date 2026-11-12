# LANStream System Tray Implementation Plan

**Goal:** Implement the user's approved Windows tray/status-window plan.

**Architecture:** A windowless desktop entry point owns Tk on the main thread,
captures output before application import, and runs the shared launcher in a
worker. Tray callbacks enqueue commands. The console entry point stays usable.

**Constraints:** Windows desktop only; 1 MiB recent output; X/minimize hide;
tray Exit stops the server; preserve redirected installer diagnostics and
Linux behavior. No commit, push, startup registration, or live deployment.

## Tasks

- [x] Add regression tests for bounded output, window show/hide and tray failure,
  startup errors, cancellation before/after binding, and releasing a live port.
- [x] Add optional stop/readiness hooks to `launcher.main`, with a private
  Waitress socket map for cooperative desktop shutdown and existing cleanup.
- [x] Add `desktop_launcher.py`: bounded capture, inherited Windows output
  handles, Tk status window, queued tray commands, and guarded shutdown.
- [x] Suppress Windows media-tool consoles (delegated: app.py, hls_streams.py,
  related tests); retain all existing input/output and error behavior.
- [x] Update windowless packaging, icon, Windows dependency and usage/license
  documentation; verify focused tests, full suite, packaged GUI and upgrades.
- [x] Obtain an independent read-only review and resolve material findings.

## Review focus

Exit during imports/probing/binding must never start a later listener. Hidden
logs and GUI work queues must stay bounded. Tray failure must leave an exit.
Inherited installer pipes must still receive startup/errors. Shutdown must not
race durable cleanup or leave an owned encoder or listening port behind.

## Execution notes

- The user explicitly approved implementation of the conversation plan; no
  additional design approval is needed. The user subsequently authorized
  committing and pushing the completed changes.
- Use Tk's main-thread event loop; no GUI access from server/tray threads.
- Existing installer launches should also show the tray icon, with the same
  redirected diagnostics. Do not introduce an unattended-only default.
- Review resolved three shutdown gaps: direct MP4 encoders share the existing
  Windows job; tray startup runs on an owned daemon thread; Exit waits for
  backpressured HTTP workers, thumbnail preparation/workers and durable Trash
  work before removing the UI. Slow filesystem calls can delay Exit.
- Output is transferred through a locked 1 MiB buffer instead of an unbounded
  log queue; tray/lifecycle commands use a separate thread-safe queue.

## Verification evidence

- Python suite: 255 tests, OK, 2 opt-in packaged tests skipped in discovery;
  both packaged startup and real upgrade/rollback passed separately.
- JavaScript: all 40 player/explorer/cleanup tests passed.
- Final affected route checks: 24 tests passed; process-checker safety checks
  passed using the project's process-scoped RemoteSigned test invocation.
- Windowless PyInstaller build passed; PE subsystem and redirected startup
  diagnostics verified. Real pystray default/menu callbacks showed the Tk
  window, X hid it, and Exit requested shutdown.
- Independent gpt-6.1-sol/high re-review found no remaining material issues.
- Local upgrade bundle: artifacts/tray-release/LANStream-Enhanced-Upgrade.zip.
  Generated bundles remain local and are excluded from Git. No installed server
  was changed.
- Physical taskbar mouse clicks and real media/hardware playback were not
  checked on this test desktop. Isolated test instances were stopped.
