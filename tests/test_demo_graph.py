import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_graph.py"


class GraphDemoTests(unittest.TestCase):
    def test_demo_runs_failure_recovery_and_writes_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "demo-checkpoint.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(DEMO_SCRIPT),
                    "--checkpoint",
                    str(checkpoint_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("首次执行在 recoverable 节点失败", completed.stdout)
            self.assertIn("正在从 Checkpoint 恢复", completed.stdout)
            self.assertIn(
                "执行顺序: prepare -> route -> approved -> recoverable -> finish",
                completed.stdout,
            )
            self.assertTrue(checkpoint_path.is_file())

            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))

        self.assertEqual(checkpoint["statuses"]["finish"], "completed")
        self.assertEqual(checkpoint["statuses"]["rejected"], "skipped")
        self.assertTrue(checkpoint["state"]["done"])


if __name__ == "__main__":
    unittest.main()
