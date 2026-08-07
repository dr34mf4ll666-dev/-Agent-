import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_macro_analysis.py"


class MacroAnalysisDemoTests(unittest.TestCase):
    def test_demo_prints_regime_risk_and_traces(self):
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
        self.assertIn("指数趋势（index trend）", completed.stdout)
        self.assertIn("资金面（funds proxy）", completed.stdout)
        self.assertIn("Market Regime: mixed", completed.stdout)
        self.assertIn("风险偏好（risk appetite）: low", completed.stdout)
        self.assertIn("综合评分（score）: -15", completed.stdout)
        self.assertIn("cognitive_loop.completed", completed.stdout)
        self.assertIn("macro_value_recompute", completed.stdout)
        self.assertIn("postflight.passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
