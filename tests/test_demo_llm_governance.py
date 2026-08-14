import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LLMGovernanceDemoTests(unittest.TestCase):
    def test_demo_shows_versions_cache_and_budget_boundary(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_llm_governance.py"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("P7 LLM 治理演示", completed.stdout)
        self.assertIn("policy_version: p7-demo-policy-v1", completed.stdout)
        self.assertIn("second_call_cache_hit: True", completed.stdout)
        self.assertIn("provider_calls: 1", completed.stdout)
        self.assertIn("budget_rejection:", completed.stdout)
        self.assertIn("deterministic_finance_controls_unchanged=true", completed.stdout)


if __name__ == "__main__":
    unittest.main()
