import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_fundamental_analysis.py"


class FundamentalAnalysisDemoTests(unittest.TestCase):
    def test_demo_prints_statements_valuation_dcf_and_traces(self):
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
        self.assertIn("资产负债表（balance sheet）", completed.stdout)
        self.assertIn("利润表（income statement）", completed.stdout)
        self.assertIn("现金流量表（cash flow）", completed.stdout)
        self.assertIn("PE=5.040000", completed.stdout)
        self.assertIn("安全边际=63.3730%", completed.stdout)
        self.assertIn("综合基本面评分（score）: 60", completed.stdout)
        self.assertIn("cognitive_loop.completed", completed.stdout)
        self.assertIn("fundamental_value_recompute", completed.stdout)
        self.assertIn("postflight.passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
