"""Media children can launch from the console-free Windows application."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hls_streams import HLSStreams


class MediaProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        cls.root = Path(cls.temp.name)
        config = cls.root / 'config.txt'
        config.write_text('port=8061\n', encoding='utf-8')
        cls.imported_app = 'app' not in sys.modules
        with patch.dict(os.environ, {'LANSTREAM_CONFIG_FILE': str(config),
                                    'LANSTREAM_CACHE_DIR': str(cls.root / 'cache')}):
            import app
        cls.server = app

    @classmethod
    def tearDownClass(cls):
        if cls.imported_app:
            cls.server.cleanup_service.close()
            cls.server.directory_index.close()
            cls.server.image_scheduler.close()
        cls.temp.cleanup()

    def test_probe_archive_and_encoder_launch_options(self):
        source = self.root / 'fictional.cbr'
        for platform in ('nt', 'posix'):
            with self.subTest(platform=platform), \
                 patch.object(self.server, 'os', SimpleNamespace(name=platform, path=os.path)), \
                 patch.object(self.server, 'SEVEN_ZIP', '7z'), \
                 patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                 patch.object(subprocess, 'run') as run:
                run.return_value = subprocess.CompletedProcess([], 0, '{}', '')
                self.assertEqual(self.server.probe(source), {})
                run.return_value = subprocess.CompletedProcess([], 0, 'Path = page1.jpg\n', '')
                self.assertEqual(self.server._seven_zip_names(source), ['page1.jpg'])
                run.return_value = subprocess.CompletedProcess([], 0, b'page data', b'')
                self.assertEqual(self.server._seven_zip_read(source, 'page1.jpg'), b'page data')
                run.return_value = subprocess.CompletedProcess([], 0, '', '')
                self.assertEqual(self.server.run_encoder_test('h264_qsv'), (True, ''))
                self.assertEqual(run.call_count, 4)
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs['stdin'], subprocess.DEVNULL)
                    self.assertEqual(call.kwargs['creationflags'], 0x08000000 if platform == 'nt' else 0)
                    self.assertTrue(call.kwargs['capture_output'])
                self.assertEqual([call.kwargs['timeout'] for call in run.call_args_list], [30, 30, 60, 20])

    def test_hls_launches_with_real_redirected_handles(self):
        tracker = Mock()
        real_popen = subprocess.Popen
        manager = HLSStreams(self.root / 'hls', tracker)
        try:
            with patch('hls_streams.subprocess.Popen', wraps=real_popen) as popen:
                stream = manager.get(
                    'a' * 32 + '-1', ('fixture',),
                    lambda directory: [sys.executable, '-c', 'import time; time.sleep(60)'],
                    source=self.root / 'fictional.mp4', backend='software', start=0,
                )
            self.assertIsNone(stream.process.poll())
            self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(popen.call_args.kwargs['creationflags'],
                             subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self.assertEqual(popen.call_args.kwargs['stdout'], subprocess.DEVNULL)
            self.assertFalse(popen.call_args.kwargs['stderr'].closed)
        finally:
            manager.close()


if __name__ == '__main__':
    unittest.main()
