import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "Scripts" / "demo_financial_batch.py"


class FinancialBatchDemoTests(unittest.TestCase):
    def test_batch_refuses_to_fake_twenty_stocks_with_offline_fixture(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("不能用一份离线样本冒充 20 只股票", completed.stdout)


if __name__ == "__main__":
    unittest.main()
