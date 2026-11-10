"""Local immutable directory snapshots; requests never touch media filesystems.

OS SMB calls cannot safely be killed. Timed-out workers remain occupied until
the OS returns, bounding abandoned work to the configured daemon worker count.
"""
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


FILE_TYPE_KINDS = {
    'all': (),
    'videos': ('folder', 'video'),
    'pictures': ('folder', 'image'),
    'documents': ('folder', 'pdf', 'comic', 'text'),
}


def root_id(root: Path) -> str:
    return 'root_' + hashlib.sha256(os.path.normcase(os.path.abspath(root)).encode('utf-8')).hexdigest()[:16]


def is_reserved_path(path):
    return '.lanstream-trash' in str(path).replace('\\', '/').casefold().split('/')


def _path_key(root, relative):
    return os.path.normcase(os.path.abspath(os.path.join(root, *relative.split('/'))))


def _relative(value):
    value = str(value)
    if '\\' in value or ':' in value or '\x00' in value or value.startswith('/') or '..' in value.split('/'):
        raise ValueError('Invalid relative path')
    for part in value.split('/'):
        if part in ('', '.'):
            continue
        if part.endswith(('.', ' ')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', part, re.IGNORECASE):
            raise ValueError('Invalid relative path')
    normalized = str(PurePosixPath(value)) if value else ''
    return '' if normalized == '.' else normalized


def _natural(value):
    return tuple((1, int(part)) if part.isascii() and part.isdigit() else (0, part) for part in re.split(r'(\d+)', value.casefold()))


class DirectoryIndex:
    def __init__(self, db_path, kind_for_suffix, ttl=300, workers=2, max_pending=32, scan_timeout=30, profiles=None):
        self.kind_for_suffix = kind_for_suffix
        self.ttl, self.scan_timeout = ttl, scan_timeout
        self.profiles = profiles
        self._automatic_libraries = {}
        self._next_library_refresh = 0
        self._crawl_after = ('', '')
        self.max_pending = max(1, max_pending)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._db_path = Path(db_path).absolute()
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=5)
        try:
            self._configure_queries(self._db)
            self._db.execute('PRAGMA journal_mode=WAL')
            schema = self._db.execute('PRAGMA user_version').fetchone()[0]
            if schema not in (0, 1):
                raise ValueError('Unsupported directory index schema')
            self._db.executescript('''
                CREATE TABLE IF NOT EXISTS folders(root TEXT, directory TEXT, version TEXT,
                    updated REAL, PRIMARY KEY(root,directory));
                CREATE TABLE IF NOT EXISTS entries(root TEXT, directory TEXT, version TEXT,
                    name TEXT, rel TEXT, kind TEXT, size INTEGER, mtime REAL, folded TEXT,
                    PRIMARY KEY(root,directory,version,name));
                CREATE TABLE IF NOT EXISTS favorites(profile TEXT, root TEXT, rel TEXT,
                    kind TEXT, name TEXT, PRIMARY KEY(profile,root,rel));
                CREATE INDEX IF NOT EXISTS entries_name_order ON entries
                    (root,directory,version,(kind='folder') DESC,name COLLATE NATURAL_NAME);
                CREATE TABLE IF NOT EXISTS crawls(profile TEXT,root TEXT,path TEXT,relative TEXT,
                    excluded TEXT,paused INTEGER,PRIMARY KEY(profile,root));
                CREATE TABLE IF NOT EXISTS crawl_frontier(profile TEXT,root TEXT,directory TEXT,
                    state TEXT,PRIMARY KEY(profile,root,directory));
                CREATE TABLE IF NOT EXISTS index_roots(root TEXT PRIMARY KEY,path TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS hidden_files(path TEXT PRIMARY KEY,fingerprint TEXT,
                    pending INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS hidden_aliases(root TEXT,rel TEXT,path TEXT,
                    PRIMARY KEY(root,rel));
                CREATE TABLE IF NOT EXISTS media_metadata(root TEXT,rel TEXT,size INTEGER,mtime REAL,
                    fingerprint TEXT,duration REAL,status TEXT,attempted REAL,error TEXT,
                    PRIMARY KEY(root,rel));
                UPDATE crawls SET paused=1;
                UPDATE crawl_frontier SET state='pending' WHERE state='scanning';
                PRAGMA user_version=1;
            ''')
            hidden_columns = {row['name'] for row in self._db.execute('PRAGMA table_info(hidden_files)')}
            with self._db:
                if 'fingerprint' not in hidden_columns:
                    self._db.execute('ALTER TABLE hidden_files ADD COLUMN fingerprint TEXT')
                if 'pending' not in hidden_columns:
                    self._db.execute('ALTER TABLE hidden_files ADD COLUMN pending INTEGER NOT NULL DEFAULT 0')
            self._known_roots = dict(self._db.execute('SELECT root,path FROM index_roots'))
        except Exception:
            self._db.close()
            raise
        self._pending = deque()
        self._jobs = {}
        self._hosts = set()
        self._errors = {}
        self._closed = False
        self._counts = dict(scans_started=0, scans_completed=0, scans_failed=0, cache_hits=0, rejected=0)
        self._threads = [threading.Thread(target=self._worker, daemon=True, name=f'directory-index-{i}') for i in range(max(1, workers))]
        for thread in self._threads:
            thread.start()
        self._crawl_wake = threading.Event()
        self._crawl_thread = threading.Thread(target=self._crawl_worker, daemon=True, name='directory-crawler')
        self._crawl_thread.start()

    @staticmethod
    def _configure_queries(db):
        db.row_factory = sqlite3.Row
        db.create_function('casefold', 1, lambda value: value.casefold(), deterministic=True)
        db.create_function('reserved_path', 1, is_reserved_path, deterministic=True)
        db.create_function('path_key', 2, _path_key, deterministic=True)
        db.create_collation('NATURAL_NAME', lambda a, b: (_natural(a) > _natural(b)) - (_natural(a) < _natural(b)))

    def _register_roots(self, roots):
        roots = tuple(Path(root) for root in roots)
        if all(root_id(root) in self._known_roots for root in roots):
            return roots
        with self._lock, self._db:
            for root in roots:
                rid, path = root_id(root), os.path.abspath(root)
                self._db.execute('INSERT OR IGNORE INTO index_roots VALUES(?,?)', (rid, path))
                for row in self._db.execute('SELECT path FROM hidden_files').fetchall():
                    try:
                        alias = Path(row['path']).relative_to(Path(os.path.normcase(path))).as_posix()
                    except ValueError:
                        continue
                    self._db.execute('INSERT OR IGNORE INTO hidden_aliases VALUES(?,?,?)', (rid, alias.casefold(), row['path']))
                self._known_roots[rid] = path
        return roots

    @contextmanager
    def _reader(self):
        db = sqlite3.connect(self._db_path.as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            self._configure_queries(db)
            deadline = time.monotonic() + 15
            db.set_progress_handler(lambda: self._closed or time.monotonic() > deadline, 10000)
            db.execute('BEGIN')
            yield db
        finally:
            db.close()

    @staticmethod
    def _visible_sql(alias='e'):
        return (f"instr('/'||lower(replace({alias}.rel,char(92),'/'))||'/','/.lanstream-trash/')=0 "
                f'AND {alias}.root NOT IN (SELECT root FROM index_roots WHERE reserved_path(path)) '
                f'AND (NOT EXISTS (SELECT 1 FROM hidden_files) OR NOT EXISTS '
                f'(SELECT 1 FROM hidden_aliases h WHERE h.root={alias}.root AND h.rel=casefold({alias}.rel)))')

    @staticmethod
    def _metadata_join():
        return ' LEFT JOIN media_metadata m ON m.root=e.root AND m.rel=e.rel AND m.size=e.size AND m.mtime=e.mtime '

    @staticmethod
    def _metadata_columns():
        return ",m.duration AS duration,coalesce(m.status,'missing') AS metadata_status "

    @staticmethod
    def _host(root):
        absolute = os.path.abspath(root)
        if absolute.startswith('\\\\'):
            return absolute.lstrip('\\').split('\\', 1)[0].casefold()
        return 'local'

    def _schedule(self, root, relative, *, background=False):
        self._register_roots([root])
        key = (root_id(root), relative)
        if self._closed:
            return
        if is_reserved_path(root) or is_reserved_path(relative):
            self._errors[key] = ('error', 'Reserved directory')
            return
        if key in self._jobs:
            if not background:
                self._jobs[key]['background'] = False
            return
        if len(self._pending) >= self.max_pending:
            displaced = next((job for job in reversed(self._pending) if job['background']), None) if not background else None
            if displaced is None:
                self._counts['rejected'] += 1
                return
            self._pending.remove(displaced)
            self._jobs.pop(displaced['key'])
            with self._db:
                self._db.execute("UPDATE crawl_frontier SET state='pending' WHERE root=? AND directory=? AND state='scanning'", displaced['key'])
        job = dict(key=key, root=Path(root), relative=relative, host=self._host(root), expired=False, finished=False, background=background)
        self._jobs[key] = job
        self._pending.append(job)
        self._condition.notify_all()

    def _worker(self):
        while True:
            with self._condition:
                job = None
                while job is None:
                    if self._closed:
                        return
                    for candidate in sorted(self._pending, key=lambda item: item['background']):
                        if candidate['host'] not in self._hosts:
                            job = candidate
                            self._pending.remove(job)
                            self._hosts.add(job['host'])
                            break
                    if job is None:
                        self._condition.wait()
                self._counts['scans_started'] += 1
            timer = threading.Timer(self.scan_timeout, self._timeout, args=(job,))
            timer.daemon = True
            timer.start()
            try:
                self._scan(job)
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
                with self._condition:
                    if not job['expired']:
                        job['finished'] = True
                        self._errors[job['key']] = ('error' if isinstance(exc, (ValueError, sqlite3.Error)) else 'offline', 'Directory unavailable. Try refreshing later.')
                        self._counts['scans_failed'] += 1
            finally:
                timer.cancel()
                with self._condition:
                    self._jobs.pop(job['key'], None)
                    self._hosts.discard(job['host'])
                    if job.get('refresh_after') and not self._closed:
                        self._schedule(job['root'], job['relative'])
                    self._condition.notify_all()
                    self._crawl_wake.set()

    def _timeout(self, job):
        with self._condition:
            if job['key'] in self._jobs and not job['finished']:
                job['expired'] = True
                self._errors[job['key']] = ('error', 'Directory scan timed out. Try refreshing later.')
                self._counts['scans_failed'] += 1

    @staticmethod
    def _safe_target(root, relative):
        # Check every configured-root descendant before enumeration, including junctions.
        base = root.resolve(strict=True)
        target = root
        for part in PurePosixPath(relative).parts:
            target = target / part
            metadata = target.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Unsafe directory')
        target.resolve(strict=True).relative_to(base)
        return target

    def _scan(self, job):
        if job['expired'] or self._closed:
            return
        rid, directory = job['key']
        version = uuid.uuid4().hex
        batch, replacements = [], {}
        with self._lock:
            hidden = {row['rel']: row for row in self._db.execute(
                'SELECT a.rel,a.path,h.fingerprint,h.pending FROM hidden_aliases a '
                'JOIN hidden_files h ON h.path=a.path WHERE a.root=?', (rid,)).fetchall()
                if row['rel'].rpartition('/')[0] == directory.casefold()}
        target = self._safe_target(job['root'], job['relative'])
        try:
            with os.scandir(target) as entries:
                for entry in entries:
                    if job['expired'] or self._closed:
                        return
                    if is_reserved_path(entry.name) or entry.is_symlink():
                        continue
                    metadata = entry.stat(follow_symlinks=False)
                    if getattr(metadata, 'st_file_attributes', 0) & 0x400:
                        continue
                    kind = 'folder' if stat.S_ISDIR(metadata.st_mode) else self.kind_for_suffix(Path(entry.name).suffix.lower()) if stat.S_ISREG(metadata.st_mode) else None
                    if kind is None:
                        continue
                    relative = '/'.join(filter(None, (directory, entry.name)))
                    tombstone = hidden.get(relative.casefold())
                    if tombstone:
                        expected = json.loads(tombstone['fingerprint']) if tombstone['fingerprint'] else None
                        if tombstone['pending'] or not expected:
                            continue
                        if any(expected.get(field) and not getattr(metadata, 'st_' + field, 0) for field in ('ino', 'dev')):
                            # Windows DirEntry.stat omits file IDs; check only confirmed tombstone candidates.
                            metadata = os.stat(target / entry.name, follow_symlinks=False)
                            if not stat.S_ISREG(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
                                continue
                        if (any(not hasattr(metadata, 'st_' + field) for field in expected)
                                or all(getattr(metadata, 'st_' + field) == value for field, value in expected.items())):
                            continue
                        replacements[tombstone['path']] = tombstone['fingerprint']
                    batch.append((rid, directory, version, entry.name, relative, kind, metadata.st_size, metadata.st_mtime, entry.name.casefold()))
                    if len(batch) == 256:
                        self._insert(batch)
                        batch.clear()
            if batch:
                self._insert(batch)
            with self._condition:
                if job['expired'] or self._closed:
                    return
                with self._db:
                    for physical, fingerprint in replacements.items():
                        current = self._db.execute('SELECT 1 FROM hidden_files WHERE path=? AND fingerprint=? AND pending=0',
                                                   (physical, fingerprint)).fetchone()
                        if current:
                            self._invalidate_path(physical, keep=(rid, directory, version))
                            self._db.execute('DELETE FROM hidden_aliases WHERE path=?', (physical,))
                            self._db.execute('DELETE FROM hidden_files WHERE path=?', (physical,))
                    removed = self._db.execute("SELECT e.rel FROM entries e JOIN folders f ON e.root=f.root AND e.directory=f.directory AND e.version=f.version WHERE e.root=? AND e.directory=? AND e.kind='folder' AND NOT EXISTS(SELECT 1 FROM entries n WHERE n.root=e.root AND n.directory=e.directory AND n.version=? AND n.name=e.name AND n.kind='folder')", (rid, directory, version)).fetchall()
                    for row in removed:
                        relative, prefix = row['rel'], row['rel'] + '/'
                        for table, column in (('entries', 'directory'), ('folders', 'directory'), ('crawl_frontier', 'directory')):
                            self._db.execute(f'DELETE FROM {table} WHERE root=? AND ({column}=? OR substr({column},1,?)=?)', (rid, relative, len(prefix), prefix))
                        for key, pending in self._jobs.items():
                            if key[0] == rid and (key[1] == relative or key[1].startswith(prefix)):
                                pending['expired'] = True
                    self._db.execute('INSERT OR REPLACE INTO folders VALUES(?,?,?,?)', (rid, directory, version, time.time()))
                    self._db.execute('DELETE FROM entries WHERE root=? AND directory=? AND version<>?', (rid, directory, version))
                self._errors.pop(job['key'], None)
                job['finished'] = True
                self._counts['scans_completed'] += 1
        finally:
            with self._lock:
                if not self._closed:
                    with self._db:
                        self._db.execute('DELETE FROM entries WHERE root=? AND directory=? AND version=? AND NOT EXISTS (SELECT 1 FROM folders WHERE root=? AND directory=? AND version=?)', (rid, directory, version, rid, directory, version))

    def _insert(self, batch):
        with self._lock:
            if not self._closed:
                with self._db:
                    self._db.executemany('INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)', batch)

    @staticmethod
    def _exclusion_sql(root, excluded, alias='e'):
        clauses, params = [], []
        for value in excluded:
            raw = str(value)
            if Path(raw).is_absolute():
                try:
                    absolute = os.path.abspath(raw)
                    absolute_root = os.path.abspath(root)
                    if os.path.normcase(os.path.commonpath((absolute, absolute_root))) == os.path.normcase(absolute):
                        raw = ''
                    else:
                        raw = os.path.relpath(absolute, absolute_root)
                except ValueError:
                    continue
                if raw == '..' or raw.startswith('..' + os.sep):
                    continue
                raw = raw.replace(os.sep, '/')
            relative = _relative(raw)
            folded = '' if relative == '.' else relative.casefold()
            clauses.append(f'NOT (casefold({alias}.rel)=? OR substr(casefold({alias}.rel),1,?)=?)')
            prefix = folded + '/' if folded else ''
            params.extend((folded, len(prefix), prefix))
        return clauses, params

    def _page(self, sql, params, page, page_size, sort='name', descending=False, file_type='all', include_duration=False, db=None):
        db = db or self._db
        page, page_size = max(1, int(page)), min(500, max(1, int(page_size)))
        kinds = FILE_TYPE_KINDS[file_type]
        if kinds:
            sql += ' AND e.kind IN (' + ','.join('?' for _ in kinds) + ')'
            params = [*params, *kinds]
        sql += ' AND ' + self._visible_sql()
        field = {'name': 'e.name COLLATE NATURAL_NAME', 'date': 'e.mtime', 'mtime': 'e.mtime', 'size': 'e.size', 'type': 'e.kind', 'duration': 'm.duration'}.get(sort, 'e.name COLLATE NATURAL_NAME')
        total = db.execute('SELECT count(*) ' + sql, params).fetchone()[0]
        page = min(page, max(1, (total + page_size - 1) // page_size))
        unknown = '(m.duration IS NULL),' if sort == 'duration' else ''
        rows = db.execute('SELECT e.* ' + (self._metadata_columns() if include_duration else '') + sql + f" ORDER BY (e.kind='folder') DESC,{unknown}{field} {'DESC' if descending else 'ASC'},e.name COLLATE BINARY,e.rel,e.root LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
        items = [{key: row[key] for key in ('name', 'rel', 'kind', 'size', 'mtime')} | {'root_id': row['root'], 'parent': row['rel'].rpartition('/')[0]} for row in rows]
        if include_duration:
            for item, row in zip(items, rows):
                item.update(duration=row['duration'], metadata_status=row['metadata_status'])
        return dict(items=items, total=total, page=page, page_size=page_size, pages=(total + page_size - 1) // page_size)

    def listing(self, root, relative='', *, page=1, page_size=100, sort='name', descending=False, query='', excluded=(), refresh=False, file_type='all', include_duration=False):
        self._register_roots([root])
        include_duration = include_duration or sort == 'duration'
        relative = _relative(relative)
        if relative == '.':
            relative = ''
        rid = root_id(root)
        key = rid, relative
        with self._condition:
            folder = self._db.execute('SELECT * FROM folders WHERE root=? AND directory=?', key).fetchone()
            stale = folder is not None and time.time() - folder['updated'] >= self.ttl
            if refresh or ((folder is None or stale) and key not in self._errors):
                self._schedule(root, relative)
            if folder:
                self._counts['cache_hits'] += 1
            clauses = ['e.root=?', 'e.directory=?', 'e.version=?', 'instr(e.folded,?)>0']
            params = [rid, relative, folder['version'] if folder else '', query.casefold()]
            extra, values = self._exclusion_sql(root, excluded)
            # Visibility filters otherwise make SQLite sort the entire folder for name pages.
            source = 'FROM entries e INDEXED BY entries_name_order ' if sort == 'name' else 'FROM entries e '
            result = self._page(source + (self._metadata_join() if include_duration else '') + ' WHERE ' + ' AND '.join(clauses + extra), params + values, page, page_size, sort, descending, file_type, include_duration)
            error_state, error = self._errors.get(key, (None, None))
            result.update(version=folder['version'] if folder else None, updated_at=folder['updated'] if folder else None, state=error_state or ('stale' if stale else 'ready' if folder else 'loading'), refreshing=key in self._jobs, error=error)
            return result

    def thumbnail_sources(self, root, relative='', excluded=()):
        self._register_roots([root])
        relative, after = _relative(relative), ''
        extra, values = self._exclusion_sql(root, excluded)
        while True:
            with self._lock:
                rows = self._db.execute(
                    'SELECT e.rel FROM entries e JOIN folders f ON e.root=f.root AND e.directory=f.directory '
                    'AND e.version=f.version WHERE e.root=? AND e.directory=? AND e.rel COLLATE BINARY>? '
                    "AND e.kind IN ('video','image','pdf','comic') "
                    + ' AND ' + self._visible_sql()
                    + ''.join(' AND ' + clause for clause in extra)
                    + ' ORDER BY e.rel COLLATE BINARY LIMIT 100',
                    [root_id(root), relative, after, *values]).fetchall()
            if not rows:
                return
            for row in rows:
                yield row['rel']
            after = rows[-1]['rel']

    def search(self, roots, *, query, excluded=None, scope_root=None, scope_relative='', recursive=True, page=1, page_size=100, file_type='all'):
        roots = self._register_roots(roots)
        scope_relative = _relative(scope_relative)
        scope_relative = '' if scope_relative == '.' else scope_relative
        excluded = excluded or {}
        with self._lock:
            clauses, params, coverage = [], [], []
            for root in roots:
                if is_reserved_path(root):
                    continue
                rid = root_id(root)
                if scope_root is not None and rid != (scope_root if isinstance(scope_root, str) and scope_root.startswith('root_') else root_id(scope_root)):
                    continue
                extra, extra_values = self._exclusion_sql(root, excluded.get(rid, ()))
                part, values = self._root_scope(root, excluded, scope_relative, recursive)
                clauses.append('(' + ' AND '.join(part) + ')')
                params.extend(values)
                folder_clauses = ['f.root=?', 'NOT reserved_path(f.directory)', *(clause.replace('e.rel', 'f.directory') for clause in extra)]
                self._db.create_function('casefold', 1, lambda value: value.casefold(), deterministic=True)
                folders = self._db.execute('SELECT count(*),max(updated) FROM folders f WHERE ' + ' AND '.join(folder_clauses), [rid, *extra_values]).fetchone()
                count = self._db.execute('SELECT count(*) FROM entries e JOIN folders f ON e.root=f.root AND e.directory=f.directory AND e.version=f.version WHERE ' + ' AND '.join(part) + ' AND ' + self._visible_sql(), values).fetchone()[0]
                coverage.append(dict(root_id=rid, folders=folders[0], items=count, updated_at=folders[1], refreshing=any(key[0] == rid for key in self._jobs)))
            sql = 'FROM entries e JOIN folders f ON e.root=f.root AND e.directory=f.directory AND e.version=f.version WHERE (' + (' OR '.join(clauses) or '0') + ') AND instr(e.folded,?)>0'
            result = self._page(sql, params + [query.casefold()], page, page_size, file_type=file_type)
            result.update(coverage=coverage, state='ready', refreshing=any(x['refreshing'] for x in coverage), version=None, updated_at=max((x['updated_at'] or 0 for x in coverage), default=0), error=None)
            return result

    def _root_scope(self, root, excluded, relative, recursive):
        rid = root_id(root)
        extra, values = self._exclusion_sql(root, excluded.get(rid, ()))
        clauses, values = ['e.root=?', *extra], [rid, *values]
        if relative:
            clauses.append('substr(e.rel,1,?)=?' if recursive else 'e.directory=?')
            values.extend((len(relative) + 1, relative + '/') if recursive else (relative,))
        elif not recursive:
            clauses.append("e.directory=''")
        return clauses, values

    def _cleanup_source(self, roots, excluded, scope_root, scope_relative, recursive, query='', file_type='all'):
        relative = _relative(scope_relative)
        scope_id = scope_root if isinstance(scope_root, str) and scope_root.startswith('root_') else root_id(scope_root) if scope_root is not None else None
        clauses, params = [], []
        selected = []
        for root in roots:
            if is_reserved_path(root) or (scope_id is not None and root_id(root) != scope_id):
                continue
            part, values = self._root_scope(root, excluded or {}, relative, recursive)
            clauses.append('(' + ' AND '.join(part) + ')')
            params.extend(values)
            selected.append(root_id(root))
        sql = ("WITH authorized AS (SELECT e.*" + self._metadata_columns()
               + ',path_key(r.path,e.rel) AS physical FROM entries e '
               'JOIN folders f ON e.root=f.root AND e.directory=f.directory AND e.version=f.version '
               'JOIN index_roots r ON r.root=e.root ' + self._metadata_join()
               + " WHERE e.kind<>'folder' AND " + self._visible_sql()
               + ' AND (' + (' OR '.join(clauses) or '0') + ') AND instr(e.folded,?)>0')
        params.append(query.casefold())
        kinds = tuple(kind for kind in FILE_TYPE_KINDS[file_type] if kind != 'folder')
        if kinds:
            sql += ' AND e.kind IN (' + ','.join('?' for _ in kinds) + ')'
            params.extend(kinds)
        sql += (') , ranked AS (SELECT *,row_number() OVER (PARTITION BY physical ORDER BY root,rel) AS alias_rank '
                'FROM authorized), files AS (SELECT * FROM ranked WHERE alias_rank=1) ')
        return sql, params, selected

    def cleanup(self, roots, *, query='', excluded=None, scope_root=None, scope_relative='', recursive=True,
                page=1, page_size=100, sort='name', descending=False, file_type='all', matching_sizes=False):
        roots = self._register_roots(roots)
        sql, params, selected = self._cleanup_source(roots, excluded, scope_root, scope_relative, recursive, query, file_type)
        sql += ', counted AS (SELECT *,count(*) OVER (PARTITION BY size) AS size_count FROM files) '
        where = ' WHERE size_count>1' if matching_sizes else ''
        field = {'name': 'name COLLATE NATURAL_NAME', 'location': 'parent COLLATE NATURAL_NAME',
                 'size': 'size', 'duration': 'duration', 'date': 'mtime', 'mtime': 'mtime', 'type': 'kind'}.get(sort, 'name COLLATE NATURAL_NAME')
        order = f"{field} {'DESC' if descending else 'ASC'},name COLLATE NATURAL_NAME,rel,root"
        if sort == 'duration':
            order = '(duration IS NULL),' + order
        if matching_sizes:
            order = f"size {'DESC' if descending else 'ASC'},rel COLLATE NATURAL_NAME,root"
        # Parent is derived in SQL so location sorting covers all results before paging.
        source = '(SELECT *,substr(rel,1,length(rel)-length(name)-1) AS parent FROM counted)'
        page, page_size = max(1, int(page)), min(500, max(1, int(page_size)))
        with self._reader() as db:
            total = db.execute(sql + 'SELECT count(*) FROM counted' + where, params).fetchone()[0]
            page = min(page, max(1, (total + page_size - 1) // page_size))
            rows = db.execute(sql + 'SELECT * FROM ' + source + where + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?',
                              [*params, page_size, (page - 1) * page_size]).fetchall()
            coverage = []
            for rid in selected:
                root = next(root for root in roots if root_id(root) == rid)
                extra, values = self._exclusion_sql(root, (excluded or {}).get(rid, ()), alias='f')
                extra = [clause.replace('f.rel', 'f.directory') for clause in extra]
                summary = db.execute('SELECT count(*),max(updated),min(updated) FROM folders f WHERE f.root=?'
                                     + ''.join(' AND ' + clause for clause in extra)
                                     + ' AND NOT reserved_path(f.directory)', [rid, *values]).fetchone()
                coverage.append(dict(root_id=rid, folders=summary[0], updated_at=summary[1],
                                     state='stale' if summary[0] and time.time() - summary[2] >= self.ttl else 'ready' if summary[0] else 'loading',
                                     refreshing=any(key[0] == rid for key in tuple(self._jobs))))
        items = [{key: row[key] for key in ('name', 'rel', 'kind', 'size', 'mtime', 'duration', 'metadata_status', 'version', 'size_count')}
                 | {'root_id': row['root'], 'parent': row['rel'].rpartition('/')[0]} for row in rows]
        failures = [value for key, value in tuple(self._errors.items()) if key[0] in selected]
        updated = max((row['updated_at'] or 0 for row in coverage), default=0)
        state = 'stale' if any(row['state'] == 'stale' for row in coverage) else 'ready' if any(row['folders'] for row in coverage) else 'loading'
        return dict(items=items, total=total, page=page, page_size=page_size, pages=(total + page_size - 1) // page_size,
                    coverage=coverage, state=failures[0][0] if failures else state, refreshing=any(row['refreshing'] for row in coverage),
                    updated_at=updated, version=None, error=failures[0][1] if failures else None)

    def cleanup_folders(self, root, relative='', *, excluded=(), page=1, page_size=100):
        self._register_roots([root])
        relative = _relative(relative)
        extra, values = self._exclusion_sql(root, excluded)
        with self._reader() as db:
            folder = db.execute('SELECT version,updated FROM folders WHERE root=? AND directory=?', (root_id(root), relative)).fetchone()
            result = self._page('FROM entries e JOIN folders f ON e.root=f.root AND e.directory=f.directory AND e.version=f.version '
                                "WHERE e.root=? AND e.directory=? AND e.kind='folder'"
                                + ''.join(' AND ' + clause for clause in extra),
                                [root_id(root), relative, *values], page, page_size, db=db)
        result.update(state='ready', refreshing=False, version=folder['version'] if folder else None,
                      updated_at=folder['updated'] if folder else None, error=None)
        return result

    def save_duration(self, root, relative, fingerprint, duration, error='', *, require_entry=True):
        self._register_roots([root])
        relative = _relative(relative)
        if is_reserved_path(root) or is_reserved_path(relative):
            return False
        if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration < 0):
            raise ValueError('Invalid duration')
        if not require_entry and duration is None:
            return False
        status = 'ready' if duration is not None else 'failed'
        if duration is None and not error:
            error = 'Duration unavailable'
        fingerprint = {key: fingerprint[key] for key in ('size', 'mtime', 'mtime_ns', 'ctime_ns', 'ino', 'dev')}
        with self._lock, self._db:
            if require_entry:
                current = self._db.execute('SELECT 1 FROM entries e JOIN folders f ON e.root=f.root AND e.directory=f.directory AND e.version=f.version '
                                          "WHERE e.root=? AND e.rel=? AND e.kind='video' AND e.size=? AND e.mtime=? AND " + self._visible_sql(),
                                          (root_id(root), relative, fingerprint['size'], fingerprint['mtime'])).fetchone()
                if not current:
                    return False
            self._db.execute('INSERT OR REPLACE INTO media_metadata VALUES(?,?,?,?,?,?,?,?,?)',
                             (root_id(root), relative, fingerprint['size'], fingerprint['mtime'], json.dumps(fingerprint),
                              duration, status, time.time(), str(error)[:500]))
        return True

    def duration_candidates(self, roots, *, excluded=None, scope_root=None, scope_relative='', recursive=True, retry_failed=False, batch_size=100):
        roots = self._register_roots(roots)
        sql, params, _ = self._cleanup_source(roots, excluded, scope_root, scope_relative, recursive, file_type='videos')
        batch_size = min(500, max(1, int(batch_size)))
        after = ('', '')
        while True:
            with self._reader() as db:
                rows = db.execute(sql + "SELECT root,rel,size,mtime,version FROM files WHERE (metadata_status='missing'"
                                  + (" OR metadata_status='failed'" if retry_failed else '')
                                  + ') AND (root>? OR (root=? AND rel>?)) ORDER BY root,rel LIMIT ?',
                                  [*params, after[0], *after, batch_size]).fetchall()
            if not rows:
                return
            for row in rows:
                yield dict(root_id=row['root'], rel=row['rel'], size=row['size'], mtime=row['mtime'], version=row['version'])
            after = rows[-1]['root'], rows[-1]['rel']

    def _invalidate_path(self, physical, keep=None):
        aliases = []
        for rid, path in self._known_roots.items():
            try:
                alias = Path(physical).relative_to(Path(os.path.normcase(path))).as_posix()
            except ValueError:
                continue
            directory = alias.rpartition('/')[0]
            sql = 'DELETE FROM entries WHERE root=? AND casefold(rel)=?'
            self._db.execute(sql + (' AND NOT (root=? AND directory=? AND version=?)' if keep else ''),
                             (rid, alias.casefold(), *keep) if keep else (rid, alias.casefold()))
            self._db.execute('DELETE FROM media_metadata WHERE root=? AND casefold(rel)=?', (rid, alias.casefold()))
            self._db.execute('UPDATE folders SET updated=0 WHERE root=? AND casefold(directory)=?', (rid, directory.casefold()))
            for key, job in self._jobs.items():
                if key[0] == rid and key[1].casefold() == directory.casefold() and (not keep or key != keep[:2]):
                    job['expired'] = True
            aliases.append((rid, path, alias, directory))
        return aliases

    def hide_file(self, root, relative, hidden=True, *, fingerprint=None, pending=False):
        self._register_roots([root])
        relative = _relative(relative)
        physical = _path_key(os.path.abspath(root), relative)
        with self._condition, self._db:
            existing = self._db.execute('SELECT fingerprint FROM hidden_files WHERE path=?', (physical,)).fetchone()
            matches = True
            if existing and fingerprint is not None and (not hidden or not pending):
                # Only a new pending move can claim a path already owned by a different file.
                matches = bool(existing['fingerprint']) and json.loads(existing['fingerprint']) == fingerprint
                if hidden and not matches:
                    return
            if hidden:
                if fingerprint is not None:
                    fingerprint = json.dumps(fingerprint)
                elif existing:
                    fingerprint = existing['fingerprint']
                else:
                    snapshot = self._db.execute('SELECT e.size,e.mtime FROM entries e JOIN folders f '
                                                'ON e.root=f.root AND e.directory=f.directory AND e.version=f.version '
                                                'WHERE e.root=? AND casefold(e.rel)=?', (root_id(root), relative.casefold())).fetchone()
                    fingerprint = json.dumps(dict(snapshot)) if snapshot else None
                self._db.execute('INSERT OR REPLACE INTO hidden_files(path,fingerprint,pending) VALUES(?,?,?)',
                                 (physical, fingerprint, int(pending)))
            elif matches:
                self._db.execute('DELETE FROM hidden_files WHERE path=?', (physical,))
                self._db.execute('DELETE FROM hidden_aliases WHERE path=?', (physical,))
            for rid, path, alias, directory in self._invalidate_path(physical):
                if hidden:
                    self._db.execute('INSERT OR REPLACE INTO hidden_aliases VALUES(?,?,?)', (rid, alias.casefold(), physical))
                elif (rid, directory) in self._jobs:
                    self._jobs[(rid, directory)]['refresh_after'] = True
                else:
                    self._schedule(Path(path), directory)
            self._condition.notify_all()

    def set_favorite(self, profile_id, root, relative, kind, name, enabled):
        self._register_roots([root])
        relative = _relative(relative)
        with self._lock, self._db:
            if enabled:
                self._db.execute('INSERT OR REPLACE INTO favorites VALUES(?,?,?,?,?)', (str(profile_id), root_id(root), relative, kind, name))
            else:
                self._db.execute('DELETE FROM favorites WHERE profile=? AND root=? AND rel=?', (str(profile_id), root_id(root), relative))

    def favorites(self, profile_id, roots, *, excluded=None, page=1, page_size=100, file_type='all'):
        roots = self._register_roots(roots)
        excluded = excluded or {}
        with self._lock:
            clauses, params = [], []
            for root in roots:
                rid = root_id(root)
                extra, values = self._exclusion_sql(root, excluded.get(rid, ()))
                clauses.append('(' + ' AND '.join(['e.root=?', *extra]) + ')')
                params.extend([rid, *values])
            sql = 'FROM (SELECT root,rel,kind,name,0 AS size,NULL AS mtime FROM favorites WHERE profile=?) e WHERE (' + (' OR '.join(clauses) or '0') + ')'
            result = self._page(sql, [str(profile_id), *params], page, page_size, file_type=file_type)
            result.update(state='ready', refreshing=False, version=None, updated_at=None, error=None)
            return result

    def favorite_flags(self, profile_id, items):
        """Mark one bounded response page without reading media or all favorites."""
        if len(items) > 500:
            raise ValueError('Favorite lookup exceeds page limit')
        keys = [(item['root_id'], _relative(item['rel'])) for item in items]
        if not keys:
            return []
        with self._lock:
            clauses = ' OR '.join('(root=? AND rel=?)' for _ in keys)
            rows = self._db.execute('SELECT root,rel FROM favorites e WHERE profile=? AND (' + clauses + ') AND ' + self._visible_sql(), [str(profile_id), *(value for key in keys for value in key)]).fetchall()
            found = {(row['root'], row['rel']) for row in rows}
            return [key in found for key in keys]

    def metrics(self):
        with self._lock:
            return dict(self._counts, pending=len(self._pending), active=len(self._hosts), in_flight=len(self._jobs))

    def _refresh_libraries(self):
        try:
            configured = {(str(profile['id']), root_id(root)): (Path(root), tuple(map(str, profile['excluded'])))
                          for profile in self.profiles() for root in profile['roots']}
            for root, excluded in configured.values():
                self._exclusion_sql(root, excluded)
        except (OSError, UnicodeError, ValueError):
            logging.getLogger(__name__).warning('Unable to read library configuration; automatic indexing will retry.')
            return
        with self._condition:
            if self._closed:
                return
            with self._db:
                for row in self._db.execute('SELECT profile,root FROM crawls').fetchall():
                    identity = (row['profile'], row['root'])
                    if identity not in configured:
                        self._db.execute('DELETE FROM crawl_frontier WHERE profile=? AND root=?', identity)
                        self._db.execute('DELETE FROM crawls WHERE profile=? AND root=?', identity)
            roots = {identity[1] for identity in configured}
            for key, job in self._jobs.items():
                if key[0] not in roots:
                    job['expired'] = True
            for identity, (root, excluded) in configured.items():
                crawl = self._db.execute('SELECT relative,paused FROM crawls WHERE profile=? AND root=?', identity).fetchone()
                unfinished = self._db.execute("SELECT 1 FROM crawl_frontier WHERE profile=? AND root=? AND state IN ('pending','scanning') LIMIT 1", identity).fetchone()
                changed = self._automatic_libraries.get(identity) != (root, excluded)
                if changed or not crawl or crawl['relative'] or (not crawl['paused'] and not unfinished):
                    self.crawl_start(identity[0], root, excluded=excluded)
            self._automatic_libraries = configured

    def crawl_start(self, profile_id, root, relative='', excluded=()):
        relative = _relative(relative)
        rid = root_id(root)
        with self._condition, self._db:
            previous = self._db.execute('SELECT relative,excluded FROM crawls WHERE profile=? AND root=?', (str(profile_id), rid)).fetchone()
            exclusions = json.dumps([str(value) for value in excluded])
            unfinished = self._db.execute("SELECT 1 FROM crawl_frontier WHERE profile=? AND root=? AND state IN ('pending','scanning') LIMIT 1", (str(profile_id), rid)).fetchone()
            if previous and (previous['relative'] != relative or previous['excluded'] != exclusions or not unfinished):
                self._db.execute('DELETE FROM crawl_frontier WHERE profile=? AND root=?', (str(profile_id), rid))
                self._db.execute('UPDATE folders SET updated=0 WHERE root=? AND (directory=? OR substr(directory,1,?)=?)', (rid, relative, len(relative) + 1 if relative else 0, relative + '/' if relative else ''))
            self._db.execute('INSERT OR REPLACE INTO crawls VALUES(?,?,?,?,?,0)', (str(profile_id), rid, os.path.abspath(root), relative, exclusions))
            self._db.execute("UPDATE crawl_frontier SET state='pending' WHERE profile=? AND root=? AND state='error'", (str(profile_id), rid))
            self._db.execute("INSERT OR IGNORE INTO crawl_frontier VALUES(?,?,?,'pending')", (str(profile_id), rid, relative))
        self._crawl_wake.set()
        return self.crawl_status(profile_id, [root])[0]

    def crawl_pause(self, profile_id, root, paused=True):
        with self._lock, self._db:
            self._db.execute('UPDATE crawls SET paused=? WHERE profile=? AND root=?', (int(paused), str(profile_id), root_id(root)))
        self._crawl_wake.set()

    def crawl_status(self, profile_id, roots):
        result = []
        with self._lock:
            for root in roots:
                rid = root_id(root)
                crawl = self._db.execute('SELECT relative,paused FROM crawls WHERE profile=? AND root=?', (str(profile_id), rid)).fetchone()
                if not crawl:
                    continue
                states = dict(self._db.execute('SELECT state,count(*) FROM crawl_frontier WHERE profile=? AND root=? GROUP BY state', (str(profile_id), rid)).fetchall())
                pending, scanning = states.get('pending', 0), states.get('scanning', 0)
                result.append(dict(root_id=rid, relative=crawl['relative'], paused=bool(crawl['paused']), running=not bool(crawl['paused']) and bool(pending + scanning), completed=states.get('done', 0), pending=pending, scanning=scanning, errors=states.get('error', 0), automatic=self.profiles is not None))
        return result

    def _crawl_worker(self):
        while True:
            self._crawl_wake.wait(.1)
            self._crawl_wake.clear()
            if self.profiles is not None and time.monotonic() >= self._next_library_refresh:
                self._next_library_refresh = time.monotonic() + max(.1, self.ttl)
                self._refresh_libraries()
            # One coordinator shares the bounded scan queue and never enumerates files itself.
            with self._condition:
                if self._closed:
                    return
                busy = tuple({key[0] for key in self._jobs})
                available = ' AND c.root NOT IN (' + ','.join('?' for _ in busy) + ')' if busy else ''
                rows = self._db.execute("SELECT c.*,f.directory,f.state FROM crawls c JOIN crawl_frontier f ON c.profile=f.profile AND c.root=f.root WHERE c.paused=0 AND f.state IN ('pending','scanning')" + available + " ORDER BY (c.profile<? OR (c.profile=? AND c.root<=?)),c.profile,c.root,f.state DESC LIMIT 1", (*busy, self._crawl_after[0], *self._crawl_after)).fetchall()
                if not rows:
                    continue
                row = rows[0]
                self._crawl_after = (row['profile'], row['root'])
                key = row['root'], row['directory']
                identity = row['profile'], row['root'], row['directory']
                if key in self._jobs:
                    continue
                exclusions = json.loads(row['excluded'])
                extra, values = self._exclusion_sql(Path(row['path']), exclusions)
                allowed = self._db.execute('SELECT 1 FROM (SELECT ? AS rel) e WHERE ' + (' AND '.join(extra) or '1'), [row['directory'], *values]).fetchone()
                if not allowed:
                    with self._db:
                        self._db.execute('DELETE FROM crawl_frontier WHERE profile=? AND root=? AND directory=?', identity)
                    continue
                folder = self._db.execute('SELECT updated FROM folders WHERE root=? AND directory=?', key).fetchone()
                if row['state'] == 'pending' and (not folder or time.time() - folder['updated'] >= self.ttl):
                    self._errors.pop(key, None)
                    self._schedule(Path(row['path']), row['directory'], background=True)
                    if key in self._jobs:
                        with self._db:
                            self._db.execute("UPDATE crawl_frontier SET state='scanning' WHERE profile=? AND root=? AND directory=?", identity)
                    continue
                if key in self._errors or not folder:
                    with self._db:
                        self._db.execute("UPDATE crawl_frontier SET state='error' WHERE profile=? AND root=? AND directory=?", identity)
                    continue
                with self._db:
                    sql = "INSERT OR IGNORE INTO crawl_frontier SELECT ?,e.root,e.rel,'pending' FROM entries e JOIN folders f ON e.root=f.root AND e.directory=f.directory AND e.version=f.version WHERE e.root=? AND e.directory=? AND e.kind='folder'"
                    sql += ' AND ' + self._visible_sql()
                    if extra:
                        sql += ' AND ' + ' AND '.join(extra)
                    self._db.execute(sql, [row['profile'], *key, *values])
                    self._db.execute("UPDATE crawl_frontier SET state='done' WHERE profile=? AND root=? AND directory=?", identity)

    def wait_for_idle(self, timeout=30):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._jobs:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._crawl_wake.set()
            self._pending.clear()
            self._condition.notify_all()
            self._db.close()
