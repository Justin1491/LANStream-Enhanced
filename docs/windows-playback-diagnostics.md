# Windows HLS startup diagnostics

This diagnostic build addresses a startup failure mode and records the evidence
needed to identify an encoder or publication failure. It has not established the
cause of the previously observed Windows 20-second playlist 503. Mac software
playback is separate evidence. No software encoding comparison is required.

## Run the reviewed diagnostic build

1. Download the `LANStream-Windows-QSV-Diagnostics` artifact from the task branch's
   **Windows playback diagnostics** Actions run. Extract it into a new folder,
   outside `C:\LANStream-Enhanced`. Keep the entire `_internal` folder beside the
   executable. `BUILD-IDENTITY.json` records the source commit and executable hash.
2. Close the running **Enhanced** app on port 8061. Keep the original production
   app on port 8060 as it is.
3. Double-click **Run QSV Diagnostics.cmd** in the extracted folder. It reads the
   existing `C:\LANStream-Enhanced\config.txt`, uses the existing media tools through
   its process PATH, and writes only to a separate `diagnostic-cache` in this new
   folder. It explicitly selects QSV, including the availability check; it does
   not test or fall back to software encoding. It does not modify VPN/firewall
   settings, stop processes, overwrite the install, or upload data.
4. Sign in locally, open the same selected clip on port 8061, leave **Normal**
   and the same audio track selected, and tap **Play** if prompted.

Close this diagnostic app and relaunch the installed application to return to
the previous build. The diagnostic cache contains private application data;
keep it on the local machine. If the existing install is in another folder,
edit only `existingInstall` in the launcher before running it.

## What changed

Safari first polls an authenticated readiness response for the same HLS stream
generation, once per second, for at most 90 seconds. It loads the media playlist
only after the playlist, init and a media segment have been published. The old
sequence exposed an unready playlist directly to Safari and returned a generic
503 after 20 seconds; Safari then surfaced error 4. The legacy playlist route
remains available, with the existing timeout, for older clients.

**Playback details** on the player reports:

- `state`, startup `phase`, backend, elapsed seconds, running/exit status;
- completed FFmpeg progress updates: frames, output seconds and speed;
- published playlist/init sizes and current segment count;
- a bounded error excerpt with the known input/output paths redacted and other
  path-containing lines omitted;
- executable/source fingerprint, server-instance ID, platform and browser state.

The report does not include the command, media filename/URL, configuration,
credentials or cookies. Reports require the same login and source access as
playback. Only that owned stream's temporary progress/error files are read.
FFmpeg progress uses its documented [key/value progress protocol](https://ffmpeg.org/ffmpeg.html#toc-Main-options),
and is removed with that stream's existing cache cleanup.

The launcher prints the same build and instance ID after binding successfully.
Windows listeners use `SO_EXCLUSIVEADDRUSE`: the Waitress default enables
`SO_REUSEADDR`, which on Windows can allow overlapping listeners with
[indeterminate request routing](https://learn.microsoft.com/en-us/windows/win32/winsock/using-so-reuseaddr-and-so-exclusiveaddruse).
This is a possible explanation of differing local/LAN status, not proof that
multiple instances caused the reported playlist failure.

## Interpret the first failure

| Phase | Evidence to inspect |
| --- | --- |
| `process_start` | Failure to create/own the encoder or cache; OS error numbers are included. |
| `first_frame` | Encoder still running but no completed frame progress; inspect its bounded error excerpt. |
| `first_segment` | Frames exist, but the first segment has not been published. |
| `playlist_publication` | Segments exist, but no complete playlist/init combination is ready. |
| `encoder_exit` | Exit code and error excerpt distinguish encoder failures from missing outputs after a clean exit. |
| `ready` | Publication succeeded; a subsequent browser error belongs to playlist/segment retrieval or decoding. |

The Windows workflow checks real subprocess ownership, exclusive listening,
delayed publication, access scope and player behavior with fictional fixtures.
It builds the Windows executable without executing FFmpeg. It does not certify
Intel hardware, this private input, or physical iPhone playback.
