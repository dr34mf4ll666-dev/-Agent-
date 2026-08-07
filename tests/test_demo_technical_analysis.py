import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_technical_analysis.py"


class TechnicalAnalysisDemoTests(unittest.TestCase):
    def test_demo_uses_real_fixture_and_prints_full_loop_and_indicators(self):
        completed = subprocess.run(
            [sys.executable, str(DEMO_SCRIPT)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("运行模式（mode）: offline", completed.stdout)
        self.assertIn("K 线数量（bars）: 30", completed.stdout)
        self.assertIn("数据来源（source）: akshare.stock_zh_a_hist_tx", completed.stdout)
        self.assertIn("短期均线（SMA5）=11.4420", completed.stdout)
        self.assertIn("指数平滑异同移动平均线（MACD）: 快线（DIF）=0.2573", completed.stdout)
        self.assertIn("相对强弱指标（RSI14）: 62.2919", completed.stdout)
        self.assertIn("随机指标（KDJ）:", completed.stdout)
        self.assertIn("布林带（BOLL）:", completed.stdout)
        self.assertIn("综合信号评分（signal）: 10，中性（neutral）", completed.stdout)
        self.assertIn("cognitive_loop.completed", completed.stdout)
        self.assertIn("technical_indicator_recompute", completed.stdout)
        self.assertIn("不构成投资建议", completed.stdout)
        self.assertIn("postflight.passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
