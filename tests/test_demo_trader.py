import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_trader.py"


class TraderDemoTests(unittest.TestCase):
    def test_default_demo_prints_candidate_safety_and_traces(self):
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
        self.assertIn("候选动作: buy（模拟买入候选）", completed.stdout)
        self.assertIn("目标价研究区间: 10.49 <= 11.22 <= 13.02", completed.stdout)
        self.assertIn("证据一致性置信度: 69 / 100", completed.stdout)
        self.assertIn("C1 门控后的仓位上限: 15%", completed.stdout)
        self.assertIn("order_created=false", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)
        self.assertIn("human_confirmation_required=true", completed.stdout)
        self.assertIn("trader.completed", completed.stdout)
        self.assertIn("guardrail.output.passed", completed.stdout)
        self.assertIn("demo_c2_trading.py 验证完整 Risk Manager", completed.stdout)


if __name__ == "__main__":
    unittest.main()
