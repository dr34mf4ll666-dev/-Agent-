import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_technical_analysis.py"


class TechnicalAnalysisDemoTests(unittest.TestCase):
    def test_demo_prints_indicators_rule_and_harness_trace(self):
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
        self.assertIn("SMA5: 12.70", completed.stdout)
        self.assertIn("SMA20: 11.95", completed.stdout)
        self.assertIn("趋势: bullish", completed.stdout)
        self.assertIn("规则: latest_close > sma_5 > sma_20", completed.stdout)
        self.assertIn("不构成投资建议", completed.stdout)
        self.assertIn("postflight.passed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
