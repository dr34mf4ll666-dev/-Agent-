import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AnalysisSnapshotDemoTests(unittest.TestCase):
    def test_demo_prints_all_source_paths_and_safety_boundary(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_analysis_snapshot.py"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("P2 统一分析快照验收", completed.stdout)
        self.assertIn("14 次请求 = 14 个唯一数据请求", completed.stdout)
        self.assertIn("备用源接管", completed.stdout)
        self.assertIn("缓存降级", completed.stdout)
        self.assertIn("部分结果", completed.stdout)
        self.assertIn("关键数据阻断", completed.stdout)
        self.assertIn("真实交易: 关闭", completed.stdout)


if __name__ == "__main__":
    unittest.main()
