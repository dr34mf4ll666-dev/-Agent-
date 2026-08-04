import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_cognitive_loop.py"


class CognitiveLoopDemoTests(unittest.TestCase):
    def test_demo_revises_invalid_action_then_completes(self):
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
        self.assertIn("=== 认知 Loop 离线演示 ===", completed.stdout)
        self.assertIn("Observation: failed", completed.stdout)
        self.assertIn("Reflection: revise", completed.stdout)
        self.assertIn("guardrail.input.failed", completed.stdout)
        self.assertIn("Observation: passed 9", completed.stdout)
        self.assertIn("Reflection: complete", completed.stdout)
        self.assertIn("guardrail.output.passed", completed.stdout)
        self.assertIn("Final: 计算结果是 9", completed.stdout)


if __name__ == "__main__":
    unittest.main()
