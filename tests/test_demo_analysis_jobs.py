import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AnalysisJobsDemoTests(unittest.TestCase):
    def test_demo_prints_async_progress_result_and_boundary(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_analysis_jobs.py"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("P1 异步分析任务演示", completed.stdout)
        self.assertIn("任务编号:", completed.stdout)
        self.assertIn("技术走势 Agent", completed.stdout)
        self.assertIn("风险经理复核", completed.stdout)
        self.assertIn("最终报告:", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)
        self.assertIn("失败只重试未完成节点", completed.stdout)
        self.assertIn("P1 异步任务中心完整验收通过", completed.stdout)


if __name__ == "__main__":
    unittest.main()
