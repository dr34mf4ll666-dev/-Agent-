import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "Scripts" / "demo_market_data.py"


class MarketDataDemoTests(unittest.TestCase):
    def test_default_demo_replays_the_verified_sample_offline(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("mode: offline", completed.stdout)
        self.assertIn("symbol: sz000001", completed.stdout)
        self.assertIn("bar_count: 4", completed.stdout)
        self.assertIn("fixture.loaded", completed.stdout)


if __name__ == "__main__":
    unittest.main()
