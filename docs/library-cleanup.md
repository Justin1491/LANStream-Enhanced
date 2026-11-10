# Library Cleanup

Open **Library Cleanup** in the top navigation, or **Open in Library Cleanup** inside a folder. Cleanup has its own table, thumbnail preference, and file selections. Ordinary folder browsing keeps its current views and also offers **Sort: Duration** and a saved Duration column in Details.

## Find and compare files

Choose all accessible libraries or one library, then choose library/folder/subfolder scope. **Choose indexed folder** navigates the folders already in the database. Filename contains searches any part of a filename with the existing Unicode case-insensitive matching. The file categories are LANStream's supported videos, pictures, and documents; arbitrary filesystem files are not added to the index.

Click a column heading to sort the complete filtered result set, then use the 100-item pages. **Show matching sizes** shows only groups with identical byte counts; the group count includes all matching paths across pages. Exact bytes appear under the readable size. Equal sizes, lengths, or thumbnails suggest candidates for review; they do not prove identical contents, and LANStream never selects duplicates automatically.

Thumbnails start off and are independent of normal browsing. Enabling them loads cached images for visible rows, with two requests at most. It does not generate missing images. **Prepare thumbnails in this folder** is explicit and covers the selected folder without subfolders. Refresh results afterward to see newly cached images.

Select individual files or **Select this page**. Selection is limited to the current page and clears when you change the results, filters, sorting, scope, or tab. The confirmation identifies the actual files and original locations. Partial failures remain in the results with explanations.

## Prepare durations

**Prepare durations** reads videos in the chosen library/folder scope, irrespective of the filename or matching-size filters. Results are saved across restarts. Unknown durations display an em dash and sort last in both directions. Sorting or opening the Cleanup page never starts FFprobe. Opening a video normally also saves valid duration metadata from its existing probe.

One preparation worker runs at a time and pauses before the next file while LANStream reports active playback. Pause/stop take effect between files; the current FFprobe call can take up to 30 seconds. Failed files are not retried automatically; use **Retry failed durations**. A restart stops the preparation job but keeps saved results. Changed files need new metadata after indexing discovers their change. File size/timestamps are version clues, not a content hash.

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
