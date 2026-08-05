import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_graph_engineering.py"


class GraphEngineeringDemoTests(unittest.TestCase):
    def test_demo_loads_yaml_runs_parallel_retry_and_renders_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "checkpoint.json"
            mermaid_path = Path(temp_dir) / "graph.mmd"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(DEMO_SCRIPT),
                    "--checkpoint",
                    str(checkpoint_path),
                    "--mermaid",
                    str(mermaid_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("调度策略: parallel", completed.stdout)
            self.assertIn("并行波次: fundamental,technical", completed.stdout)
            self.assertIn('"fundamental": 2', completed.stdout)
            self.assertIn('"technical": 1', completed.stdout)
            self.assertIn('"summary_score": 68', completed.stdout)
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            mermaid = mermaid_path.read_text(encoding="utf-8")

        self.assertEqual(checkpoint["version"], 2)
        self.assertEqual(checkpoint["attempts"]["fundamental"], 2)
        self.assertEqual(checkpoint["statuses"]["synthesize"], "completed")
        self.assertIn("flowchart TD", mermaid)
        self.assertIn("class prepare,fundamental,technical,synthesize completed", mermaid)


if __name__ == "__main__":
    unittest.main()
