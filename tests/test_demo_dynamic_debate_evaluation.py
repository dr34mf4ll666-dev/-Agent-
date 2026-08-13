import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DynamicDebateEvaluationDemoTests(unittest.TestCase):
    def test_offline_demo_prints_metrics_raw_runs_and_online_boundary(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_dynamic_debate_evaluation.py"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("动态多空辩论固定评测", completed.stdout)
        self.assertIn("【固定模板基线】", completed.stdout)
        self.assertIn("【动态辩论】", completed.stdout)
        self.assertIn("候选证据有效率", completed.stdout)
        self.assertIn("观点多样性", completed.stdout)
        self.assertIn("正反平衡率", completed.stdout)
        self.assertIn("重试率", completed.stdout)
        self.assertIn("平均耗时", completed.stdout)
        self.assertIn("Token", completed.stdout)
        self.assertIn("结果稳定性", completed.stdout)
        self.assertIn("【验收阈值】", completed.stdout)
        self.assertIn("【逐次原始结果】", completed.stdout)
        self.assertIn("脚本化 Mock", completed.stdout)
        self.assertIn("不冒充真实 DeepSeek 质量", completed.stdout)

    def test_live_mode_without_key_fails_before_model_call(self):
        environment = os.environ.copy()
        environment.pop("DEEPSEEK_API_KEY", None)
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [
                sys.executable,
                "Scripts/demo_dynamic_debate_evaluation.py",
                "--live",
                "--no-key-prompt",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            env=environment,
        )

        self.assertEqual(completed.returncode, 2, completed.stdout + completed.stderr)
        self.assertIn("需要 DeepSeek API Key", completed.stderr)


if __name__ == "__main__":
    unittest.main()
