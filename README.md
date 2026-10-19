# LANStream Enhanced

LANStream is a lightweight, folder-native, web-based media browser and player for local networks.

It is designed for people who want to open a browser, peruse a media collection, and play videos without importing a metadata library or changing their folders. Enhanced keeps local directory snapshots and profile-scoped favorites in SQLite.

LANStream runs on Linux through Docker or as a portable Windows application. It supports direct playback whenever the browser can handle the source file and automatically falls back to hardware-accelerated or software transcoding when necessary.

<img width="1148" height="628" alt="lanstream1" src="https://github.com/user-attachments/assets/48dd5b48-0f33-4bf7-b06f-969c85575cfb" />

<img width="1135" height="510" alt="lanstream2" src="https://github.com/user-attachments/assets/a0907380-0f9b-4fc4-a9c7-f46515c714a5" />

<img width="1145" height="648" alt="lanstream3" src="https://github.com/user-attachments/assets/deb61a2c-8032-498f-adf6-efbf37a8e684" />

<img width="1252" height="798" alt="lanstream4" src="https://github.com/user-attachments/assets/d6109107-5a9f-45f2-b647-8da761586d75" />

## Enhanced edition

Based on [candre23/LANStream](https://github.com/candre23/LANStream). See the [Enhanced operations guide](docs/enhanced-guide.md) for isolated port-8061 deployment, configuration, cache/favorite backups, benchmarks and rollback. See [measured status](docs/status.md) for local verification and remaining N100/SMB checks. The existing production install must stay untouched.

## Why LANStream?

Most home media servers are built around a managed library. That is ideal if you want posters, metadata matching, watch history, recommendations, user profiles, and a streaming-service-style interface.

Not everybody needs all that.  If your media is already organized into folders and you simply want to poke around and play the files, a full media-management platform can be more effort than it's worth.

LANStream is

- **Cached browsing**
  SQLite snapshots avoid repeated full directory scans; back up the local database to preserve Favorites.
- **Scan-free**  
  LANStream does not need to index your entire collection before it becomes usable. Directories are read when you browse them.
- **Folder-native**  
  Your existing directory structure is the interface. There is no requirement to rename or reorganize files for metadata matching.
- **Read-only toward your media**  
  LANStream does not need to modify files in your media collection.
- **Portable**  
  The Windows build can be extracted and run as a self-contained application. The Linux version can run in Docker with configuration and cache files kept outside the container.
- **Low-maintenance**  
  With `automatic_indexing=true` (the default in `config.txt`), all configured libraries and their subfolders are indexed automatically behind browsing and refresh requests. Periodic scans keep search updated as files are added or removed. Set it to `false` and restart for on-demand browsing and manual indexing only.
- **LAN-focused**  
  LANStream is optimized for simple local-network playback rather than trying to be an all-purpose cloud media ecosystem.

This makes it particularly useful for:

- large collections that are already organized by folder
- archive drives
- home-video collections
- temporary or portable media servers
- off-grid or popup media streaming
- shared household folders
- systems where simplicity and transparency matter more than rich metadata

## Features

- Browse ordinary folders directly from a web browser
- Multiple configurable media roots
- Server pagination and full-folder filtering/sorting
- File type filter: All, Videos, Pictures, or Documents (PDFs, comics, and text/Markdown); folders remain visible
- List/Grid/Details with device preferences and compact video thumbnails
- Global Unicode filename substring search with path context
- Profile-scoped persistent video/folder favorites
- Lazy thumbnail generation
- Cached thumbnails and preview images
- Dedicated browser-based video player
- Extremely low seek latency and buffering times
- Audio & subtitle track selection
- Low-bandwidth mode for congested networks

### Transcoding

Videos will be streamed directly to the browser when possible.  For formats unsupported by the browser, LANStream automatically detects available encoders and selects an appropriate backend.

**Windows**

- Intel Quick Sync Video (QSV)
- NVIDIA NVENC
- AMD AMF
- software H.264

**Linux / Docker**

- VAAPI
- NVIDIA NVENC
- software H.264

The active backend and capability-test results can be viewed at:

```text
<your LANStream address>/transcoder-status
```


### Access Control

LANStream can run completely passwordless on a trusted LAN, use a single master password, or define multiple password profiles.

Profiles can expose different roots and exclude specific subfolders.


## Installation

### Windows

The Windows release is distributed as a portable, self-contained folder.  Download the latest release zip.

1. Extract the release archive.
2. Edit `config.txt`.
3. Run `LANStream.exe`.
4. Open the displayed LAN URL in a browser.

See [manual.md](manual.md) for detailed setup instructions.

### Linux / Docker

LANStream can be run in Docker.

The recommended deployment keeps user data outside the container:

```text
config.txt
cache/
```

Media directories are mounted read-only.

This means the container can be rebuilt or replaced without losing configuration or thumbnail cache data.

NVIDIA NVENC in Docker requires NVIDIA Container Toolkit on the host.

A Linux Easy Config preset is provided for deployments using LEC.

## Requirements

### Client

A reasonably modern web browser with HTML5 video support.

### Windows server

- 64-bit Windows 10/11
- Hardware acceleration is optional

### Linux server

- Docker
- `/dev/dri` access for VAAPI when applicable
- NVIDIA Container Toolkit when using NVENC in Docker


## AI & Safety Disclaimer

The code and documentation included in this project is primarily vibeslop. The human writing this sentence in particular can barely code and doesn't really understand how any of this works. It Works On My Machine and hasn't caused my genitals to explode, but your mileage may vary. I make absolutely no guarantee as to the safety or security of the contents of this project. Use at your own risk. Or don't.

LANStream is primarily intended for trusted local networks.  It is not "secure" in any meaningful sense and should never be exposed to the internet at large.  If you choose to ignore this advice and do it anyway, for the love of fuck at least place it behind an appropriately configured reverse proxy and TLS endpoint.


## Documentation

Detailed setup and usage instructions can be found in the [User Manual](manual.md).


## License

LANStream is released into the public domain under [The Unlicense](LICENSE).

Third-party libraries and dependencies remain subject to their respective licenses. See [Third-Party Licenses](THIRD_PARTY_LICENSES.md) for attribution and licensing information.

Copyleft 2026. Do what thou wilt shall be the whole of the law. One step closer to AGI.

