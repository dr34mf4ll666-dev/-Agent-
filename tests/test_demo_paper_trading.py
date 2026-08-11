import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "Scripts" / "demo_paper_trading.py"


class PaperTradingDemoTests(unittest.TestCase):
    def run_demo(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

    def test_confirmed_demo_shows_fill_records_and_unmet_live_duration(self):
        completed = self.run_demo("--confirm")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("D4 持续模拟运行：单次直观验收", completed.stdout)
        self.assertIn("本次状态: simulated_fill", completed.stdout)
        self.assertIn("只写入 local_simulator", completed.stdout)
        self.assertIn("confirmations=1", completed.stdout)
        self.assertIn("连续运行条件=尚未满足", completed.stdout)
        self.assertIn("T4.3=in_progress", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)
        self.assertIn("临时账本", completed.stdout)

    def test_unconfirmed_demo_makes_the_manual_gate_visible(self):
        completed = self.run_demo()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("本次状态: pending_human_confirmation", completed.stdout)
        self.assertIn("请显式添加 --confirm", completed.stdout)

    def test_persistent_ledger_can_be_reviewed_without_another_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger = str(Path(temp_dir) / "paper.json")
            first = self.run_demo("--confirm", "--ledger", ledger)
            review = self.run_demo("--review-only", "--ledger", ledger)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertIn("D4 持续模拟运行：账本状态", review.stdout)
        self.assertIn("运行=1，成交=1", review.stdout)
        self.assertIn("T4.3=in_progress", review.stdout)
        self.assertIn("最近运行:", review.stdout)


if __name__ == "__main__":
    unittest.main()
