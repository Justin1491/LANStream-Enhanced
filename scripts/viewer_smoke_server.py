"""Serve fictional non-video fixtures on loopback for browser verification."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from viewer_fixtures import create_viewer_fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-parent', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8061)
    parser.add_argument('--executable', type=Path, help='Test the portable executable instead of source')
    args = parser.parse_args()
    args.work_parent = args.work_parent.resolve()
    args.work_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='viewers-', dir=args.work_parent) as directory:
        work = Path(directory)
        root = work / 'Fictional Viewers'
        files = create_viewer_fixtures(root)
        config = work / 'config.txt'
        config.write_text(f'port={args.port}\ntranscoder=software\n<viewer-test>\n{root}\n-/Private\n<full-test>\n{root}\n', encoding='utf-8')
        os.environ['LANSTREAM_CONFIG_FILE'] = str(config)
        os.environ['LANSTREAM_CACHE_DIR'] = str(work / 'cache')
        manifest = dict(files=files, work=str(work), root=str(root), url=f'http://127.0.0.1:{args.port}')
        (args.work_parent / 'viewer-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        if args.executable:
            executable = args.executable.resolve(strict=True)
            environment = dict(os.environ, LANSTREAM_HOST='127.0.0.1', LANSTREAM_PORT=str(args.port), LANSTREAM_TRANSCODER='software')
            process = subprocess.Popen([str(executable)], cwd=executable.parent, env=environment,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            return
        import app
        from waitress import serve
        print('VIEWER FIXTURE READY ' + manifest['url'], flush=True)
        try:
            serve(app.app, host='127.0.0.1', port=args.port, threads=8)
        finally:
            app.image_scheduler.wait_for_idle(10)
            app.image_scheduler.close()
            app.directory_index.close()


if __name__ == '__main__':
    main()
