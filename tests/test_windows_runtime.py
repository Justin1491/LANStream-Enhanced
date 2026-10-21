"""Launch the built Windows application; no media or encoding is involved."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request


@unittest.skipUnless(os.name == 'nt' and os.environ.get('LANSTREAM_RUNTIME_EXE'),
                     'Built Windows executable required')
class FrozenRuntimeTests(unittest.TestCase):
    def test_packaged_app_loads_runtime_and_serves_its_binary_identity(self):
        executable = Path(os.environ['LANSTREAM_RUNTIME_EXE']).resolve()
        self.assertTrue(any(path.is_file() for path in (executable.parent / '_internal').glob('python3[0-9]*.dll')),
                        'Package is missing its versioned Python runtime DLL')
        self.assertFalse((executable.parent / 'ffmpeg.exe').exists())
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix='lanstream-frozen-check-') as temporary:
            work = Path(temporary)
            config = work / 'config.txt'
            config.write_text('automatic_indexing=false\ntranscoder=qsv\n', encoding='utf-8')
            environment = dict(os.environ, LANSTREAM_CONFIG_FILE=str(config),
                               LANSTREAM_CACHE_DIR=str(work / 'cache'), LANSTREAM_PORT=str(port),
                               LANSTREAM_HOST='127.0.0.1', LANSTREAM_TRANSCODER='qsv',
                               LANSTREAM_USE_MASTER_PASSWORD='false', LANSTREAM_PASSWORD='',
                               PATH=str(work))  # No media executables on PATH or in the package.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with (work / 'runtime.log').open('wb') as log:
                process = subprocess.Popen([str(executable)], cwd=work, env=environment,
                                           stdout=log, stderr=log)
                try:
                    payload = None
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            break
                        try:
                            with opener.open(f'http://127.0.0.1:{port}/api/transcoder-status', timeout=1) as response:
                                payload = json.load(response)
                            break
                        except (OSError, urllib.error.URLError):
                            time.sleep(.1)
                    self.assertIsNotNone(payload, (work / 'runtime.log').read_text(errors='replace'))
                    self.assertEqual(payload['preference'], 'qsv')
                    self.assertEqual([test['key'] for test in payload['tests']], ['qsv'])
                    self.assertFalse(payload['transcoder_ok'], 'No encoder may be available in this runtime check')
                    self.assertEqual(payload['server']['kind'], 'executable')
                    self.assertEqual(payload['server']['build'], hashlib.sha256(executable.read_bytes()).hexdigest())
                finally:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=10)
