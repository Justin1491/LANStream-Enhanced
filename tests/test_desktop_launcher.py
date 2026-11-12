"""Desktop lifecycle checks use real Tk, with only the tray boundary replaced."""
import io
import gc
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import desktop_launcher as desktop


class OutputTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows windowless interpreter')
    def test_windowless_output_reaches_inherited_installer_pipes(self):
        interpreter = Path(sys.executable).with_name('pythonw.exe')
        if not interpreter.exists():
            self.skipTest('pythonw.exe unavailable')
        # PyInstaller's windowless bootloader clears these even with pipes;
        # recent pythonw versions can initialize them from inherited pipes.
        code = ('import sys; import desktop_launcher as d; '
                'sys.stdout=None; sys.stderr=None; o=d.RecentOutput(); '
                'sys.stdout=d.CapturedStream(o,d.inherited_stream("stdout")); '
                'sys.stderr=d.CapturedStream(o,d.inherited_stream("stderr")); '
                'print("startup redirected"); print("error redirected",file=sys.stderr)')
        with tempfile.TemporaryDirectory() as directory:
            with open(Path(directory) / 'out.txt', 'w+b') as out, \
                 open(Path(directory) / 'err.txt', 'w+b') as err:
                process = subprocess.run([str(interpreter), '-c', code], stdout=out, stderr=err,
                                         cwd=Path(__file__).resolve().parents[1], timeout=15)
                self.assertEqual(process.returncode, 0)
                out.seek(0)
                err.seek(0)
                self.assertIn(b'startup redirected', out.read())
                self.assertIn(b'error redirected', err.read())

    def test_output_keeps_only_one_mib_of_recent_utf8(self):
        output = desktop.RecentOutput()
        output.write('old\n' + '\u00e9' * desktop.LOG_LIMIT)
        revision, text = output.snapshot()
        self.assertLessEqual(len(text.encode('utf-8')), 1024 * 1024)
        self.assertTrue(text.endswith('\u00e9' * 100))
        self.assertNotIn('old', text)
        output.write('latest')
        self.assertGreater(output.snapshot()[0], revision)

    def test_capture_tees_output_and_flushes_existing_redirect(self):
        original = io.StringIO()
        output = desktop.RecentOutput()
        stream = desktop.CapturedStream(output, original)
        self.assertEqual(stream.write('startup\n'), 8)
        stream.flush()
        self.assertEqual(original.getvalue(), 'startup\n')
        self.assertEqual(output.snapshot()[1], 'startup\n')


@unittest.skipUnless(os.name == 'nt', 'Windows desktop')
class StatusWindowTests(unittest.TestCase):
    def setUp(self):
        self.output = desktop.RecentOutput()
        self.window = desktop.StatusWindow(self.output)

    def tearDown(self):
        self.window.finish()
        del self.window
        gc.collect()  # Release Tcl on the main thread before server-worker tests.

    def test_start_hidden_show_close_and_minimize_keep_server_running(self):
        self.assertEqual(self.window.root.state(), 'withdrawn')
        self.window.tray_available = True
        self.window.show()
        self.window.root.update()
        self.assertEqual(self.window.root.state(), 'normal')
        self.window.close_window()
        self.assertEqual(self.window.root.state(), 'withdrawn')
        self.assertFalse(self.window.stop.is_set())
        self.window.show()
        self.window.root.update()
        self.window.root.iconify()
        self.window.root.update()
        self.assertEqual(self.window.root.state(), 'withdrawn')
        self.assertFalse(self.window.stop.is_set())

    def test_tray_failure_exposes_status_and_close_requests_exit(self):
        self.window.events.put(('tray_failed', 'Tray unavailable'))
        self.window.poll()
        self.window.root.update()
        self.assertEqual(self.window.root.state(), 'normal')
        self.assertIn('Tray unavailable', self.output.snapshot()[1])
        self.window.close_window()
        self.assertTrue(self.window.stop.is_set())

    def test_startup_failure_is_visible_with_diagnostics(self):
        self.output.write('Unable to listen on port 9999\n')
        self.window.events.put(('finished', 1))
        self.window.poll()
        self.window.root.update()
        self.assertEqual(self.window.root.state(), 'normal')
        self.assertIn('Unable to listen', self.window.text.get('1.0', 'end'))
        self.assertIn('Failed', self.window.root.title())

    def test_repeated_exit_is_safe_and_waits_for_worker(self):
        self.window.request_exit()
        self.window.request_exit()
        self.assertTrue(self.window.stop.is_set())
        self.assertTrue(self.window.root.winfo_exists())
        self.assertIn('Stopping', self.window.root.title())

    def test_worker_failure_queues_result_and_traceback(self):
        import launcher
        with patch.object(launcher, 'main', side_effect=ValueError('startup test failure')):
            self.window.run_server()
        event, result = self.window.events.get_nowait()
        self.assertEqual((event, result), ('finished', 1))
        self.assertIn('ValueError: startup test failure', self.output.snapshot()[1])

    def test_native_tray_failure_has_no_non_daemon_setup_thread(self):
        import pystray
        import threading
        before = set(threading.enumerate())
        with patch.object(pystray.Icon, '_run', side_effect=OSError('Native tray failed')):
            self.window.start_tray()
            self.window.tray_thread.join(2)
        self.window.poll()
        self.assertTrue(self.window.tray_failed)
        self.assertEqual(self.window.root.state(), 'normal')
        self.assertIn('Native tray failed', self.output.snapshot()[1])
        leaked = [thread for thread in threading.enumerate() if thread not in before and not thread.daemon]
        self.assertEqual(leaked, [])


if __name__ == '__main__':
    unittest.main()
