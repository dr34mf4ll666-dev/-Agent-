import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "Scripts" / "demo_final_delivery.py"


class FinalDeliveryDemoTests(unittest.TestCase):
    def test_demo_runs_all_major_workflows_and_prints_honest_final_status(self):
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
        self.assertIn("D4 最终交付统一验收", completed.stdout)
        self.assertIn("【1. 环境检查】", completed.stdout)
        self.assertIn("通过: 通用 Harness", completed.stdout)
        self.assertIn("通过: C3 完整金融 Graph", completed.stdout)
        self.assertIn("通过: D1 固定回测", completed.stdout)
        self.assertIn("通过: D2/D3 Harness 工程验收", completed.stdout)
        self.assertIn("通过: D4 本地模拟执行", completed.stdout)
        self.assertIn("证据状态: waived_not_proven", completed.stdout)
        self.assertIn("D4 调整后验收通过；项目交付完成", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)


if __name__ == "__main__":
    unittest.main()
