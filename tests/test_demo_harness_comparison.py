import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class HarnessComparisonDemoTests(unittest.TestCase):
    def test_d3_demo_prints_metrics_raw_cases_costs_and_boundary(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_harness_comparison.py"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("【总指标对比】", completed.stdout)
        self.assertIn("【逐用例原始结果】", completed.stdout)
        self.assertIn("source-grounding", completed.stdout)
        self.assertIn("Token 总成本", completed.stdout)
        self.assertIn("结论: D3 验收通过", completed.stdout)
        self.assertIn("不代表真实模型线上质量", completed.stdout)


if __name__ == "__main__":
    unittest.main()
