from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from run_lanstream import prepare_launch, run_child


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "source"
        self.tool = self.repo / "tools" / "mac-playback"
        (self.tool / "media").mkdir(parents=True)
        (self.repo / "launcher.py").write_text("# fake launcher\n")
        self.which = patch("run_lanstream.shutil.which", return_value="/fake/bin/tool").start()
        self.addCleanup(patch.stopall)

    def prepare(self, port=8766):
        return prepare_launch(self.tool, self.repo, "127.0.0.1", port, {
            "PATH": "/fake/bin", "KEEP": "yes", "LANSTREAM_PASSWORD": "private",
            "LANSTREAM_CONFIG_FILE": "/personal/config.txt", "LANSTREAM_PORT": "8061",
            "LANSTREAM_SESSION_SECRET": "private", "lanstream_custom": "private",
        })

    def test_isolated_config_and_environment(self):
        env = self.prepare()
        config = self.tool / "work" / "config.txt"
        self.assertEqual(config.read_text().splitlines(), [
            "port=8766", "use_master_password=false", "master_password=",
            "transcoder=software", "automatic_indexing=false", str((self.tool / "media").resolve()),
        ])
        self.assertEqual(env, {
            "PATH": "/fake/bin", "KEEP": "yes", "LANSTREAM_CONFIG_FILE": str(config.resolve()),
            "LANSTREAM_CACHE_DIR": str((self.tool / "work" / "cache").resolve()),
            "LANSTREAM_HOST": "127.0.0.1", "LANSTREAM_PORT": "8766", "LANSTREAM_TRANSCODER": "software",
        })
        self.assertEqual(self.which.call_args_list[0].args, ("ffmpeg",))
        self.assertEqual(self.which.call_args_list[1].args, ("ffprobe",))

    def test_missing_inputs_do_not_write_config(self):
        (self.repo / "launcher.py").unlink()
        with self.assertRaisesRegex(ValueError, "launcher.py"):
            self.prepare()
        (self.repo / "launcher.py").write_text("# fake launcher\n")
        (self.tool / "media").rmdir()
        with self.assertRaisesRegex(ValueError, "private samples"):
            self.prepare()
        self.assertFalse((self.tool / "work").exists())

    def test_required_media_tools(self):
        for missing in ("ffmpeg", "ffprobe"):
            with self.subTest(missing=missing):
                self.which.side_effect = lambda name: None if name == missing else "/fake/tool"
                with self.assertRaisesRegex(ValueError, missing):
                    self.prepare()
        self.assertFalse((self.tool / "work").exists())

    def test_invalid_port(self):
        for port in (0, -1, 65536):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "Port"):
                self.prepare(port)
        self.assertFalse((self.tool / "work").exists())

    @patch("run_lanstream.subprocess.Popen")
    def test_same_interpreter_and_source_working_directory(self, popen):
        popen.return_value.wait.return_value = 3
        env = {"PATH": "/fake/bin"}
        self.assertEqual(run_child(self.repo, env), 3)
        popen.assert_called_once_with([sys.executable, str(self.repo / "launcher.py")], cwd=self.repo, env=env)

    @patch("run_lanstream.subprocess.Popen")
    def test_ctrl_c_terminates_child(self, popen):
        child = popen.return_value
        child.wait.side_effect = [KeyboardInterrupt(), 0]
        child.poll.return_value = None
        self.assertEqual(run_child(self.repo, {}), 130)
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()
        self.assertEqual(child.wait.call_args.kwargs, {"timeout": 5})

    @patch("run_lanstream.subprocess.Popen")
    def test_ctrl_c_kills_unresponsive_child(self, popen):
        child = popen.return_value
        child.wait.side_effect = [KeyboardInterrupt(), subprocess.TimeoutExpired("launcher", 5), 0]
        child.poll.return_value = None
        self.assertEqual(run_child(self.repo, {}), 130)
        child.terminate.assert_called_once_with()
        child.kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
