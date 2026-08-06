import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "Scripts" / "demo_model_gateway.py"
sys.path.insert(0, str(PROJECT_ROOT))

from Scripts.demo_model_gateway import build_demo_schema


class ModelGatewayDemoTests(unittest.TestCase):
    def test_live_demo_schema_rejects_offline_mode(self):
        schema = build_demo_schema("live")

        self.assertEqual(schema["properties"]["mode"]["enum"], ["live"])

    def test_demo_schema_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            build_demo_schema("unknown")

    def test_default_demo_is_offline_and_observable(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("provider: mock", completed.stdout)
        self.assertIn("response_id: mock-1", completed.stdout)
        self.assertIn("status: succeeded", completed.stdout)
        self.assertIn("tokens: input=12, output=9, total=21", completed.stdout)
        self.assertIn("gateway.succeeded", completed.stdout)

    def test_deepseek_live_mode_reports_missing_key_without_network(self):
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        environment.pop("DEEPSEEK_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--live", "--provider", "deepseek"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("DEEPSEEK_API_KEY is required", completed.stdout)


if __name__ == "__main__":
    unittest.main()
