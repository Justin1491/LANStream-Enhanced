# LANStream Enhanced User Manual

LANStream is a lightweight, folder-native media browser and player for local networks.

This manual covers installation, configuration, everyday use, transcoding, Low Bandwidth mode, authentication, troubleshooting, and maintenance for both Windows and Linux/Docker installations.

For all Enhanced-specific installation, behavior, safety, packaging and acceptance steps, start with the [Enhanced operations guide](docs/enhanced-guide.md). Existing playback/configuration details below remain applicable unless the guide states a changed default. Enhanced defaults to port 8061.

---

## Table of Contents

1. [How LANStream Works](#how-lanstream-works)
2. [Supported Platforms](#supported-platforms)
3. [Windows Installation](#windows-installation)
4. [Linux / Docker Installation](#linux--docker-installation)
5. [Opening LANStream](#opening-lanstream)
6. [The config.txt File](#the-configtxt-file)
7. [Adding Media Folders](#adding-media-folders)
8. [Authentication and Password Profiles](#authentication-and-password-profiles)
9. [Browsing Media](#browsing-media)
10. [Playing Video](#playing-video)
11. [Direct Playback and Transcoding](#direct-playback-and-transcoding)
12. [Low Bandwidth Mode](#low-bandwidth-mode)
13. [Audio Tracks and Subtitles](#audio-tracks-and-subtitles)
14. [Thumbnail and Preview Cache](#thumbnail-and-preview-cache)
15. [Transcoder Status Page](#transcoder-status-page)
16. [Hardware Acceleration](#hardware-acceleration)
17. [Changing the Listening Port](#changing-the-listening-port)
18. [Updating LANStream](#updating-lanstream)
19. [Backing Up LANStream](#backing-up-lanstream)
20. [Troubleshooting](#troubleshooting)
21. [Security Notes](#security-notes)
22. [Supported Video File Extensions](#supported-video-file-extensions)
23. [Licensing](#licensing)

---

# How LANStream Works

Enhanced maintains local directory snapshots and profile-scoped favorites in cache/index.sqlite3. Media files remain in their original folders.

Instead, it reads your existing folder structure when you browse it. If your files are arranged like this:

```text
Movies/
    Alien/
        Alien.mkv
    Blade Runner/
        Blade Runner.mkv
TV/
    The Expanse/
        Season 01/
        Season 02/
Home Videos/
```

LANStream presents those same folders in the browser.

There is no library import, metadata matching, file renaming, or initial scan required.

LANStream performs three main jobs:

1. Displays your existing folders and video files in a browser.
2. Sends browser-compatible files directly to the client whenever possible.
3. Uses FFmpeg to transcode files that the browser cannot play directly.

Media files themselves are never intentionally modified.

---

# Supported Platforms

## Windows server

LANStream 1.0.0 supports 64-bit Windows systems.

The Windows release is intended to be portable. A typical release folder contains:

```text
LANStream/
    LANStream.exe
    config.txt
    ffmpeg.exe
    ffprobe.exe
    cache/
    _internal/
```

No normal installer is required.

## Linux server

LANStream can run in Docker on Linux.

The recommended deployment keeps the following outside the container:

```text
config.txt
cache/
```

Media folders are mounted read-only.

## Client devices

LANStream is accessed through a modern web browser.

Typical clients include:

- Windows PCs
- Linux PCs
- macOS systems
- tablets
- phones
- other devices with a reasonably modern HTML5-capable browser

Playback capability depends partly on the browser. If a file cannot be played directly, LANStream attempts to transcode it to browser-compatible H.264/AAC video.

---

# Windows Installation

## Using the prebuilt Windows release

The normal Windows release is distributed as a ZIP archive containing the complete portable LANStream folder.

### Step 1: Extract the ZIP

Extract the complete archive to a permanent location.

For example:

```text
C:\LANStream\
```

or:

```text
D:\Applications\LANStream\
```

Do not run `LANStream.exe` directly from inside the ZIP archive.

## Step 2: Edit config.txt

Open:

```text
config.txt
```

in Notepad or another plain-text editor.

At minimum, add one or more media paths below the settings.

Example:

```text
port=8060
use_master_password=false
master_password=
transcoder=auto
low_bandwidth_resolution=854x480
low_bandwidth_bitrate=2M

D:\Movies
D:\TV
E:\Home Videos
```

See [The config.txt File](#the-configtxt-file) for full configuration instructions.

## Step 3: Start LANStream

Double-click:

```text
LANStream.exe
```

A console window opens.

The console displays information including:

- LANStream version
- local URL
- LAN URL
- selected transcoder

Keep this window open while using LANStream.

Closing it stops the server.

## Step 4: Windows Firewall

The first time LANStream accepts network connections, Windows Firewall may ask whether to allow access.

For normal home LAN use, allow LANStream on:

```text
Private networks
```

Public-network access is generally unnecessary.

## Step 5: Connect from another device

From another computer or device on the same network, open the LAN URL shown in the LANStream console.

For example:

```text
http://192.168.1.50:8060
```

The exact IP address will depend on your computer and network.

---

# Linux / Docker Installation

LANStream is designed to work well as a Docker container.

The published Linux Easy Config preset can automate the normal Docker deployment.

## Recommended host layout

Keep editable and persistent data outside the container.

A typical layout is:

```text
/opt/lanstream/
    config.txt
    cache/
```

The application container can then be replaced or rebuilt without losing configuration or thumbnail cache files.

Your actual media may be located elsewhere, for example:

```text
/mnt/media/
/mnt/media/Movies/
/mnt/media/TV/
```

## config.txt remains editable on the host

LANStream's Docker deployment bind-mounts the host configuration file into the container.

For example:

```text
/opt/lanstream/config.txt
```

may be mounted inside the container as:

```text
/config.txt
```

The container reads it, but the file itself remains an ordinary Linux file on the host.

You can edit it using any normal text editor:

```bash
nano /opt/lanstream/config.txt
```

or:

```bash
vim /opt/lanstream/config.txt
```

Media path changes are read when LANStream pages are loaded. Server-level settings such as the listening port or transcoder selection should be followed by a container restart.

## Cache directory

The cache directory should be writable by the container.

For example:

```text
/opt/lanstream/cache/
```

The Enhanced cache contains generated images, SQLite directory snapshots, the crawl frontier and durable favorites. Back it up; do not delete the entire cache to clear thumbnails.

## Media mounts

Media should normally be mounted read-only.

Example Docker volume concept:

```yaml
volumes:
  - /opt/lanstream/config.txt:/config.txt:ro
  - /opt/lanstream/cache:/cache
  - /mnt/media:/mnt/media:ro
```

If `config.txt` contains:

```text
/mnt/media/Movies
/mnt/media/TV
```

those same paths must exist inside the container.

## NVIDIA Docker support

To use NVIDIA NVENC inside Docker:

1. Install the NVIDIA driver on the Linux host.
2. Install NVIDIA Container Toolkit.
3. Configure Docker to expose the NVIDIA GPU.
4. Enable NVIDIA GPU support in the LANStream LEC preset.

LANStream will then test NVENC automatically.

Enabling NVIDIA support does not force NVENC. LANStream still performs its normal encoder tests and selects an available backend.

## Intel / VAAPI support

For Intel hardware acceleration under Linux, the container needs access to:

```text
/dev/dri
```

LANStream normally tests:

```text
/dev/dri/renderD128
```

for VAAPI.

---

# Opening LANStream

LANStream does not require a dedicated client application.

Open a web browser and navigate to:

```text
http://SERVER-IP:PORT
```

Example:

```text
http://192.168.1.50:8060
```

The default port is:

```text
8060
```

If authentication is enabled, LANStream first displays the login page.

If authentication is disabled, LANStream opens directly to the list of configured media roots.

---

# The config.txt File

LANStream uses one plain-text configuration file:

```text
config.txt
```

The same basic format is used on Windows and Linux.

A complete simple example:

```text
# LANStream configuration

port=8060
use_master_password=false
master_password=
transcoder=auto
low_bandwidth_resolution=854x480
low_bandwidth_bitrate=2M

D:\Movies
D:\TV
E:\Home Videos
```

Lines beginning with:

```text
#
```

are comments.

Blank lines are ignored.

## Available settings

### port

```text
port=8060
```

Controls the listening port for native Windows operation.

The default is:

```text
8060
```

Docker normally overrides the internal application port through its container environment and maps that to the host port selected in the deployment preset.

### use_master_password

```text
use_master_password=false
```

Valid values include:

```text
true
false
yes
no
on
off
1
0
```

When enabled, LANStream requires the password specified by `master_password`.

Password-profile sections override master-password mode.

### master_password

```text
master_password=MyPassword
```

Used only when:

```text
use_master_password=true
```

and no `<password>` profile sections exist.

### transcoder

```text
transcoder=auto
```

Available values are:

```text
auto
vaapi
qsv
nvenc
amf
software
```

`auto` is recommended.

When `auto` is selected, LANStream tests available encoders and chooses the first working option according to the platform-specific preference order.

### low_bandwidth_resolution

Default:

```text
low_bandwidth_resolution=854x480
```

This controls the maximum resolution used when the player is switched to Low Bandwidth mode.

The source aspect ratio is preserved.

For example, a 4:3 source constrained to `854x480` may become approximately:

```text
640x480
```

rather than being stretched to fill 854x480.

Dimensions are kept encoder-safe and even-numbered.

### low_bandwidth_bitrate

Default:

```text
low_bandwidth_bitrate=2M
```

This controls the target video bitrate in Low Bandwidth mode.

Examples:

```text
low_bandwidth_bitrate=800k
low_bandwidth_bitrate=1200k
low_bandwidth_bitrate=2M
low_bandwidth_bitrate=3M
```

Low Bandwidth audio is encoded at approximately 128 kbps in addition to the configured video bitrate.

---

# Adding Media Folders

When no password profiles are configured, each non-setting line in `config.txt` is treated as a media root.

## Windows examples

```text
D:\Movies
D:\TV
E:\Home Videos
```

Network UNC paths are also supported:

```text
\\NAS\Media\Movies
\\SERVER\Shared Videos
```

The Windows account running LANStream must have permission to read those paths.

Mapped drive letters may behave differently depending on how LANStream is launched and which Windows account owns the mapping. UNC paths are generally more predictable for network shares.

## Linux examples

```text
/mnt/media/Movies
/mnt/media/TV
/mnt/archive/Home Videos
```

When using Docker, the path written in `config.txt` must also exist inside the container.

If the host has:

```text
/mnt/media
```

and the container mounts it as:

```text
/mnt/media
```

then `config.txt` can directly use:

```text
/mnt/media/Movies
```

## Multiple roots

You can configure as many roots as needed.

Example:

```text
D:\Movies
D:\TV
E:\Documentaries
\\NAS\Archive\Home Videos
```

The LANStream home page displays each root separately.

Roots are sorted alphabetically for display.

---

# Authentication and Password Profiles

LANStream supports three access-control modes:

1. No password
2. One master password
3. Multiple password profiles with different visible folders

---

## Passwordless mode

For a trusted LAN:

```text
use_master_password=false
master_password=

D:\Movies
D:\TV
```

Users are not prompted to log in.

---

## Master-password mode

To require one password for all configured media:

```text
use_master_password=true
master_password=ExamplePassword

D:\Movies
D:\TV
```

All users who know that password see the same roots.

---

## Password profiles

Password profiles allow different passwords to expose different media paths.

Example:

```text
<GrownupPassword123>
D:\Cartoons
D:\TV
D:\Movies

<KidPassword123>
D:\Cartoons
D:\TV
-/ViolentShow1
-/RisqueShow2
```

Entering:

```text
GrownupPassword123
```

shows all three configured roots.

Entering:

```text
KidPassword123
```

shows only the paths listed in that profile, with the excluded folders hidden.

When any `<password>` profile sections are present:

```text
master_password=
```

and:

```text
use_master_password=
```

do not control access.

The profile sections become the authentication system.

## Relative exclusions

An exclusion beginning with:

```text
-/
```

is relative to the most recently listed positive root.

Example:

```text
<KidPassword>
D:\TV
-/Restricted Show
```

means:

```text
D:\TV\Restricted Show
```

is excluded.

Another example:

```text
<KidPassword>
D:\TV
-/Show A
-/Show B
D:\Movies
-/Horror
```

The exclusions apply as follows:

```text
D:\TV\Show A
D:\TV\Show B
D:\Movies\Horror
```

## Absolute exclusions

Absolute exclusions are also supported.

Example:

```text
<KidPassword>
D:\TV
D:\Movies
-D:\Movies\Horror
```

Use absolute exclusions when the target is easier to identify explicitly.

## Direct-link protection

Excluded directories are not merely hidden from the browser interface.

LANStream also checks access when resolving requested paths, so a user cannot normally bypass a profile exclusion simply by typing or reusing a direct URL to the excluded subtree.

## Logout

When authentication is enabled, use the LANStream logout function to clear the current authenticated session.

---

# Browsing Media

Use the [Enhanced operations guide](docs/enhanced-guide.md#browse-search-and-favorites) for List/Grid/Details, full-folder filtering and sorting, pagination, indexed search, favorites, refresh, offline behavior and explicit recursive indexing. It replaces the upstream all-items page and client-only filter.

---

# Playing Video

Select a video file from a folder to open its detail page.

The detail page may show information such as:

- filename
- resolution
- duration
- file size
- video codec
- audio streams
- subtitle streams
- generated preview image

Select **Play** to open the dedicated player.

The player begins playback automatically when allowed by the browser.

## Player controls

The player includes controls for:

- Play / Pause
- 30 seconds back
- 30 seconds forward
- timeline seeking
- current playback position
- volume
- fullscreen
- stream quality mode
- audio track selection when available
- supported subtitle selection when available

---

# Direct Playback and Transcoding

LANStream automatically decides whether a file can be sent directly to the browser.

## Direct playback

A direct stream means LANStream sends the original media file without re-encoding it.

This gives:

- original video quality
- minimal server CPU/GPU load
- no transcoding delay

LANStream uses explicit HTTP byte-range responses so browsers can seek efficiently.

## Current conservative direct-playback rules

LANStream intentionally uses conservative compatibility rules.

Common direct-play candidates include:

### MP4 / M4V / MOV

Video:

```text
H.264
```

Audio:

```text
AAC
MP3
```

or no audio.

### WebM

Video:

```text
VP8
VP9
AV1
```

Audio:

```text
Opus
Vorbis
```

or no audio.

A source containing multiple audio tracks is normally transcoded so LANStream can provide reliable audio-track selection.

Other containers and codecs are normally sent through the transcoder.

## Transcoded playback

When direct playback is not appropriate, LANStream launches FFmpeg and produces a browser-compatible fragmented MP4 stream.

The output uses:

```text
H.264 video
AAC audio
```

The selected transcoder backend depends on the server hardware and configuration.

Transcoding begins from the requested playback position, so seeking does not require encoding the entire file from the beginning.

---

# Low Bandwidth Mode

The player provides two explicit stream-quality options:

```text
Normal
Low Bandwidth
```

`Normal` is selected by default.

## Normal mode

Normal mode uses LANStream's usual playback decision:

- direct stream when possible
- normal transcoding when required

## Low Bandwidth mode

Low Bandwidth mode always forces transcoding, even when the source would otherwise play directly.

This allows LANStream to reduce both resolution and encoded bitrate.

Default settings:

```text
low_bandwidth_resolution=854x480
low_bandwidth_bitrate=2M
```

When you switch to Low Bandwidth mode:

1. LANStream notes the current playback position.
2. The existing stream is replaced.
3. A transcoded stream starts at approximately the same timestamp.
4. The configured resolution and bitrate limits are applied.

Switching back to Normal mode follows the same process.

## Choosing low-bandwidth settings

The defaults are intended as a reasonable general-purpose starting point.

For a moderately constrained Wi-Fi connection:

```text
low_bandwidth_resolution=854x480
low_bandwidth_bitrate=2M
```

For a slower connection:

```text
low_bandwidth_resolution=640x360
low_bandwidth_bitrate=1200k
```

For a very constrained connection:

```text
low_bandwidth_resolution=640x360
low_bandwidth_bitrate=800k
```

The best choice depends on:

- display size
- Wi-Fi conditions
- number of simultaneous users
- source complexity
- available transcoding performance

## Transfer rate versus media bitrate

The `/transcoder-status` page reports both network transfer rate and Low Bandwidth media target.

These are not the same thing.

A stream encoded at approximately:

```text
2 Mbps
```

may temporarily transfer over the network at:

```text
10 Mbps
20 Mbps
30 Mbps
```

or more.

This is normal.

FFmpeg may encode faster than real time and the browser may download data ahead of the current playback position.

The **Media target** field represents the configured encoded bitrate.

The **Transfer rate** field represents how quickly bytes are currently moving over HTTP.

---

# Audio Tracks and Subtitles

## Audio tracks

If the source contains multiple audio tracks, LANStream displays an audio selector.

Examples might include:

```text
eng · aac
jpn · aac
eng · ac3 · Commentary
```

Changing the audio track restarts the transcoded stream at the current playback position.

Files with multiple audio tracks are intentionally transcoded instead of direct-played so the selected stream can be mapped reliably.

## Subtitles

LANStream supports text-based subtitle streams that FFmpeg can convert to WebVTT.

Recognized text subtitle codecs include:

```text
subrip
srt
ass
ssa
webvtt
mov_text
text
```

Supported tracks appear in the subtitle selector.

Selecting a subtitle track starts or updates the browser subtitle track.

## Image-based subtitles

Image-based subtitle formats such as PGS are not currently converted for browser display.

If a file contains both supported text subtitles and unsupported image subtitles, only the supported text tracks are offered.

---

# Thumbnail and Preview Cache

LANStream generates thumbnails and larger preview images using FFmpeg.

These images are stored in the local cache rather than beside the media files.

LANStream does not write thumbnails into your media directories.

## Lazy generation

LANStream intentionally avoids generating images for an entire library at once.

When a directory is opened:

- the initial visible set is queued first
- additional thumbnails are requested as items approach the browser viewport

This keeps browsing responsive even when a folder contains many videos.

## Black-frame avoidance

LANStream checks several candidate frames when generating an image.

This reduces the chance of choosing:

- a black opening frame
- a fade-in
- a blank title transition

If every candidate appears mostly black, LANStream still creates an image rather than leaving the item permanently without a thumbnail.

## Cache location

### Windows

The cache normally resides beside LANStream:

```text
LANStream\cache\
```

### Docker

The cache should be bind-mounted to a persistent host directory.

For example:

```text
/opt/lanstream/cache/
```

## Clearing thumbnails

It is safe to delete generated cache files.

LANStream recreates them as needed.

If a thumbnail was generated from an undesirable frame, delete the applicable cache image and revisit the directory to trigger regeneration.

Deleting the entire cache does not alter media files, but it deletes Enhanced favorites and its index. Clear only thumbs/ and posters/ when clearing generated previews.

---

# Transcoder Status Page

LANStream includes a live diagnostics page:

```text
http://SERVER:PORT/transcoder-status
```

Example:

```text
http://192.168.1.50:8060/transcoder-status
```

This page is useful for confirming hardware acceleration and diagnosing playback.

## Selected backend

The page shows which transcoder LANStream selected.

Examples:

```text
Intel QSV
NVIDIA NVENC
AMD AMF
VAAPI
Software libx264
```

## Backend tests

LANStream tests available encoders by attempting a small real encode.

This is more reliable than simply checking whether an encoder name exists in FFmpeg.

An encoder may be compiled into FFmpeg but still fail because of:

- missing driver support
- unsupported hardware
- missing Docker GPU access
- incompatible runtime libraries
- permissions
- unavailable VAAPI device

The status page displays the result of each capability test.

## Active streams

Active streams can show:

- filename
- client IP
- playback mode
- transcoder backend
- elapsed stream time
- playback start offset
- bytes sent
- current transfer rate
- Low Bandwidth media target

Direct streams are displayed as:

```text
Direct
```

Transcoded streams identify the actual encoder backend.

## Transfer rate

Transfer rate represents HTTP throughput between LANStream and the browser.

It is not the same as source-file bitrate or encoded media bitrate.

Browsers commonly buffer ahead, so this number may be substantially higher than the bitrate required for continuous playback.

## Media target

For Low Bandwidth streams, the media target shows the configured approximate output bitrate.

With:

```text
low_bandwidth_bitrate=2M
```

LANStream uses approximately:

```text
2 Mbps video
128 kbps audio
```

for a total target around:

```text
2.13 Mbps
```

## Retesting hardware

The status page provides a way to force LANStream to repeat hardware capability tests.

This is useful after:

- updating GPU drivers
- changing FFmpeg
- changing Docker GPU configuration
- changing device permissions

---

# Hardware Acceleration

Hardware acceleration is optional.

If no supported hardware encoder works, LANStream can use software x264 encoding.

## Windows selection order

With:

```text
transcoder=auto
```

LANStream tests in this order:

1. Intel QSV
2. NVIDIA NVENC
3. AMD AMF
4. Software libx264

The first successful backend is selected.

## Linux selection order

LANStream tests:

1. VAAPI, when the configured device exists
2. NVIDIA NVENC
3. Software libx264

## Intel QSV

QSV requires:

- supported Intel graphics hardware
- a suitable Intel graphics driver
- FFmpeg built with QSV support

The status page reports whether the actual encoder test succeeds.

## NVIDIA NVENC

NVENC requires:

- supported NVIDIA GPU
- compatible NVIDIA driver
- FFmpeg built with NVENC support

For Docker, NVIDIA Container Toolkit is also required.

A machine may support NVENC in one application but not another if the applications ship different encoder runtimes or FFmpeg builds. The LANStream status test confirms whether the exact FFmpeg binary used by LANStream can initialize the encoder.

## AMD AMF

AMF on Windows requires:

- compatible AMD graphics hardware
- current AMD drivers
- FFmpeg built with AMF support

## VAAPI

VAAPI is the normal Linux hardware path.

LANStream defaults to:

```text
/dev/dri/renderD128
```

The Docker container must be allowed to access `/dev/dri`.

## Software x264

Software transcoding uses:

```text
libx264
```

This works without compatible GPU hardware but consumes CPU resources.

High-resolution sources may require a reasonably capable processor for real-time software transcoding.

---

# Changing the Listening Port

## Windows

Edit:

```text
port=8060
```

in `config.txt`.

Example:

```text
port=9000
```

Restart LANStream.

Then connect to:

```text
http://SERVER-IP:9000
```

## Docker

Docker normally uses an internal application port and a separately configured host port.

The published deployment preset controls the externally exposed host port.

Changing only the Windows-style `port=` setting may not change the Docker host mapping because Docker environment settings override it.

Use the Docker/LEC host-port setting for the externally accessible port.

---

# Updating LANStream

## Windows

The portable Windows release keeps user-editable files beside the executable.

Before updating, preserve:

```text
config.txt
cache\
```

Recommended process:

1. Stop LANStream.
2. Back up your existing `config.txt`.
3. Extract the new release to a new folder.
4. Copy your existing `config.txt` into the new folder.
5. Optionally copy the old `cache` folder to preserve thumbnails.
6. Start the new `LANStream.exe`.
7. Confirm `/transcoder-status` reports the expected backend.

Using a new folder makes rollback easy.

## Docker

The recommended Docker design stores configuration and cache outside the application image.

To update:

1. Stop or redeploy the container.
2. Pull/build the newer LANStream image/source.
3. Reuse the existing host `config.txt`.
4. Reuse the existing cache directory.
5. Reuse the same media mounts.
6. Start the new container.

Because the application image is disposable, updating should not require rebuilding your media configuration.

---

# Backing Up LANStream

Preserve config.txt and the complete cache directory, including index.sqlite3 and any WAL/SHM companions. Stop Enhanced before copying the database. The SQLite file includes durable Favorites. See [backup and recovery](docs/enhanced-guide.md#backup-schema-and-index-recovery) before rebuilding or clearing anything.

---

# Troubleshooting

## LANStream opens but no media folders appear

Check `config.txt`.

Make sure media paths are listed after the settings:

```text
D:\Movies
D:\TV
```

On Linux/Docker, confirm that the exact paths exist inside the container.

If the path exists only on the host but is not mounted into Docker, LANStream cannot see it.

---

## A configured root appears unavailable

Verify that:

- the directory exists
- the account running LANStream can read it
- the network share is connected
- the Docker bind mount is correct

For Windows network storage, prefer UNC paths such as:

```text
\\NAS\Media\Movies
```

when mapped-drive visibility is unreliable.

---

## Browser cannot connect to Windows LANStream

Check:

1. `LANStream.exe` is still running.
2. The console shows the expected port.
3. You are using the correct server IP address.
4. Windows Firewall allows LANStream on Private networks.
5. The client is on the same reachable network.

Test locally on the server first:

```text
http://localhost:8060
```

If localhost works but another device cannot connect, the issue is usually firewall or network routing.

---

## Video plays directly but does not seek well

LANStream implements HTTP byte-range streaming for direct playback.

If seeking is still problematic:

- try a different browser
- confirm the file is not on an extremely slow or unreliable storage device
- check that a reverse proxy is not interfering with Range requests

---

## Video will not play

Open:

```text
/transcoder-status
```

Check whether LANStream has a usable transcoder.

If no backend is available, direct-compatible files may still work, but files requiring transcoding will fail.

---

## Hardware transcoder is unavailable

Read the detailed error on `/transcoder-status`.

Common causes include:

- outdated GPU driver
- FFmpeg build lacking the required encoder
- unsupported GPU
- Docker GPU not exposed
- missing NVIDIA Container Toolkit
- `/dev/dri` not available
- Linux device permissions

Use the status-page retest after correcting the problem.

---

## NVIDIA NVENC reports an API version mismatch

An error similar to:

```text
Required NVENC API: X
Found: Y
```

means the FFmpeg build expects a newer NVIDIA driver interface than the installed driver provides.

Update the NVIDIA driver and reboot.

Then force a transcoder retest.

---

## Intel QSV reports MFX errors

Errors mentioning:

```text
MFX
```

or:

```text
current mfx implementation is not supported
```

usually mean the installed Intel hardware/driver combination cannot initialize the QSV encoder through that FFmpeg build.

LANStream will normally continue testing NVENC, AMF, or software encoding.

---

## Linux VAAPI is unavailable

Check whether the render device exists:

```bash
ls -l /dev/dri
```

A common device is:

```text
/dev/dri/renderD128
```

For Docker, make sure `/dev/dri` is exposed to the container.

---

## Low Bandwidth picture is lower quality but status still shows high Mbps

Check which status column you are reading.

**Transfer rate** can be high because FFmpeg and the browser are buffering ahead.

**Media target** is the relevant Low Bandwidth encoding target.

A 2 Mbps media stream may still transfer in short 20 Mbps bursts.

---

## Low Bandwidth mode still buffers

Try reducing the configuration.

Example:

```text
low_bandwidth_resolution=640x360
low_bandwidth_bitrate=1200k
```

Restart LANStream after changing server-level settings.

Also verify that the server can transcode the source faster than real time.

---

## Thumbnails are missing

Thumbnail generation requires working FFmpeg access.

Check:

```text
/transcoder-status
```

and server logs.

If FFmpeg itself is unavailable, video probing and image generation may also fail.

---

## Thumbnail shows a poor frame

Delete the applicable cached thumbnail or clear the cache.

LANStream will regenerate the image.

---

## Old password still appears to be required

If changing authentication mode:

1. Confirm `config.txt` contains the intended settings.
2. Restart LANStream or the container when changing server-level configuration.
3. Clear the browser session by logging out or removing the LANStream site cookie if necessary.

Password profiles take precedence whenever `<password>` sections exist.

---

## Password profiles show the wrong directories

Check the order of entries carefully.

A relative exclusion:

```text
-/Folder
```

belongs to the most recently listed positive root.

Example:

```text
D:\TV
-/Restricted
D:\Movies
-/Horror
```

means:

```text
D:\TV\Restricted
D:\Movies\Horror
```

are excluded.

---

## Docker config changes do not appear

Confirm you are editing the host file that is actually mounted as:

```text
/config.txt
```

into the container.

Media-root and profile changes are read when pages are loaded, but environment-overridden settings may require restarting the container.

---

## CPU usage is unexpectedly high

Visit:

```text
/transcoder-status
```

If the selected backend is:

```text
Software libx264
```

then LANStream is using CPU transcoding.

Investigate why the expected hardware encoder failed its capability test.

---

## Network usage is unexpectedly high

Direct streams send the original source bitrate.

A high-bitrate Blu-ray rip or camera file may require tens of Mbps.

Use Low Bandwidth mode to force a constrained stream.

---

# Security Notes

LANStream is primarily designed for trusted local networks.

## Passwords

LANStream's password system is useful for basic household or LAN access control.

It should not be treated as a replacement for a hardened internet-facing identity system.

## Internet exposure

Do not expose LANStream directly to the public internet without appropriate protection.

If remote access is required, use a properly configured solution such as:

- VPN
- authenticated reverse proxy
- TLS termination
- firewall restrictions

## Media permissions

Where practical, grant LANStream read-only access to media.

The recommended Docker deployment mounts media with:

```text
:ro
```

This protects the collection even if the application encounters a problem.

## Reverse proxies

A reverse proxy should preserve:

- streaming responses
- HTTP Range headers
- long-running connections

If direct-stream seeking breaks only through the reverse proxy, verify the proxy's Range-request behavior.

---

# Supported Video File Extensions

LANStream recognizes files with these extensions as video:

```text
.mp4
.m4v
.mov
.mkv
.webm
.avi
.mpg
.mpeg
.m2ts
.mts
.ts
.wmv
.flv
.ogv
```

Recognition does not guarantee direct browser playback.

Unsupported browser formats are normally transcoded through FFmpeg.

Actual codec support therefore also depends on the FFmpeg build supplied with or installed for LANStream.

---

# Licensing

LANStream itself is released into the public domain under **The Unlicense**.

See:

```text
LICENSE
```

The Windows distribution may include separately licensed third-party software, including:

```text
ffmpeg.exe
ffprobe.exe
```

Those programs are not covered by The Unlicense.

See:

```text
THIRD_PARTY_LICENSES.md
```

for the exact third-party licensing information included with the release.

---

# Quick Reference

## Default Windows configuration

```text
port=8060
use_master_password=false
master_password=
transcoder=auto
low_bandwidth_resolution=854x480
low_bandwidth_bitrate=2M

D:\Movies
D:\TV
```

## Default Linux configuration

```text
port=8060
use_master_password=false
master_password=
transcoder=auto
low_bandwidth_resolution=854x480
low_bandwidth_bitrate=2M

/mnt/media/Movies
/mnt/media/TV
```

## Status page

```text
http://SERVER-IP:8060/transcoder-status
```

## Transcoder values

```text
auto
vaapi
qsv
nvenc
amf
software
```

## Default Low Bandwidth settings

```text
854x480
2 Mbps video
128 kbps audio
```

## Windows automatic transcoder preference

```text
Intel QSV
NVIDIA NVENC
AMD AMF
Software libx264
```

## Linux automatic transcoder preference

```text
VAAPI
NVIDIA NVENC
Software libx264
```

---

# Version

**LANStream 1.0.0**
