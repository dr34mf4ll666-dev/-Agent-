import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "Scripts" / "demo_financial_data_hub.py"


class FinancialDataHubDemoTests(unittest.TestCase):
    def test_default_demo_lists_every_dataset_offline(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("mode: offline", completed.stdout)
        self.assertIn("market.weekly", completed.stdout)
        self.assertIn("fundamental.balance_sheet", completed.stdout)
        self.assertIn("macro.shibor", completed.stdout)
        self.assertIn("sentiment.research", completed.stdout)
        self.assertIn("tushare.daily", completed.stdout)


if __name__ == "__main__":
    unittest.main()
