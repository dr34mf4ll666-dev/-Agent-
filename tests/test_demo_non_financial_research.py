import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "Scripts" / "demo_non_financial_research.py"


class NonFinancialResearchDemoTests(unittest.TestCase):
    def _run_demo(self, *arguments: str):
        env = dict(os.environ)
        env.pop("DEEPSEEK_API_KEY", None)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_default_demo_is_offline_and_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run_demo(
                "--checkpoint",
                str(Path(directory) / "checkpoint.json"),
                "--timestamp",
                "2026-08-06T12:00:00+08:00",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("=== A5 非金融资料研究演示 ===", result.stdout)
        self.assertIn("mode: offline", result.stdout)
        self.assertIn(
            "execution_order: ['retrieve', 'organize', 'synthesize']",
            result.stdout,
        )
        self.assertIn("allowed_tools: ['local_document_search']", result.stdout)
        self.assertIn("model_calls: 4", result.stdout)
        self.assertIn("graph.completed", result.stdout)

    def test_demo_can_show_checkpoint_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run_demo(
                "--checkpoint",
                str(Path(directory) / "checkpoint.json"),
                "--timestamp",
                "2026-08-06T12:00:00+08:00",
                "--verify-recovery",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("预期故障: synthesize 节点失败", result.stdout)
        self.assertIn(
            "completed_before_failure: ['retrieve', 'organize']",
            result.stdout,
        )
        self.assertIn(
            "recovery_node_calls: {'retrieve': 0, 'organize': 0, 'synthesize': 1}",
            result.stdout,
        )

    def test_live_demo_without_key_fails_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self._run_demo(
                "--live",
                "--checkpoint",
                str(Path(directory) / "checkpoint.json"),
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("DEEPSEEK_API_KEY", result.stdout)


if __name__ == "__main__":
    unittest.main()
