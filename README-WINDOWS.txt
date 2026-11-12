LANStream Enhanced for Windows
=====================

LANStream runs as a normal Windows application. Docker and Python are not
required after the package has been built.

Expected release folder:

LANStream\
    LANStream.exe
    config.txt
    ffmpeg.exe
    ffprobe.exe
    cache\
    _internal\

Enhanced test installation
--------------------------
Extract to C:\LANStream-Enhanced and use port 8061. Do not replace C:\LANStream
or its config/cache on port 8060. See docs/enhanced-guide.md and docs/status.md.
The cache contains durable Favorites and the Library Cleanup Trash journal;
back it up before upgrading. Also back up each library's .lanstream-trash folder.
See docs/library-cleanup.md for opt-in file changes, saved durations and recovery.

Routine updates
---------------
If an old copy may still be using 8061, run scripts\Check-LANStreamProcesses.ps1
on the N100. It reports paths/build fingerprints and lets you select unwanted
instances to stop, with protection for port 8060 and the original installation.
See docs/n100-process-check.md for instructions and reference-build comparison.

Extract the latest N100 setup ZIP and run Upgrade Enhanced.cmd. It preserves
the existing config.txt and complete cache, retains a dated backup of the old
installation, and restores it if the new app fails to start. Install Enhanced.cmd
is for a first installation. See docs/n100-installer.md for custom folders and
backup/recovery details. Never replace your saved config with the setup template.

Configuration
-------------
Edit config.txt beside LANStream.exe.

Examples:

port=8061
use_master_password=false
transcoder=auto

D:\Movies
D:\TV
\\NAS\Media\Home Videos

UNC network paths are supported as long as the Windows account running
LANStream has permission to read them.

Starting LANStream
------------------
Double-click LANStream.exe.

LANStream starts as an icon in the system tray beside the clock. Windows may
place it in the tray's hidden-icons menu. Click the icon (or right-click and
choose Show status) to open a scrollable status window displaying:
- localhost URL
- LAN URL
- selected transcoder
- build, instance, process ID and live application output

Minimize or close the status window to hide it; LANStream keeps serving media.
To stop the server, right-click its tray icon and choose Exit LANStream.
Exit shows a stopping state until active requests, thumbnails and library
cleanup operations finish. A slow or unavailable share can delay shutdown.
The status window retains the most recent 1 MiB of output. Startup errors open
the window automatically. If the tray is unavailable, the window remains
accessible and closing it stops LANStream.

This does not configure LANStream to start automatically with Windows.
For a Python console launch, run python launcher.py as before.

Windows may ask whether LANStream may communicate through Windows Firewall.
For normal home use, allow it on Private networks.

Transcoding
-----------
With transcoder=auto LANStream tries, in order:
1. Intel QSV on Windows
2. NVIDIA NVENC on Windows
3. AMD AMF on Windows
4. software libx264 fallback

The included FFmpeg build must provide the encoder being used. The
/transcoder-status page shows each backend test and which one was selected.

Building
--------
1. Install Python 3 on Windows.
2. Put ffmpeg.exe and ffprobe.exe in:
       tools\
3. Double-click:
       build_windows.bat
4. The distributable folder will be:
       dist\LANStream\

PyInstaller creates a one-folder distribution. End users do not need Python.


NVENC driver / FFmpeg compatibility
-----------------------------------
NVENC has an API-version relationship between the FFmpeg build and the NVIDIA
driver. A very new FFmpeg may require a newer NVIDIA driver even though the GPU
itself supports NVENC.

If /transcoder-status reports something such as:

    Required NVENC API: 13.1
    Found: 13.0

you have two choices:

1. Upgrade the NVIDIA driver to one which provides the API required by that
   FFmpeg build.

2. Put an older FFmpeg build which works with the installed driver in the
   tools folder as:

       ffmpeg-nvenc.exe

   then rebuild LANStream.

LANStream tests three NVENC option profiles (modern, legacy-compatible, and
minimal) against both the normal ffmpeg.exe and optional ffmpeg-nvenc.exe. The
first combination which actually encodes a test frame is used for real streams.
The selected profile and FFmpeg executable are displayed on
/transcoder-status.
