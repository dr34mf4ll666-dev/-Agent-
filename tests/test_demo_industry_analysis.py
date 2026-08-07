import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_industry_analysis.py"


class IndustryAnalysisDemoTests(unittest.TestCase):
    def test_demo_prints_profile_chain_leaders_and_traces(self):
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
        self.assertIn("行业画像（industry profile）", completed.stdout)
        self.assertIn("景气度（prosperity）: hot", completed.stdout)
        self.assertIn("产业链（industry chain）", completed.stdout)
        self.assertIn("龙头排序（leaders）", completed.stdout)
        self.assertIn("综合行业评分（score）: 40", completed.stdout)
        self.assertIn("cognitive_loop.completed", completed.stdout)
        self.assertIn("industry_value_recompute", completed.stdout)
        self.assertIn("postflight.passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
