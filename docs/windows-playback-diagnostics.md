# Windows HLS startup diagnostics

Windows QSV diagnostics reproduced successful encoding with no published segment
until about 42 seconds. The first playlist contained a 42.042-second segment,
despite the requested two-second HLS interval. That exceeds the older client's
20-second playlist timeout. Requesting IDR frames at the scheduled QSV segment
boundaries reduced first publication to 2.5 seconds on the same Windows hardware.
Normal and Low Bandwidth playback were verified in Mac Safari and manually on a
physical iPhone. No additional software encoding comparison was performed.

## Verified Windows result — October 21, 2026

The tested executable was built from `7f1be19` in
[Windows run 39593003827](https://github.com/Justin1491/LANStream-Enhanced/actions/runs/39593003827).
Its SHA-256 was verified against the running authenticated server report:
`39e63cc9383ccf9b09ef04a7850d18e9c788d132c96eb8ede7b964683ccc94f8`.
The selected backend remained QSV, using the same representative input and audio
track as the baseline.

| Trial | First publication | First segment | Mac Safari result |
| --- | --- | --- | --- |
| Before the IDR change, Normal | 42.4–43.1 s | 42.042 s | Exceeds the legacy 20 s startup timeout. |
| Updated Normal | 2.5 s | 2.002 s | Advancing 1080×1920 video, no media error. |
| Updated Low Bandwidth | 5.0 s | 2.002 s | Advancing 270×480 video, no media error. |
| Updated Low, seek forward 30 s | 4.8 s | 2.002 s | Playback resumes at the requested position. |

All updated first playlists returned HTTP 200 with target duration 2. The user
confirmed moving picture and sound in both quality modes on their physical
iPhone. These observations establish the fix for this Windows QSV setup and
representative input; they do not cover every encoder, input or device.
Private media and detailed browser reports remain local and are not in Git.

## Run the reviewed diagnostic build

1. Download the `LANStream-Windows-QSV-Diagnostics` artifact from the task branch's
   **Windows playback diagnostics** Actions run. Extract it into a new folder,
   outside `C:\LANStream-Enhanced`. Keep the entire `_internal` folder beside the
   executable. `BUILD-IDENTITY.json` records the source commit and executable hash.
2. In Windows ZIP Properties, select **Unblock** if shown, then extract into a
   fresh folder. Previously extracted DLLs can retain the download marker.
3. Double-click **Run QSV Diagnostics.cmd** in the extracted folder. It reads the
   existing `C:\LANStream-Enhanced\config.txt`, uses the existing media tools through
   its process PATH, and writes only to a separate `diagnostic-cache` in this new
   folder. It explicitly selects QSV, including the availability check; it does
   not test or fall back to software encoding. It does not modify VPN/firewall
   settings, stop processes, overwrite the install, or upload data.
4. Sign in locally, open the same selected clip on port **8062**, leave **Normal**
   and the same audio track selected, and tap **Play** if prompted.

The diagnostic app runs beside the existing apps on ports 8060/8061. Close its
window to stop it. The diagnostic cache contains private application data;
keep it on the local machine. If the existing install is in another folder,
edit only `existingInstall` in the launcher before running it.

## What changed

QSV HLS now uses `-forced_idr 1` alongside the existing two-second
`-force_key_frames` schedule, in both Normal and Low Bandwidth. QSV otherwise
defaults to forcing ordinary I frames. HLS needs random-access keyframes to cut
segments; FFmpeg marks H.264 QSV IDR frames as key packets. See the
[QSV encoder options](https://www.ffmpeg.org/ffmpeg-codecs.html#QSV-Encoders)
and [QSV packet flags](https://ffmpeg.org/doxygen/9.0/qsvenc_8c_source.html).

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

The workflow also launches the actual frozen Windows executable with a private
loopback port and an empty fictional config, then checks its HTTP status and
binary identity. No media tools are available in that launch. This catches
missing runtime files/imports; it cannot reproduce a local Windows downloaded-file
or application-control policy. An access-denied DLL load happens before app
startup and should be distinguished from the HLS encoder report.
