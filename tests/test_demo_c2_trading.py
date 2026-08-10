import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_c2_trading.py"


class C2TradingDemoTests(unittest.TestCase):
    def run_demo(self, *arguments):
        return subprocess.run(
            [sys.executable, str(DEMO_SCRIPT), *arguments],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

    def test_default_demo_stops_at_human_confirmation(self):
        completed = self.run_demo()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("status=pending_human_confirmation", completed.stdout)
        self.assertIn("simulation_execution_allowed=false", completed.stdout)
        self.assertIn("添加 --confirm", completed.stdout)
        self.assertIn("order_created=false", completed.stdout)

    def test_confirmed_demo_passes_all_risk_controls(self):
        completed = self.run_demo("--confirm")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Trader 候选: buy", completed.stdout)
        self.assertIn("status=approved", completed.stdout)
        self.assertIn("批准仓位: 15.00%", completed.stdout)
        self.assertIn("预计单笔亏损: 0.98%", completed.stdout)
        self.assertIn("single_trade_loss: passed", completed.stdout)
        self.assertIn("sector_exposure: passed", completed.stdout)
        self.assertIn("simulation_execution_allowed=true", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)
        self.assertIn("c2.completed", completed.stdout)
        self.assertIn("guardrail.output.passed", completed.stdout)
        self.assertIn("C2 已完成 Trader 与 Risk Manager", completed.stdout)


if __name__ == "__main__":
    unittest.main()
