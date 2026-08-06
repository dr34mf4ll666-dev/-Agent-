import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "Scripts" / "demo_echo.py"


class EchoDemoTests(unittest.TestCase):
    def test_echo_demo_runs_through_harness_and_prints_trace(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--task", "verify echo"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("input: verify echo", completed.stdout)
        self.assertIn("output: verify echo", completed.stdout)
        self.assertIn("agent: echo", completed.stdout)
        self.assertIn("preflight.passed", completed.stdout)
        self.assertIn("postflight.passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
