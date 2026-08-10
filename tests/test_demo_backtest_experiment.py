import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "Scripts" / "demo_backtest_experiment.py"


class BacktestExperimentDemoTests(unittest.TestCase):
    def test_demo_shows_real_pool_future_rejection_baseline_and_safety(self):
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
        self.assertIn("3 只股票 × 243 根日线", completed.stdout)
        self.assertIn("故意放入下一交易日证据: 已拒绝", completed.stdout)
        self.assertIn("沪深300收益率", completed.stdout)
        self.assertIn("相对沪深300超额收益", completed.stdout)
        self.assertIn("滑点=", completed.stdout)
        self.assertIn("达标=False", completed.stdout)
        self.assertIn("涨跌停方向权限拦截: 2 次", completed.stdout)
        self.assertIn("分红送转事件应用: 1 次", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)
        self.assertIn("D1 总验收结论", completed.stdout)
        self.assertIn("总体结果: 通过", completed.stdout)


if __name__ == "__main__":
    unittest.main()
