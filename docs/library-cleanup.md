# Library Cleanup

Open **Library Cleanup** in the top navigation, or **Open in Library Cleanup** inside a folder. Cleanup has its own table, thumbnail preference, and file selections. Ordinary folder browsing keeps its current views and also offers **Sort: Duration** and a saved Duration column in Details.

## Find and compare files

Use **Change folder** to choose a library or an indexed folder. **Include subfolders** starts enabled; uncheck it to compare only files directly in that folder. Breadcrumbs and file locations navigate within Cleanup. Opening Cleanup from ordinary browsing includes that folder's subfolders. With several libraries, choose a location first; **All accessible libraries** remains available in the picker.

**Possible duplicates** shows a group list beside the file comparison table (stacked on narrow screens). **Same size** groups identical byte counts; **Same duration** groups videos with the same displayed hours, minutes and seconds, ignoring fractional seconds. Switch between these separate clues. They do not prove identical contents: LANStream does not hash files or automatically select copies. Missing durations are counted separately and excluded from duration groups until prepared.

Search any part of a filename with Unicode case-insensitive matching. In Possible duplicates, a matching filename finds its entire group, including renamed copies in the chosen folder tree. Scope, access restrictions, and file-type filters still apply to every member. The file categories are supported videos, pictures, and documents; arbitrary filesystem files are not indexed.

Groups are ordered by largest size or longest duration, with 20 groups per page. Choose a group to compare its files by location, in 100-file pages; group counts cover all members. **All files** retains sorting by filename, location, size, duration, modified date, and type across the complete result set before pagination. Exact byte counts appear under readable sizes. Results can be incomplete while indexing is pending or stale when storage is unavailable; changing the view never starts a share scan.

Thumbnails start off and are independent of normal browsing. Enabling them loads cached images for visible rows, with two requests at most. It does not generate missing images. **Prepare thumbnails in this folder** is explicit and covers the selected folder without subfolders. Refresh results afterward to see newly cached images.

Select individual files or **Select this page**. Selection is limited to the current comparison page and clears when you change groups, results, filters, sorting, location, or tab. The confirmation identifies actual files and original locations. Groups are recomputed after changes; a group disappears when fewer than two files remain. Partial failures remain visible with explanations. Trash has its own filename search so a group search does not hide a differently named copy after moving it.

## Prepare durations

**Prepare durations** reads videos in the chosen folder scope, including subfolders when selected, irrespective of filename, file-type, or group filters. Results are saved across restarts. Unknown durations display an em dash and sort last in both directions in All files. Duration grouping excludes unavailable values. Sorting, grouping, searching, or opening Cleanup never starts FFprobe. Opening a video normally also saves valid duration metadata from its existing probe.

One preparation worker runs at a time and pauses before the next file while LANStream reports active playback. Pause/stop take effect between files; the current FFprobe call can take up to 30 seconds. Failed files are not retried automatically; use **Retry failed durations**. A restart stops the preparation job but keeps saved results. Changed files need new metadata after indexing discovers their change. File size/timestamps are version clues, not a content hash.

This update preserves folder-name casing when refreshing restored files on Windows, so their saved durations stay available. If a duration disappeared after restoring with an older version, prepare durations again for that folder; existing affected cache entries are not automatically rewritten.

## Enable file changes

Cleanup is read-only by default. On Windows, configure LANStream's existing password sign-in and set this global option in your private server configuration, then restart:

```ini
library_cleanup_enabled=true
```

Every signed-in profile can then move, restore, or permanently delete files within its currently accessible roots and exclusions. There is no separate cleanup administrator role. Passwordless browsing remains supported, but its Cleanup page cannot change files. Linux/Docker Cleanup supports indexed reads and duration preparation; source-file changes are Windows-only in this version. The server process still needs filesystem write permissions.

Changing a shared file affects every profile and other application using the same storage. A profile cannot see Trash items whose original paths are currently excluded or outside its configured libraries. Removed/reordered roots and changed exclusions are rechecked before actions. A busy stream, thumbnail operation, changed file, sharing violation, or unavailable share can cause an action to fail safely; refresh and retry when appropriate.

## Recoverable Trash

**Move to Trash** relocates the selected files to a reserved `.lanstream-trash` directory at their library root. This is LANStream-managed storage, separate from the Windows Recycle Bin. It uses a same-storage move; if the storage cannot support the required safe operation, LANStream fails without falling back to copy/delete or permanent deletion. Trash retains the space used by the media.

Open the **Trash** tab to restore files or select a bounded group for **Permanently delete selected**. Restore requires the original parent folder to exist and never overwrites an existing destination or silently renames a file. Resolve destination conflicts yourself and retry. No files expire automatically and there is no automatic emptying of Trash. Permanent deletion requires its own explicit confirmation and cannot be undone through LANStream.

Operations run on a single bounded background worker. If a share stalls, an operation can remain Pending while indexed reads stay available. Reopening Cleanup resumes checking a saved operation in the same browser tab. Do not repeat an uncertain operation or manipulate its payload externally; resolve its state first. An interrupted or ambiguous operation may show **Needs attention** rather than make a destructive assumption.

## Backups and recovery

`cache/trash.sqlite3` is durable product data containing the operation journal and confirmations, separate from the rebuildable `cache/index.sqlite3`. Back up the entire cache consistently, including live SQLite WAL files, and each library's `.lanstream-trash` directory. The existing Windows upgrade preserves the cache; include the on-share Trash in your media backups as well. Deleting local cache is no longer a harmless way to reset every application record.

Each on-share Trash item includes an original-location manifest and an opaque payload. After loss of the local journal/cache, choose the library in the Trash tab and use **Recover selected library's Trash records**. Only that library's reserved directory is inspected, in the background. Before first mutation on a root, the server also checks for existing records so an empty local database is not mistaken for empty on-share Trash. Invalid, unexpected, changed, or orphan payloads are preserved for attention. Recovery never automatically deletes an uncertain file. A purge interrupted after unlink can require attention even though its file is already absent.

The reserved directory is omitted from indexing and blocked by media/viewer routes, including old snapshots and nested-root aliases. It is still a real filesystem directory with the library's inherited permissions; it is not encrypted or protected from users and other tools that have direct share access. Do not rename it, edit its manifests, or configure a root inside it. Completed manifest directories are retained as recovery evidence; LANStream does not recursively remove them.

Local Windows tests with fictional files establish the implementation's no-overwrite, identity, interruption and recovery behavior. Actual SMB server permissions, locking, disconnect/reconnect behavior, storage durability, N100 load and physical-device playback need acceptance on the target setup before relying on a release for real cleanup.
