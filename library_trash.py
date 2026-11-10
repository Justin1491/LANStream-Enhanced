"""Recoverable, Windows-only media moves. All share I/O runs on one worker.

The journal is durable product data, independent of the disposable directory index.
On-share manifests retain enough information to recover after journal loss.
"""
import contextlib
import ctypes
import hashlib
import json
import math
import os
import queue
import re
import sqlite3
import stat
import threading
import time
import uuid
from pathlib import Path

from directory_index import root_id

TRASH = '.lanstream-trash'
FIELDS = ('size', 'mtime', 'mtime_ns', 'ctime_ns', 'ino', 'dev')
TERMINAL = ('restored', 'purged')


class Conflict(Exception):
    pass


def fingerprint(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise Conflict('Unsafe file')
    return dict(zip(FIELDS, (info.st_size, info.st_mtime, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_dev)))


def _relative(value, empty=False):
    if not isinstance(value, str) or (not value and not empty):
        raise Conflict('Invalid path')
    if any(c in value for c in ('\\', ':', '\x00')) or value.startswith('/'):
        raise Conflict('Invalid path')
    for part in value.split('/') if value else ():
        if part in ('', '.', '..') or part.casefold() == TRASH or part.endswith(('.', ' ')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?', part, re.I):
            raise Conflict('Invalid path')
    return value


def _root(root):
    path = Path(os.path.abspath(root))
    if any(p.casefold() == TRASH or p.endswith(('.', ' ')) for p in path.parts[1:]):
        raise Conflict('Invalid library')
    return path


def safe_regular_path(root, relative):
    """Worker-side no-link validation; mutation additionally holds native handles."""
    root = _root(root)
    path = root / _relative(relative)
    for parent in [*reversed(path.parents), path]:
        info = os.lstat(parent)
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise Conflict('Unsafe path')
    fingerprint(path)
    return path


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Invalid identifier')
    return value


def _error(exc):
    if isinstance(exc, Conflict):
        return str(exc)
    if isinstance(exc, PermissionError):
        return 'Access unavailable'
    if isinstance(exc, FileNotFoundError):
        return 'File or folder unavailable'
    if isinstance(exc, FileExistsError):
        return 'Destination already exists'
    return 'Operation could not complete; check availability or recover Trash records'


if os.name == 'nt':
    from ctypes import wintypes
    _win = ctypes.WinDLL('kernel32', use_last_error=True)
    _win.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    _win.CreateFileW.restype = wintypes.HANDLE
    _win.CloseHandle.argtypes = (wintypes.HANDLE,)
    _win.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _win.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    _win.CreateMutexW.restype = wintypes.HANDLE
    _win.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _win.ReleaseMutex.argtypes = (wintypes.HANDLE,)


def _native(path):
    value = os.path.abspath(path)
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


@contextlib.contextmanager
def _handle(path, *, directory=False, exclusive=False, create=False, metadata=False):
    # Denying write/delete sharing prevents replacement while identity is checked.
    access = 0x80 if directory or metadata else 0x10080
    handle = _win.CreateFileW(_native(path), access, 0 if exclusive else (3 if directory else 1), None,
                              4 if create else 3, 0x2200000 if directory else 0x200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise Conflict('Unsafe path')
        if directory and not stat.S_ISDIR(info.st_mode):
            raise Conflict('Unsafe directory')
        if not directory and not stat.S_ISREG(info.st_mode):
            raise Conflict('Unsafe file')
        yield handle
    finally:
        _win.CloseHandle(handle)


@contextlib.contextmanager
def _directories(*paths):
    with contextlib.ExitStack() as stack:
        seen = set()
        for path in paths:
            for parent in [*reversed(Path(path).parents), Path(path)]:
                key = os.path.normcase(str(parent))
                if key not in seen:
                    stack.enter_context(_handle(parent, directory=True))
                    seen.add(key)
        yield


def _rename(handle, destination):
    name = _native(destination)
    class Rename(ctypes.Structure):
        _fields_ = [('ReplaceIfExists', wintypes.BOOL), ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD), ('FileName', ctypes.c_wchar * (len(name) + 1))]
    value = Rename(False, None, len(name.encode('utf-16-le')), name)
    if not _win.SetFileInformationByHandle(handle, 3, ctypes.byref(value), ctypes.sizeof(value)):
        raise ctypes.WinError(ctypes.get_last_error())


def _unlink(handle):
    value = wintypes.BOOL(True)
    if not _win.SetFileInformationByHandle(handle, 4, ctypes.byref(value), ctypes.sizeof(value)):
        raise ctypes.WinError(ctypes.get_last_error())


class TrashManager:
    def __init__(self, db_path, *, resolve, busy, changed, windows=None, activity_lock=None):
        self.resolve, self.busy, self.changed = resolve, busy, changed
        self.windows = os.name == 'nt' if windows is None else bool(windows)
        self._activity_lock = activity_lock or threading.RLock()
        self._active = set()
        self._lock = threading.RLock()
        self._queue = queue.Queue(maxsize=8)
        self._closed = False
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS trash_items(item_id TEXT PRIMARY KEY, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trash_jobs(job_id TEXT PRIMARY KEY,profile TEXT,operation_id TEXT UNIQUE,request TEXT,job TEXT);
            CREATE TABLE IF NOT EXISTS trash_previews(token TEXT PRIMARY KEY,profile TEXT,action TEXT,expires REAL,data TEXT);
            CREATE TABLE IF NOT EXISTS trash_confirmed(token TEXT PRIMARY KEY,operation_id TEXT);
            CREATE TABLE IF NOT EXISTS trash_roots(root_id TEXT PRIMARY KEY,root TEXT);''')
        with self._db:
            self._db.execute("UPDATE trash_jobs SET job=json_set(job,'$.state','complete') WHERE json_extract(job,'$.state') != 'complete'")
        self._queue.put(None)  # Reconcile intents before accepting newly queued work.
        self._thread = threading.Thread(target=self._worker, name='library-trash', daemon=True)
        self._thread.start()

    def _save(self, row):
        with self._lock, self._db:
            self._db.execute('INSERT OR REPLACE INTO trash_items VALUES(?,?)', (row['item_id'], json.dumps(row)))

    def _rows(self):
        with self._lock:
            return [json.loads(row[0]) for row in self._db.execute('SELECT record FROM trash_items')]

    def _item(self, item_id):
        _uuid(item_id)
        with self._lock:
            result = self._db.execute('SELECT record FROM trash_items WHERE item_id=?', (item_id,)).fetchone()
        if not result:
            raise Conflict('Trash record unavailable')
        return json.loads(result[0])

    def _authorized(self, profile, row):
        rel = _relative(row['rel'], empty=True)
        root = _root(self.resolve(profile, row['root_id'], rel))
        if root_id(root) != row['root_id'] or ('root' in row and os.path.normcase(str(root)) != os.path.normcase(row['root'])):
            raise PermissionError()
        return root

    def _public(self, profile, result):
        try:
            if 'root_id' in result:
                self._authorized(profile, result)
            elif 'confirmation_token' in result:
                self._confirmation(profile, result['confirmation_token'], None)
        except Exception:
            return {'status': 'error', 'error': 'Access unavailable', **({'request_index': result['request_index']} if 'request_index' in result else {})}
        return {key: value for key, value in result.items() if key not in ('root', 'fingerprint', 'profile')}

    def submit(self, profile_id, action, items, operation_id):
        _uuid(operation_id)
        if action not in ('preview_move', 'move', 'preview_restore', 'restore', 'preview_purge', 'purge', 'recover'):
            raise ValueError('Invalid action')
        if not isinstance(items, list) or not 1 <= len(items) <= 100 or any(not isinstance(x, dict) for x in items):
            raise ValueError('Select between 1 and 100 files')
        profile_id = str(profile_id)
        request = json.dumps({'action': action, 'items': items}, sort_keys=True, allow_nan=False)
        with self._lock:
            previous = self._db.execute('SELECT profile,request,job FROM trash_jobs WHERE operation_id=?', (operation_id,)).fetchone()
            if previous:
                if previous['profile'] != profile_id or previous['request'] != request:
                    raise ValueError('Operation identifier already used')
                return self._job_public(profile_id, json.loads(previous['job']))
            if self._closed or self._queue.full():
                raise ValueError('Trash worker is busy')
            job = dict(job_id=str(uuid.uuid4()), operation_id=operation_id, action=action, state='queued', results=[], total=len(items), completed=0)
            with self._db:
                self._db.execute('INSERT INTO trash_jobs VALUES(?,?,?,?,?)', (job['job_id'], profile_id, operation_id, request, json.dumps(job)))
            self._queue.put_nowait((profile_id, job, items))
        return job.copy()

    def _job_public(self, profile, job):
        return job | {'results': [self._public(profile, row) for row in job['results']]}

    def status(self, profile_id, job_id=None):
        with self._lock:
            rows = self._db.execute('SELECT job FROM trash_jobs WHERE profile=?' + (' AND job_id=?' if job_id else ' ORDER BY rowid DESC LIMIT 20'), (str(profile_id), job_id) if job_id else (str(profile_id),)).fetchall()
        jobs = [self._job_public(str(profile_id), json.loads(row[0])) for row in rows]
        return (jobs[0] if jobs else None) if job_id else {'jobs': jobs}

    def listing(self, profile_id, roots, excluded=None, **filters):
        allowed = {root_id(root) for root in roots}
        query = str(filters.get('query', '')).casefold()
        scope = filters.get('scope_root')
        scope_relative = _relative(filters.get('scope_relative', ''), empty=True).casefold()
        recursive = bool(filters.get('recursive', True))
        rows = []
        for row in self._rows():
            if row['state'] in TERMINAL or row['root_id'] not in allowed or (scope and row['root_id'] != scope):
                continue
            parent = row['rel'].rpartition('/')[0].casefold()
            if (not recursive and parent != scope_relative) or (recursive and scope_relative and parent != scope_relative and not parent.startswith(scope_relative + '/')):
                continue
            try:
                self._authorized(str(profile_id), row)
                for value in (excluded or {}).get(row['root_id'], ()):
                    value = str(value).replace('\\', '/').casefold().rstrip('/')
                    relative = row['rel'].casefold()
                    absolute = (row['root'].replace('\\', '/') + '/' + row['rel']).casefold()
                    if not value or relative == value or relative.startswith(value + '/') or absolute == value or absolute.startswith(value + '/'):
                        raise PermissionError()
            except Exception:
                continue
            if query not in Path(row['rel']).name.casefold():
                continue
            rows.append(self._public(str(profile_id), row) | {'name': Path(row['rel']).name, 'parent': row['rel'].rpartition('/')[0]})
        key = {'name': lambda r: Path(r['rel']).name.casefold(), 'location': lambda r: (r['root_id'], r['rel'].casefold()), 'size': lambda r: r['size'], 'duration': lambda r: r.get('duration', 0), 'state': lambda r: r['state'], 'date': lambda r: r['trashed_at'], 'mtime': lambda r: r['mtime'], 'trashed_at': lambda r: r['trashed_at']}.get(filters.get('sort'), lambda r: Path(r['rel']).name.casefold())
        rows.sort(key=lambda r: (r['root_id'], r['rel'], r['item_id']))
        rows.sort(key=key, reverse=bool(filters.get('descending', False)))
        if filters.get('sort') == 'duration':
            rows.sort(key=lambda r: r.get('duration') is None)
        total = len(rows)
        page_size = min(100, max(1, int(filters.get('page_size', 100))))
        pages = (total + page_size - 1) // page_size
        page = min(max(1, int(filters.get('page', 1))), max(1, pages))
        return dict(items=rows[(page-1)*page_size:page*page_size], total=total, page=page, pages=pages, page_size=page_size)

    def is_busy(self, path):
        with self._activity_lock:
            return os.path.normcase(os.path.abspath(path)) in self._active

    @contextlib.contextmanager
    def _claim(self, path):
        key = os.path.normcase(os.path.abspath(path))
        with self._activity_lock:
            if key in self._active or self.busy(path):
                raise Conflict('File is busy')
            self._active.add(key)
        try:
            yield
        finally:
            with self._activity_lock:
                self._active.discard(key)

    @contextlib.contextmanager
    def _root_lock(self, root):
        name = 'Global\\LANStreamTrash_' + hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()
        handle = _win.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = _win.WaitForSingleObject(handle, 0) in (0, 0x80)
        try:
            if not acquired:
                raise Conflict('Library is busy')
            yield
        finally:
            if acquired:
                _win.ReleaseMutex(handle)
            _win.CloseHandle(handle)

    def _marker(self, root, create=False):
        trash = root / TRASH
        if not trash.exists():
            if not create:
                return None
            with _directories(root):
                trash.mkdir()
                self._write_json(trash / 'root.json', {'format': 1, 'root_id': root_id(root)}, initial=True)
        with _directories(trash):
            marker = self._read_json(trash / 'root.json')
            if marker != {'format': 1, 'root_id': root_id(root)}:
                raise Conflict('Unrecognized Trash directory')
        return trash

    @contextlib.contextmanager
    def _storage(self, root, create=False):
        with self._root_lock(root):
            trash = self._marker(root, create)
            if trash is None:
                yield None
            else:
                with _directories(trash), _handle(trash / 'lock', exclusive=True, create=True):
                    yield trash

    def _read_json(self, path):
        with _handle(path, metadata=True):
            with open(path, 'rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise Conflict('Invalid Trash metadata')
            return json.loads(raw)

    def _write_json(self, path, value, initial=False):
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        with open(temporary, 'x', encoding='utf-8') as stream:
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if initial:
                os.rename(temporary, path)  # Native Windows rename never overwrites.
            else:
                # Only replace a validated regular metadata file in our held directory.
                with _handle(path, metadata=True):
                    pass
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _manifest(self, row, initial=False):
        path = Path(row['root']) / TRASH / row['item_id'] / 'manifest.json'
        self._write_json(path, row | {'format': 1}, initial)

    def _validate_manifest(self, root, item_id):
        _uuid(item_id)
        with _directories(root / TRASH / item_id):
            row = self._read_json(root / TRASH / item_id / 'manifest.json')
        if row.get('format') != 1 or row.get('item_id') != item_id or row.get('root_id') != root_id(root) or os.path.normcase(row.get('root', '')) != os.path.normcase(str(root)):
            raise Conflict('Invalid Trash metadata')
        _relative(row['rel'])
        _uuid(row['operation_id'])
        if not isinstance(row.get('fingerprint'), dict) or any(field not in row['fingerprint'] for field in FIELDS):
            raise Conflict('Invalid Trash metadata')
        live = row['fingerprint']
        if any(type(live[field]) is not int or live[field] < 0 for field in ('size', 'mtime_ns', 'ctime_ns', 'ino', 'dev')) or type(live['mtime']) not in (int, float) or not math.isfinite(live['mtime']):
            raise Conflict('Invalid Trash metadata')
        if type(row.get('size')) is not int or row['size'] != live['size'] or row.get('mtime') != live['mtime'] or type(row.get('trashed_at')) not in (int, float) or not math.isfinite(row['trashed_at']) or not isinstance(row.get('profile'), str):
            raise Conflict('Invalid Trash metadata')
        if 'duration' in row and (type(row['duration']) not in (int, float) or not math.isfinite(row['duration']) or row['duration'] < 0):
            raise Conflict('Invalid Trash metadata')
        if row.get('state') not in ('move_intent', 'trashed', 'restore_intent', 'purge_intent', 'restored', 'purged', 'attention', 'move_failed'):
            raise Conflict('Invalid Trash metadata')
        return row

    def _confirmation(self, profile, token, action):
        _uuid(token)
        with self._lock:
            row = self._db.execute('SELECT * FROM trash_previews WHERE token=? AND profile=?', (token, profile)).fetchone()
        if not row or (action is not None and row['action'] != action) or row['expires'] < time.time():
            raise Conflict('Confirmation expired or invalid')
        data = json.loads(row['data'])
        self._authorized(profile, data)
        return data

    def _checkpoint(self, point):
        """Named fault boundaries for synthetic crash/reconciliation tests."""

    def _preview(self, profile, action, item):
        if action == 'move':
            root = self._authorized(profile, item)
            rel = _relative(item['rel'])
            row = dict(root_id=root_id(root), root=str(root), rel=rel, profile=profile)
            source = root / rel
        else:
            row = self._item(item['item_id'])
            root = self._authorized(profile, row)
            if row['state'] != 'trashed':
                raise Conflict('Trash item needs attention')
            source = root / TRASH / row['item_id'] / 'payload'
        with self._claim(root / row['rel']), self._root_lock(root):
            if action == 'move':
                source = safe_regular_path(root, row['rel'])
            else:
                with self._storage(root):
                    manifest = self._validate_manifest(root, row['item_id'])
                    if manifest['fingerprint'] != row['fingerprint']:
                        raise Conflict('Trash item needs attention')
            with _directories(source.parent), _handle(source):
                live = fingerprint(source)
                if action == 'move':
                    if live['size'] != item['size'] or live['mtime'] != item['mtime']:
                        raise Conflict('Selected file changed')
                    row.update(size=live['size'], mtime=live['mtime'], fingerprint=live)
                    duration = item.get('duration')
                    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
                        row['duration'] = duration
                elif live != row['fingerprint']:
                    raise Conflict('Trash payload changed')
                if action == 'restore':
                    with _directories(root / row['rel'].rpartition('/')[0]):
                        if os.path.lexists(root / row['rel']):
                            raise FileExistsError()
            token = str(uuid.uuid4())
            with self._lock, self._db:
                self._db.execute('INSERT INTO trash_previews VALUES(?,?,?,?,?)', (token, profile, action, time.time() + 600, json.dumps(row)))
            return row | {'status': 'ready', 'name': Path(row['rel']).name, 'confirmation_token': token}

    def _execute(self, profile, action, item, operation):
        if not self.windows or os.name != 'nt':
            raise Conflict('Trash mutations require Windows')
        if action.startswith('preview_'):
            return self._preview(profile, action[8:], item)
        if action == 'recover':
            root = self._authorized(profile, item | {'rel': ''})
            with self._storage(root) as trash:
                count, attention = self._recover_root(profile, root, trash)
            return dict(status='recovered', root_id=root_id(root), rel='', count=count, needs_attention=attention,
                        **({'error': 'Some Trash records need attention'} if attention else {}))
        row = self._confirmation(profile, item['confirmation_token'], action)
        with self._lock, self._db:
            try:
                self._db.execute('INSERT INTO trash_confirmed VALUES(?,?)', (item['confirmation_token'], operation))
            except sqlite3.IntegrityError:
                raise Conflict('Confirmation already used') from None
        root = self._authorized(profile, row)
        with self._claim(root / row['rel']), self._storage(root, create=action == 'move') as trash:
            if trash is None:
                raise Conflict('Trash storage unavailable')
            with self._lock:
                initialized = self._db.execute('SELECT 1 FROM trash_roots WHERE root_id=?', (root_id(root),)).fetchone()
            if not initialized:
                _, attention = self._recover_root(profile, root, trash)
                if attention and action == 'move':
                    raise Conflict('Trash recovery needs attention')
            if action == 'move':
                return self._move(row, root, operation)
            current = self._item(row['item_id'])
            if current['state'] != 'trashed' or current['fingerprint'] != row['fingerprint']:
                raise Conflict('Trash item changed')
            manifest = self._validate_manifest(root, row['item_id'])
            if manifest['fingerprint'] != row['fingerprint'] or manifest['state'] != 'trashed':
                raise Conflict('Trash item needs attention')
            return self._finish(row | {'profile': profile}, root, action)

    def _move(self, row, root, operation):
        source = safe_regular_path(root, row['rel'])
        item_id = str(uuid.uuid4())
        folder = root / TRASH / item_id
        row = row | dict(item_id=item_id, operation_id=operation, trashed_at=time.time(), state='move_intent', error='')
        with contextlib.ExitStack() as stack:
            stack.enter_context(_directories(source.parent))
            handle = stack.enter_context(_handle(source))
            if fingerprint(source) != row['fingerprint']:
                raise Conflict('Selected file changed')
            folder.mkdir()
            stack.enter_context(_directories(folder))
            self._save(row)
            self._checkpoint('move_journal')
            self._manifest(row, initial=True)
            self._checkpoint('move_manifest')
            self.changed(root, row['rel'], True, fingerprint=row['fingerprint'], pending=True)
            self._checkpoint('move_hidden')
            try:
                _rename(handle, folder / 'payload')
            except Exception:
                # A native failed rename is definite only while our original handle/path agree.
                if os.path.lexists(source) and fingerprint(source) == row['fingerprint'] and not os.path.lexists(folder / 'payload'):
                    row.update(state='move_failed', error='Move did not complete')
                    self._manifest(row)
                    self._save(row)
                    self.changed(root, row['rel'], False, fingerprint=row['fingerprint'])
                raise
            self._checkpoint('move_renamed')
            if fingerprint(folder / 'payload') != row['fingerprint']:
                raise Conflict('Moved payload needs attention')
            row['state'] = 'trashed'
            self._manifest(row)
            self._checkpoint('move_completed_manifest')
            self._save(row)
            self._checkpoint('move_completed_journal')
            self.changed(root, row['rel'], True, fingerprint=row['fingerprint'], pending=False)
            return row | {'status': 'moved'}

    def _finish(self, row, root, action):
        payload = root / TRASH / row['item_id'] / 'payload'
        destination = root / row['rel']
        parents = (payload.parent, destination.parent) if action == 'restore' else (payload.parent,)
        with _directories(*parents), _handle(payload) as handle:
            if fingerprint(payload) != row['fingerprint']:
                raise Conflict('Trash payload changed')
            if action == 'restore' and os.path.lexists(destination):
                raise FileExistsError()
            row = row | {'state': action + '_intent', 'error': ''}
            self._save(row)
            self._checkpoint(action + '_journal')
            self._manifest(row)
            self._checkpoint(action + '_manifest')
            if action == 'restore':
                _rename(handle, destination)
            else:
                _unlink(handle)
            self._checkpoint(action + '_applied')
        # Purge takes effect when the exclusive payload handle closes.
        if action == 'restore' and fingerprint(destination) != row['fingerprint']:
            raise Conflict('Restored file needs attention')
        row['state'] = 'restored' if action == 'restore' else 'purged'
        self._manifest(row)
        self._checkpoint(action + '_completed_manifest')
        self._save(row)
        self._checkpoint(action + '_completed_journal')
        self._clear_terminal(root, row, restored=action == 'restore')
        return row | {'status': row['state']}

    def _clear_terminal(self, root, row, *, restored=False):
        physical = os.path.normcase(os.path.abspath(root / row['rel']))
        with self._lock:
            newer = self._db.execute("SELECT record FROM trash_items WHERE json_extract(record,'$.trashed_at')>? "
                                     "AND json_extract(record,'$.state') IN ('move_intent','trashed','restore_intent','purge_intent')",
                                     (row['trashed_at'],)).fetchall()
        for candidate in newer:
            other = json.loads(candidate[0])
            if os.path.normcase(os.path.abspath(Path(other['root']) / other['rel'])) == physical:
                return
        self.changed(root, row['rel'], False, fingerprint=row['fingerprint'],
                     **({'restored_duration': row.get('duration')} if restored else {}))

    def _reconcile(self, row, root):
        folder = root / TRASH / row['item_id']
        manifest = self._validate_manifest(root, row['item_id'])
        if manifest['fingerprint'] != row['fingerprint'] or manifest['rel'] != row['rel']:
            raise Conflict('Trash metadata conflict')
        source = root / row['rel']
        payload = folder / 'payload'
        with _directories(folder, source.parent):
            original = fingerprint(source) if os.path.lexists(source) else None
            stored = fingerprint(payload) if os.path.lexists(payload) else None
        state = row['state']
        expected = row['fingerprint']
        if stored is not None and original is not None:
            row.update(state='attention', error='Needs attention: both original and payload are present')
            self.changed(root, row['rel'], False, fingerprint=row['fingerprint'])
        elif state == 'move_intent' and stored == expected and original is None:
            row['state'] = 'trashed'
            self.changed(root, row['rel'], True, fingerprint=row['fingerprint'], pending=False)
        elif state == 'move_intent' and original == expected and stored is None:
            row['state'] = 'move_failed'
            self.changed(root, row['rel'], False, fingerprint=row['fingerprint'])
        elif state == 'restore_intent' and original == expected and stored is None:
            row['state'] = 'restored'
            self.changed(root, row['rel'], False, fingerprint=row['fingerprint'])
        elif state in ('restore_intent', 'purge_intent') and stored == expected and original is None:
            row['state'] = 'trashed'
        elif state in TERMINAL and stored is None:
            self._clear_terminal(root, row)
        elif state == 'trashed' and stored == expected and original is None:
            self.changed(root, row['rel'], True, fingerprint=row['fingerprint'], pending=False)
        elif state == 'move_failed' and stored is None:
            pass
        else:
            row.update(state='attention', error='Needs attention: filesystem outcome is uncertain')
            self.changed(root, row['rel'], False, fingerprint=row['fingerprint'])
        self._manifest(row)
        self._save(row)
        return row['state']

    def _recover_root(self, profile, root, trash):
        count, attention = 0, 0
        if trash:
            with os.scandir(trash) as entries:
                for entry in entries:
                    if entry.name in ('root.json', 'lock'):
                        continue
                    try:
                        _uuid(entry.name)
                        row = self._validate_manifest(root, entry.name)
                        try:
                            self._authorized(profile, row)
                        except PermissionError:
                            continue
                        with self._lock:
                            existing = self._db.execute('SELECT record FROM trash_items WHERE item_id=?', (row['item_id'],)).fetchone()
                        if existing:
                            known = json.loads(existing[0])
                            if any(known.get(key) != row.get(key) for key in ('root_id', 'root', 'rel', 'fingerprint')):
                                raise Conflict('Trash metadata conflict')
                        if self._reconcile(row, root) == 'attention':
                            attention += 1
                        count += 1
                    except Exception:
                        # Unknown material stays untouched; one bad item must not orphan valid ones.
                        attention += 1
        with self._lock, self._db:
            if attention:
                self._db.execute('DELETE FROM trash_roots WHERE root_id=?', (root_id(root),))
            else:
                self._db.execute('INSERT OR REPLACE INTO trash_roots VALUES(?,?)', (root_id(root), str(root)))
        return count, attention

    def _startup(self):
        for row in self._rows():
            if row['state'] in (*TERMINAL, 'trashed'):
                try:
                    root = self._authorized(row['profile'], row)
                    if row['state'] == 'trashed':
                        self.changed(root, row['rel'], True, fingerprint=row['fingerprint'], pending=False)
                    else:
                        self._clear_terminal(root, row)
                except Exception:
                    pass
                continue
            if row['state'] not in ('move_intent', 'restore_intent', 'purge_intent'):
                continue
            try:
                root = self._authorized(row['profile'], row)
                with self._storage(root):
                    self._reconcile(row, root)
            except Exception as exc:
                row.update(state='attention', error=_error(exc))
                self._save(row)

    def _job_save(self, job):
        with self._lock, self._db:
            self._db.execute('UPDATE trash_jobs SET job=? WHERE job_id=?', (json.dumps(job), job['job_id']))

    def _failure(self, profile, item, exc):
        result = {'status': 'error', 'error': _error(exc)}
        try:
            if 'confirmation_token' in item:
                row = self._confirmation(profile, item['confirmation_token'], None)
            elif 'item_id' in item:
                row = self._item(item['item_id'])
            else:
                row = item
            self._authorized(profile, row)
            result.update(root_id=row['root_id'], rel=row['rel'], name=Path(row['rel']).name)
            if 'item_id' in row:
                result['item_id'] = row['item_id']
        except Exception:
            pass
        return result

    def _worker(self):
        while True:
            try:
                work = self._queue.get(timeout=.1)
            except queue.Empty:
                if self._closed:
                    self._db.close()
                    return
                continue
            try:
                if work is None:
                    self._startup()
                    continue
                profile, job, items = work
                job['state'] = 'running'
                self._job_save(job)
                seen = set()
                for request_index, item in enumerate(items):
                    try:
                        result = self._execute(profile, job['action'], item, job['operation_id'])
                        live = result.get('fingerprint', {})
                        identity = (live.get('dev'), live.get('ino')) if live.get('ino') else (result.get('root_id'), result.get('rel'))
                        if job['action'].startswith('preview_') and identity in seen:
                            raise Conflict('Duplicate selected file')
                        seen.add(identity)
                    except Exception as exc:
                        result = self._failure(profile, item, exc)
                    result['request_index'] = request_index
                    job['results'].append(result)
                    job['completed'] += 1
                    self._job_save(job)
                job['state'] = 'complete'
                self._job_save(job)
            finally:
                self._queue.task_done()

    def wait_for_idle(self, timeout=5):
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(.01)
        return True

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._thread.join(2)
