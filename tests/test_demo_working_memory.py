import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_working_memory.py"


class WorkingMemoryDemoTests(unittest.TestCase):
    def test_demo_repairs_from_memory_and_restores_bounded_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "memory.json"
            completed = subprocess.run(
                [sys.executable, str(DEMO_SCRIPT), "--snapshot", str(snapshot_path)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("最终回答: 计算结果是 9", completed.stdout)
            self.assertIn("容量: 5", completed.stdout)
            self.assertIn("已淘汰: 2", completed.stdout)
            self.assertIn("#3 observation", completed.stdout)
            self.assertIn("#7 reflection", completed.stdout)
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(snapshot["version"], 1)
        self.assertEqual(snapshot["capacity"], 5)
        self.assertEqual(snapshot["dropped_count"], 2)
        self.assertEqual(len(snapshot["entries"]), 5)


if __name__ == "__main__":
    unittest.main()
