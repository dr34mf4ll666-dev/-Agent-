import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_loop_engineering.py"


class LoopEngineeringDemoTests(unittest.TestCase):
    def test_demo_runs_three_memory_layers_and_all_loop_triggers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / "a3"
            completed = subprocess.run(
                [sys.executable, str(DEMO_SCRIPT), "--runtime", str(runtime)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(
                "长期记忆: project=1, organization=1",
                completed.stdout,
            )
            self.assertIn("Heartbeat: completed", completed.stdout)
            self.assertIn("Cron: completed", completed.stdout)
            self.assertIn("Hook: 1 run", completed.stdout)
            self.assertIn("Goal: 3 runs", completed.stdout)
            self.assertIn("运行台账: 6", completed.stdout)
            self.assertIn("独立工作目录: 6", completed.stdout)

            ledger = json.loads(
                (runtime / "run-ledger.json").read_text(encoding="utf-8")
            )
            memory = json.loads(
                (runtime / "long-term-memory.json").read_text(encoding="utf-8")
            )
            workspace_snapshots = tuple(
                (runtime / "tasks").glob("*/working-memory.json")
            )

        self.assertEqual(ledger["version"], 1)
        self.assertEqual(len(ledger["records"]), 6)
        self.assertTrue(all(item["status"] == "completed" for item in ledger["records"]))
        self.assertEqual(memory["version"], 1)
        self.assertEqual(len(memory["entries"]), 2)
        self.assertEqual(len(workspace_snapshots), 6)


if __name__ == "__main__":
    unittest.main()
