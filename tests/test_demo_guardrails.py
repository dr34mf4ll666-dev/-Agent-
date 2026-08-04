import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_guardrails.py"


class GuardrailDemoTests(unittest.TestCase):
    def test_demo_shows_pass_keyword_block_and_rate_limit(self):
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
        self.assertIn("安全输出: passed", completed.stdout)
        self.assertIn("json_schema", completed.stdout)
        self.assertIn("source_attribution", completed.stdout)
        self.assertIn("rate_limiter", completed.stdout)
        self.assertIn("keyword_blocker", completed.stdout)
        self.assertIn("cross_validator", completed.stdout)
        self.assertIn("关键词拦截: postflight failed", completed.stdout)
        self.assertIn("blocked keyword: 绝对稳赚", completed.stdout)
        self.assertIn("限流拦截: preflight failed", completed.stdout)
        self.assertIn("exceeded 2 calls", completed.stdout)
        self.assertIn("guardrail.output.failed", completed.stdout)
        self.assertIn("guardrail.input.failed", completed.stdout)


if __name__ == "__main__":
    unittest.main()
