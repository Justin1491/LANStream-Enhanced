"""Library Cleanup routes; share operations are submitted to bounded workers."""
import atexit
import os
import threading
import uuid
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from cleanup_jobs import DurationJobs, fingerprint, valid_duration
from directory_index import FILE_TYPE_KINDS, _relative, root_id
from library_trash import TrashManager


def reserved(path):
    return '.lanstream-trash' in str(path).replace('\\', '/').casefold().split('/')


class CleanupService:
    def __init__(self, server, cache_dir=None):
        self.server = server
        self.activity_lock = server.CLEANUP_ACTIVITY_LOCK
        self.signer = URLSafeTimedSerializer(server.app.secret_key, salt='cleanup-selection-v1')
        self.trash = TrashManager(Path(cache_dir or server.CACHE_DIR) / 'trash.sqlite3',
                                  resolve=self.resolve, busy=self.busy, changed=self.changed,
                                  activity_lock=self.activity_lock)
        self.durations = DurationJobs(lambda: server.directory_index, resolve=self.resolve,
                                      probe=lambda path: server.probe(path),
                                      streaming=lambda: bool(server.stream_tracker.snapshot()),
                                      busy=self.trash.is_busy)

    def resolve(self, profile_id, rid, relative):
        relative = _relative(relative)
        for profile in self.server.load_profiles():
            if profile['id'] != profile_id:
                continue
            for root in profile['roots']:
                if root_id(root) != rid or reserved(root) or reserved(relative):
                    continue
                target = os.path.normcase(os.path.abspath(root / relative))
                base = os.path.normcase(os.path.abspath(root))
                if os.path.commonpath((base, target)) != base:
                    raise PermissionError('File not available.')
                for excluded in profile['excluded']:
                    blocked = os.path.normcase(os.path.abspath(excluded))
                    try:
                        if os.path.commonpath((blocked, target)) == blocked:
                            raise PermissionError('File not available.')
                    except ValueError:
                        continue
                return root
        raise PermissionError('File not available.')

    def busy(self, path):
        key = os.path.normcase(os.path.abspath(path))
        with self.activity_lock:
            if key in self.server.ACTIVE_PROBES:
                return True
            if any(os.path.normcase(os.path.abspath(row['path'])) == key
                   for row in self.server.stream_tracker.snapshot()):
                return True
            scheduler = self.server.image_scheduler
            with scheduler._condition:
                if any(os.path.normcase(os.path.abspath(job['source'])) == key
                       for job in scheduler._active.values()):
                    return True
            return hasattr(self, 'durations') and self.durations.is_busy(path)

    def changed(self, root, relative, hidden, **version):
        restored_duration = version.pop('restored_duration', None)
        self.server.directory_index.hide_file(root, relative, hidden, **version)
        if not hidden and restored_duration is not None:
            self.server.directory_index.save_duration(root, relative, version['fingerprint'],
                                                      restored_duration, require_entry=False)

    def writes_enabled(self):
        return (os.name == 'nt' and self.server.authentication_required()
                and self.server.bool_setting(self.server.SETTINGS.get('library_cleanup_enabled', 'false')))

    def selection(self, profile, item):
        return self.signer.dumps(dict(profile=profile['id'], **{
            key: item[key] for key in ('root_id', 'rel', 'size', 'mtime', 'version', 'duration')}))

    def selected(self, profile, token):
        try:
            item = self.signer.loads(token, max_age=600)
            if item.pop('profile') != profile['id']:
                raise ValueError
            self.resolve(profile['id'], item['root_id'], item['rel'])
            return item
        except (BadSignature, KeyError, TypeError, ValueError, PermissionError):
            abort(400, description='Selection expired or changed. Reload the list and select again.')

    def save_probe(self, root, relative, before, info):
        try:
            if fingerprint(root / relative) == before:
                duration = valid_duration(info)
                if duration is not None:
                    self.server.directory_index.save_duration(root, relative, before, duration)
        except (OSError, ValueError):
            pass

    def close(self):
        self.durations.close()
        self.trash.close()


def register_cleanup(server):
    bp = Blueprint('cleanup', __name__)
    server.cleanup_service = CleanupService(server)
    atexit.register(server.cleanup_service.close)

    def service():
        return server.cleanup_service

    def context():
        profile, roots, excluded, indexes = server.explorer_roots()
        if not profile:
            abort(404)
        return profile, roots, excluded, indexes

    def scope_args():
        profile, roots, excluded, _ = context()
        scope = request.args.get('scope', 'all')
        if scope not in {'all', 'library', 'folder', 'subtree'}:
            abort(400)
        result = dict(excluded=excluded)
        if scope != 'all':
            value = request.args.get('root', '')
            try:
                root = next((root for root in roots if root_id(root) == value), None)
                if root is None:
                    index = int(value)
                    root = profile['roots'][index] if index >= 0 else None
                if root not in roots:
                    abort(404)
                relative = _relative(request.args.get('relative', '')) if scope != 'library' else ''
                service().resolve(profile['id'], root_id(root), relative)
            except (ValueError, IndexError, PermissionError):
                abort(400)
            result.update(scope_root=root, scope_relative=relative, recursive=scope != 'folder')
        return profile, roots, result

    def query_args():
        try:
            page = int(request.args.get('page', '1'))
        except ValueError:
            abort(400)
        sort = request.args.get('sort', 'name')
        category = request.args.get('file_type', 'all')
        if sort not in {'name', 'location', 'size', 'duration', 'date', 'mtime', 'type', 'trashed_at', 'state'} or category not in FILE_TYPE_KINDS:
            abort(400)
        if request.args.get('direction', 'asc') not in {'asc', 'desc'}:
            abort(400)
        return dict(query=request.args.get('q', '')[:500], page=max(1, min(1000000, page)),
                    page_size=100, sort=sort, descending=request.args.get('direction') == 'desc', file_type=category)

    @bp.get('/cleanup')
    def page():
        profile, roots, _, indexes = context()
        root_options = [dict(id=root_id(root), index=indexes[root_id(root)],
                             label=server.root_label(root, indexes[root_id(root)])) for root in roots]
        return render_template('cleanup.html', roots=root_options, writes_enabled=service().writes_enabled())

    @bp.get('/api/cleanup/files')
    def files():
        profile, roots, scope = scope_args()
        options = query_args()
        if options['sort'] in {'trashed_at', 'state'}:
            abort(400)
        group = {}
        if 'group_by' in request.args or 'group_key' in request.args:
            group = group_args(members=True)
        result = server.directory_index.cleanup(roots, **scope, **options, **group,
                                                matching_sizes=not group and request.args.get('matching_sizes') == '1')
        for item in result['items']:
            item['selection_token'] = service().selection(profile, item)
        result = server.explorer_items(result)
        result['writes_enabled'] = service().writes_enabled()
        return jsonify(result)

    def group_args(members=False):
        group_by = request.args.get('group_by', 'size')
        if group_by not in {'size', 'duration'}:
            abort(400)
        result = dict(group_by=group_by)
        if members:
            value = request.args.get('group_key', '')
            if ('group_by' not in request.args or not value.isascii() or not value.isdecimal()
                    or len(value) > 19 or int(value) > 9223372036854775807):
                abort(400)
            result['group_key'] = value
        elif 'group_key' in request.args:
            abort(400)
        return result

    @bp.get('/api/cleanup/groups')
    def groups():
        _, roots, scope = scope_args()
        options = query_args()
        result = server.directory_index.cleanup_groups(roots, **scope, **group_args(),
                     query=options['query'], file_type=options['file_type'], page=options['page'], page_size=20)
        result['writes_enabled'] = service().writes_enabled()
        return jsonify(result)

    @bp.get('/api/cleanup/folders')
    def folders():
        profile, roots, scope = scope_args()
        root = scope.get('scope_root')
        if root is None:
            abort(400)
        return jsonify(server.directory_index.cleanup_folders(root, scope.get('scope_relative', ''),
                       excluded=scope['excluded'].get(root_id(root), ()), page=query_args()['page']))

    @bp.get('/api/cleanup/trash')
    def trash():
        profile, roots, scope = scope_args()
        options = query_args()
        result = service().trash.listing(profile['id'], roots, scope['excluded'], **options,
                                         scope_root=root_id(scope['scope_root']) if scope.get('scope_root') else None,
                                         scope_relative=scope.get('scope_relative', ''),
                                         recursive=scope.get('recursive', True))
        _, _, _, indexes = context()
        for item in result['items']:
            index = indexes.get(item['root_id'])
            if index is not None:
                item['name'] = Path(item['rel']).name
                item['location'] = '/'.join(filter(None, (server.root_label(profile['roots'][index], index), item['rel'].rpartition('/')[0])))
        result['writes_enabled'] = service().writes_enabled()
        return jsonify(result)

    @bp.post('/api/cleanup/actions')
    def actions():
        server.explorer_write_check()
        profile, _, _, _ = context()
        if not service().writes_enabled():
            abort(403, description='File changes require enabled Library Cleanup and password sign-in on Windows.')
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        action, items = data.get('action'), data.get('items')
        if action not in {'preview_move', 'move', 'preview_restore', 'restore', 'preview_purge', 'purge', 'recover'}:
            abort(400)
        if not isinstance(items, list) or not 1 <= len(items) <= 100 or not all(isinstance(item, dict) for item in items):
            abort(400)
        try:
            operation = str(uuid.UUID(data['operation_id']))
        except (KeyError, TypeError, ValueError, AttributeError):
            abort(400)
        if action == 'preview_move':
            items = [service().selected(profile, item.get('selection_token')) for item in items]
        elif action in {'move', 'restore', 'purge'}:
            if any(set(item) != {'confirmation_token'} or not isinstance(item['confirmation_token'], str) for item in items):
                abort(400)
        elif action == 'recover':
            if len(items) != 1 or set(items[0]) != {'root_id', 'rel'} or items[0]['rel'] != '':
                abort(400)
            try:
                service().resolve(profile['id'], items[0]['root_id'], '')
            except (PermissionError, ValueError):
                abort(404)
        else:
            if any(set(item) != {'item_id'} or not isinstance(item['item_id'], str) for item in items):
                abort(400)
        try:
            return jsonify(service().trash.submit(profile['id'], action, items, operation)), 202
        except (ValueError, PermissionError) as exc:
            abort(400, description=str(exc))

    @bp.get('/api/cleanup/jobs/<job_id>')
    def job(job_id):
        profile, _, _, _ = context()
        value = service().trash.status(profile['id'], job_id)
        if value is None:
            abort(404)
        return jsonify(value)

    @bp.route('/api/cleanup/durations', methods=['GET', 'POST'])
    def durations():
        profile, roots, scope = scope_args()
        if request.method == 'GET':
            return jsonify(job=service().durations.status(profile['id']))
        server.explorer_write_check()
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or data.get('action') not in {'start', 'retry', 'pause', 'resume', 'stop'}:
            abort(400)
        try:
            if data['action'] in {'start', 'retry'}:
                job = service().durations.start(profile['id'], roots, **scope, retry_failed=data['action'] == 'retry')
            else:
                job = service().durations.control(profile['id'], data['action'])
            return jsonify(job=job)
        except PermissionError:
            abort(404)
        except ValueError as exc:
            abort(409, description=str(exc))

    @bp.errorhandler(Exception)
    def error(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        server.app.logger.exception('Library Cleanup request failed')
        return jsonify(error='Library Cleanup is unavailable. Check access and retry.'), 503

    server.app.register_blueprint(bp)
