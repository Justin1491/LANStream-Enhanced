# Check and stop stale N100 instances

Copy `scripts/Check-LANStreamProcesses.ps1` to the Windows N100 (for example,
Downloads). It is also included in the Windows playback diagnostics artifact.
Run it locally in **PowerShell as Administrator** for complete process paths
and permission to stop hidden instances owned by another account. Windows
PowerShell 5.1 is supported; Python and additional modules are not required.

From the folder containing the script:

```powershell
Unblock-File -LiteralPath .\Check-LANStreamProcesses.ps1
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\Check-LANStreamProcesses.ps1
```

This execution policy applies only to the new PowerShell process. If an
organization policy blocks scripts, use the approved administrator process.

The script prints every listener on **8061**, plus all `LANStream.exe`
processes, including hidden or non-listening instances. Each entry shows its
PID, parent PID, start time, executable path, listening ports, configured port,
Windows file/product versions, SHA-256 fingerprint and source commit if a
matching `BUILD-IDENTITY.json` is present. It does not print config contents or
command lines, request passwords, or make HTTP requests.

File/product versions may describe the bundled Python runtime. The SHA-256 is
the useful build identifier for a packaged EXE. These values describe the file
on disk, not a measurement of already-loaded code; the script warns if the file
was modified after process start. For current builds, sign in to
`http://127.0.0.1:8061/transcoder-status` to see the live **Build / Instance**
and compare it with the launcher's output. Older builds may lack this display.
A build fingerprint does not establish which build is newer.

For the clearest comparison, point at the EXE from the new build you intend to
use. This reads the file without launching it:

```powershell
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File .\Check-LANStreamProcesses.ps1 -ReferenceExecutable 'C:\Users\YourName\Downloads\New build\LANStream.exe'
```

Processes whose on-disk fingerprint matches the reference are marked **Keep**
and excluded from stopping. Different fingerprints mean different files, not
necessarily older files. Without a reference, all eligible builds are offered
for your selection, including the current build.

Enter a comma/space separated list of unwanted PIDs, or **ALL** to select every
eligible instance in the displayed inventory. Review the selected paths and
type **STOP** to terminate them. Enter cancels at either prompt. Use Ctrl+C
instead when a visible server console is available for an orderly shutdown.
Termination interrupts that instance's playback and background work.

Protection rules:

- `C:\LANStream` and its descendants are protected.
- Processes listening on any port other than 8061, including 8060 and the 8062
  diagnostic app, are protected.
- Unknown executables, Python servers and unreadable process identities are
  inspection only. Non-listening `LANStream.exe` instances require an adjacent
  config with exactly one `port=8061` setting before they can be selected.
- Each process path, start time, hash and current listeners are checked again
  immediately before stopping. A cached Windows process handle prevents a
  recycled numeric PID from targeting a replacement process.
- Only selected processes are terminated. Children such as FFmpeg are not
  stopped separately; no process-name-wide kill or process-tree kill is used.

The script waits three seconds, prints a fresh inventory, and warns if 8061 is
still occupied or a listener restarted. It never automatically stops a newly
appeared PID. Review Task Scheduler's Actions, Services, and Startup apps for
launchers pointing to an old folder if a process returns. This short observation
does not rule out a later automatic restart. No startup settings are changed.

For inspection without prompts, add `-ReportOnly`. For a dry run showing which
eligible PIDs would be targeted, add `-WhatIf`. Neither mode stops anything.
Failure to query processes/listeners aborts the check. A process that changes
identity or becomes protected is skipped with an error and a nonzero exit code;
other confirmed selections can still be stopped. Exit zero means inspection
completed or selected stops succeeded, not that 8061 must be free (you may have
kept an instance or an automatic restart may have occurred).

Tests: `powershell.exe -NoProfile -File tests\process_check_tests.ps1` exercises
selection and stop guards. On Windows PowerShell 5.1 it also compiles, launches
and stops a disposable test executable in a temporary folder. It never stops
an installed app. Portable PowerShell exercises the simulated Windows boundary
and file-identity tests; it cannot validate native Windows process behavior.
