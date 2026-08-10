import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class D2EngineeringDemoTests(unittest.TestCase):
    def test_demo_prints_complete_chinese_acceptance(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_d2_engineering.py"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("【独立 Evaluator】", completed.stdout)
        self.assertIn("状态=open，暂停=true", completed.stdout)
        self.assertIn("越权操作实际执行次数=0", completed.stdout)
        self.assertIn("幻觉率", completed.stdout)
        self.assertIn("结论: D2 验收通过", completed.stdout)


if __name__ == "__main__":
    unittest.main()
