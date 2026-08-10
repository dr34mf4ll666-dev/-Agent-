import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "Scripts" / "demo_backtest.py"


class BacktestDemoTests(unittest.TestCase):
    def test_demo_makes_time_cost_metrics_and_boundaries_visible(self):
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
        self.assertIn("D1 无未来数据回测核心演示", completed.stdout)
        self.assertIn("信号=2026-07-02T15:00:00+08:00", completed.stdout)
        self.assertIn("执行=2026-07-03T09:30:00+08:00", completed.stdout)
        self.assertIn("同一根K线成交=false", completed.stdout)
        self.assertIn("执行层使用未来数据=false", completed.stdout)
        self.assertIn("信号生成无未来数据验证=尚未完成", completed.stdout)
        self.assertIn("卖出印花税", completed.stdout)
        self.assertIn("年化夏普比率", completed.stdout)
        self.assertIn("order_created=false", completed.stdout)
        self.assertIn("本次结果只显示在终端，不生成文件", completed.stdout)
        self.assertIn("D1 本切片验收结论", completed.stdout)
        self.assertIn("总体结果: 通过", completed.stdout)


if __name__ == "__main__":
    unittest.main()
