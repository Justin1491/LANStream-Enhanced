"""Run the PowerShell process checker's behavior tests when a runtime is available."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


POWERSHELL = (os.environ.get('LANSTREAM_TEST_POWERSHELL')
              or shutil.which('powershell.exe') or shutil.which('pwsh'))
BASE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(POWERSHELL, 'PowerShell runtime required')
class ProcessCheckTests(unittest.TestCase):
    def test_inventory_selection_and_verified_stop(self):
        runtime = Path(shutil.which(POWERSHELL) or POWERSHELL).resolve()
        result = subprocess.run(
            [POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'RemoteSigned', '-File', str(BASE / 'tests/process_check_tests.ps1')],
            capture_output=True, text=True, timeout=90,
            # Do not inherit incompatible modules from a different PowerShell edition.
            env=dict(os.environ, PSModulePath=str(runtime.parent / 'Modules')),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS: process inventory policy, identity, selection and stop guards', result.stdout)


if __name__ == '__main__':
    unittest.main()
