# Folder-first Cleanup implementation

Approved in chat after the interactive mockup on 2026-11-12.

## Decisions

- One location bar and indexed folder picker; include subfolders by default, preserve an explicit folder-only choice across navigation.
- Possible duplicates (default), All files, and Trash. Separate same-byte-size and same-displayed-second duration groups; no hashing or automatic selection.
- Group list beside a comparison table; stacked on narrow screens. Filename queries select whole groups, including differently named members within the authorized scope and file type.
- Twenty groups per page, descending matching value; one hundred comparison files per page, ordered by location/name with stable ties. Missing durations are counted with explicit preparation available.
- Database-only reads; no implicit scans, probes, thumbnail generation, migrations, or changes to normal browsing beyond the Cleanup link's recursive default.
- Keep page-local signed selections, confirmations, recoverable Trash, cached thumbnails, and existing duration jobs. Separate Trash search state. Preserve legacy Cleanup links and Back/Forward navigation.

## Delivery and checks

1. Indexed group summaries/member queries with authorization, exclusions, latest-snapshot visibility and alias deduplication before grouping. Query and pagination regressions, fractional-duration boundaries, missing metadata and representative indexed-library timing.
2. Read-only groups API and member filters, strict argument validation, route authorization/no-I/O regressions.
3. Approved template/CSS/JavaScript layout and interactions, unit coverage for scope, whole-group search, group selection, stale requests, navigation/history and cleared selections. Real-browser desktop/mobile and fictional Trash/restore acceptance.
4. Focused checks then full Python/JavaScript suites, read-only independent final review, documentation. This request implements source; no new release publication or live deployment is requested.
