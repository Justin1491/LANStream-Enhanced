# LANStream Enhanced

A folder-native media browser and player for your local network. Browse your existing folders, search across libraries, save Favorites, and open videos, pictures, PDFs, comics, and text in a browser. Browsing and playback leave your media intact. An optional, separate Library Cleanup page can move unwanted files to recoverable Trash.

Enhanced builds on [candre23/LANStream](https://github.com/candre23/LANStream) with persistent directory caching, paginated browsing, background indexing, and a more capable media interface. It runs without a cloud account, metadata matching, or a library import. You can browse while indexing continues in the background.

[Downloads](https://github.com/Justin1491/LANStream-Enhanced/releases) · [Enhanced guide](docs/enhanced-guide.md) · [Installation and upgrades](docs/n100-installer.md)

## What's new in Enhanced

### Browse large folders

- **List, Grid, and Details views**, with compact List thumbnails, adjustable Grid density, and view preferences remembered per device. Mobile defaults to List.
- **Server-side pagination** keeps each page to at most 100 items, including folders containing thousands of files.
- **Whole-folder sorting and filtering**, rather than just filtering the current page. Sort by name, date, size, saved duration, or type, with folders first and natural filename order such as `Clip 2` before `Clip 10`. Unknown durations sort last; sorting never probes files.
- **File type filters** in Browse, Search, and Favorites: All, Videos, Pictures, and Documents. Documents include PDFs, comics, and text/Markdown; folders remain visible for navigation.
- **Persistent SQLite directory snapshots** make warm revisits possible without repeating a full directory scan. Stale snapshots remain available while a refresh runs; failed or offline scans retain the last good listing and show an error.
- Breadcrumbs, containing-folder links, and browser Back/Forward support keep your place in the collection.

Cold network-share scans still depend on the server and network. Caching improves repeat visits; it cannot eliminate the first SMB enumeration.

### Search and background indexing

- **Case-insensitive Unicode filename substring search** across all indexed roots, the current subtree, or the current folder.
- Results show the library and containing-folder path, with an **Open folder** action.
- **Automatic recursive indexing** discovers all configured libraries at startup and refreshes them periodically, even without a browser open.
- **Background indexing controls** show library progress and let you pause or resume/refresh discovery. Interactive browsing and folder refreshes take priority over queued background scans.
- Coverage indicators show how much has been indexed. Search is partial until discovery finishes, and offline results can be stale.

Automatic indexing is enabled by default. Set `automatic_indexing=false` in `config.txt` and restart for on-demand browsing and manual indexing only. This preserves existing snapshots and Favorites. When automatic indexing is enabled, restarting the server resumes discovery, including previously paused tasks.

### Library Cleanup and recoverable Trash

- A separate indexed table provides partial filename search, folder/subfolder scopes, exact-size comparison groups, sortable attributes, and optional cached thumbnails.
- Explicit background duration preparation saves video lengths for Cleanup and ordinary browsing; it pauses between files during tracked playback.
- On Windows, opt-in authenticated file changes support recoverable Trash, restore without overwriting, and separately confirmed permanent deletion. No automatic purge runs.
- Back up the durable `cache/trash.sqlite3` journal and each library's `.lanstream-trash` directory, as well as the rest of the cache. Trash retains media disk space until permanent deletion.

See the [Library Cleanup guide](docs/library-cleanup.md) for enablement, permissions, recovery, and storage limitations. The feature is read-only by default (`library_cleanup_enabled=false`).

### Persistent Favorites

Star **videos and folders** from any browsing view, Search, Favorites, video details, or the player. Favorites persist across restarts in the local database and are scoped to the active password profile.

Root reordering does not change a Favorite's identity. Missing or renamed items remain at their saved location so you can remove them; they are not silently matched to another file. Changing a profile password starts a new Favorites identity for that profile.

### Thumbnails that keep up with browsing

- Cached thumbnails and posters are reused, with missing previews generated lazily for visible and nearby items.
- Background listing refreshes preserve loaded thumbnails and pending downloads for unchanged files.
- Bounded queues, request timeouts, and retry backoff prevent unavailable previews from holding up the rest of the folder.
- **Prepare thumbnails** processes the entire current folder, including later pages and filtered-out files, without descending into subfolders. Progress and **Stop preparation** remain available when you return to the folder.
- Preparation continues after navigation, uses lower priority than browsing, and pauses during tracked playback. It stops on application shutdown; starting it again reuses completed previews.

Two thumbnail workers are used by default. Set `thumbnail_workers=1` if preview generation competes with playback on a smaller server. Indexing alone does not start bulk thumbnail generation.

### More than video

| Media | Browser experience |
| --- | --- |
| Pictures, including TIFF | Image viewer with next/previous navigation, arrow keys, swipes, and fullscreen navigation |
| PDF | Page viewer with keyboard, swipe, and fullscreen controls |
| CBZ / CBR comics | Page viewer with natural page ordering; CBR requires 7-Zip |
| Text / Markdown | Escaped plain-text display; embedded HTML is not executed |

## Video playback

Compatible sources play directly when the browser supports them. Sources requiring conversion, including HEVC/H.265 video, use H.264 transcoding with stereo AAC audio. The player provides seeking, audio and supported subtitle track selection, fullscreen controls, and a Favorite button.

Video metadata is remembered in server memory for up to five minutes to avoid repeated FFprobe work when opening the same file. File identity, size, or timestamp changes trigger a new probe; restarting the server clears the cache.

- **Normal** preserves the normal playback profile.
- **Low Bandwidth** applies a configurable resolution and bitrate. Defaults are `854x480` and `2M`; change `low_bandwidth_resolution` and `low_bandwidth_bitrate` in `config.txt` and restart. Quality is selected manually.
- **Native HLS delivery** serves generated playback to browsers such as Safari using playlists and short MP4 segments, including Low Bandwidth playback. Other browsers retain streamed MP4 delivery.
- **Playback details** shows startup progress, errors, and build/instance identity. Visit `/transcoder-status` for the selected backend and encoder capability-test results.

Available transcoding backends depend on the host, FFmpeg build, and drivers:

| Server | Backends |
| --- | --- |
| Windows | Intel Quick Sync (QSV), NVIDIA NVENC, AMD AMF, software H.264 |
| Linux | VAAPI, NVIDIA NVENC, software H.264 |

Automatic selection tests available encoders and can fall back to software. Hardware acceleration is optional.

The Windows QSV HLS startup fix has documented Mac Safari and physical iPhone playback results for a representative input. See [Windows playback diagnostics](docs/windows-playback-diagnostics.md) for the tested build and scope. Other inputs, GPUs, devices, and newly installed builds still need their own playback checks.

## Windows setup

Use 64-bit Windows 10/11 and a modern browser on your client device. The portable Windows app does not require a separate Python installation. Run it with a Windows account that can read your media folders or UNC shares.

### Upgrade an existing Enhanced installation

1. Download **LANStream-Enhanced-Upgrade.zip** from the [Enhanced releases](https://github.com/Justin1491/LANStream-Enhanced/releases).
2. Unblock the downloaded ZIP in Properties if shown, then extract the entire archive into a fresh folder outside the installation. Keep the included files together and leave the inner application ZIP compressed.
3. Run **Upgrade Enhanced.cmd**.
4. Open `http://localhost:8061` on the server, or `http://<server-LAN-IP>:8061` from another device, and check playback.

The upgrader preserves `config.txt`, the complete cache and Favorites, and installed media tools. It retains the previous installation as a dated backup and restores it if the replacement fails its startup checks. Allow space for another copy of the app and cache. An upgrade-only ZIP requires an existing Enhanced installation with the expected media tools.

### First installation

Use a complete portable Windows app package or the **LANStream-Enhanced-N100-Setup.zip** bundle when supplied. The upgrade-only ZIP cannot create a first installation.

For the setup bundle, extract all files, edit the generic `config.txt` with your media roots while retaining its setup-password placeholder, then run **Install Enhanced.cmd**. Setup prompts for a password and installs separately at `C:\LANStream-Enhanced` on port **8061**. See the [first-install guide](docs/n100-installer.md#first-installation) for template details and acceptance checks; N100 acceptance requires working QSV.

For a complete portable app, extract it into a separate writable folder, edit its `config.txt`, and run `LANStream.exe`. Keep FFmpeg, FFprobe, the runtime files, and the supplied comic-viewer tools alongside the app as packaged.

The Windows app starts in the system tray beside the clock (possibly under hidden icons). Click its icon to show URLs, transcoder, build identity and live output in the status window. Minimize or close hides the window while the server keeps running; right-click the icon and choose **Exit LANStream** to stop. Startup errors open the status window automatically. Direct Python console launches and Docker behavior are unchanged.

Keep an existing original LANStream installation separate. The Enhanced installer protects `C:\LANStream` and port 8060. Neither installer creates a service, scheduled task, or firewall rule; allow Private-network access if Windows prompts.

## Configuration and access

`config.txt` defines media roots, port, playback settings, indexing, and thumbnail workers. Multiple roots, local paths, and UNC shares are supported. Configuration changes generally require a restart; root/profile changes can be picked up by subsequent requests and background checks.

Access can be passwordless on a trusted LAN, protected by a single master password, or split into password profiles. Profiles can expose different roots and exclude subfolders with `-/Subfolder` entries. Browse, Search, Favorites, images, and playback recheck profile access; indexing does not grant access to another profile's files.

See the [Enhanced guide](docs/enhanced-guide.md) for configuration examples and the [User Manual](manual.md) for the full configuration syntax.

### Linux / Docker

Linux configuration and Docker deployment instructions are retained in the [User Manual](manual.md#linux--docker-installation). Keep configuration and the complete cache outside the container, and mount media read-only. VAAPI needs access to `/dev/dri`; NVENC in Docker needs the NVIDIA Container Toolkit.

The current Dockerfile omits the required `hls_streams.py` module from its application copy step. It needs that packaging correction before use with the current source; the Windows verification notes do not establish a working Docker build.

## Backups and media safety

Media stays in its original folders. LANStream writes its directory index, Favorites, generated previews, and temporary playback data to its local cache; keep that cache on local storage where possible.

Back up **`config.txt` and the complete `cache/` directory** with the app stopped. Favorites live in **`cache/index.sqlite3`**, alongside regenerable directory snapshots. Preserve any SQLite `-wal` and `-shm` companions when copying the database. Deleting the entire cache also deletes Favorites.

Upgrade backups and logs can contain passwords, private media paths, and Favorites. Keep them private. See [backup and recovery](docs/enhanced-guide.md#backup-schema-and-index-recovery) before rebuilding an index or cleaning cached data.

Use LANStream on a **trusted private LAN or VPN**. It retains the upstream private-network authentication model and is not intended for direct public internet exposure.

## Documentation and development

- [Enhanced operations guide](docs/enhanced-guide.md): everyday use, configuration, indexing, thumbnails, backups, and benchmarks.
- [Windows installation and upgrades](docs/n100-installer.md): first setup, upgrade preservation, rollback, and N100 acceptance.
- [Windows development handoff](docs/windows-development-handoff.md): source setup, code map, build/release workflow, and verification limits.
- [Windows playback diagnostics](docs/windows-playback-diagnostics.md): QSV startup investigation and documented Safari/iPhone results.
- [User Manual](manual.md): broader configuration and inherited platform instructions; use the Enhanced guides for changed behavior.
- [Historical benchmark and test results](docs/status.md): local synthetic measurements and earlier acceptance snapshots, not a claim about every current installation.

## License and attribution

LANStream Enhanced is based on [candre23/LANStream](https://github.com/candre23/LANStream) and is released under [The Unlicense](LICENSE).

Third-party dependencies retain their own licenses. See [Third-Party Licenses](THIRD_PARTY_LICENSES.md).
