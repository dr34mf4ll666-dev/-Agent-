import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ObservabilityDemoTests(unittest.TestCase):
    def test_demo_prints_all_acceptance_metrics_without_writing_report(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_observability.py"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("【总览】", completed.stdout)
        self.assertIn("失败率: 25.00%", completed.stdout)
        self.assertIn("Token 消耗: 输入=12，输出=9，总计=21", completed.stdout)
        self.assertIn("【逐次调用链】", completed.stdout)
        self.assertIn("失败原因: GuardrailViolation", completed.stdout)
        self.assertIn("结论: 本切片验收通过", completed.stdout)


if __name__ == "__main__":
    unittest.main()
