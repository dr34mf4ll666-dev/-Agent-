import subprocess
import sys
import os
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InterviewShowcaseDemoTests(unittest.TestCase):
    def test_demo_is_offline_and_prints_all_acceptance_sections(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_interview_showcase.py"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("网络访问: 未使用", completed.stdout)
        self.assertIn("正常运行", completed.stdout)
        self.assertIn("数据源超时与有限重试", completed.stdout)
        self.assertIn("缓存降级", completed.stdout)
        self.assertIn("Checkpoint 恢复", completed.stdout)
        self.assertIn("输出校验失败", completed.stdout)
        self.assertIn("P50=", completed.stdout)
        self.assertIn("两份报告为什么不同", completed.stdout)


if __name__ == "__main__":
    unittest.main()
