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
The cache now contains durable Favorites; back it up before upgrading.

Routine updates
---------------
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

A console window remains open and displays:
- localhost URL
- LAN URL
- selected transcoder

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
