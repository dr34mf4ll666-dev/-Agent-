import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_combined_analysis.py"


class CombinedAnalysisDemoTests(unittest.TestCase):
    def test_demo_prints_four_specialists_parallel_wave_and_stage_boundary(self):
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
        self.assertIn("Planner: technical、fundamental、industry、macro 四个 Agent 并行运行", completed.stdout)
        self.assertIn("技术分析（technical）", completed.stdout)
        self.assertIn("基本面（fundamental）", completed.stdout)
        self.assertIn("行业（industry）", completed.stdout)
        self.assertIn("大盘/宏观（macro）", completed.stdout)
        self.assertIn("technical,fundamental,industry,macro", completed.stdout)
        self.assertIn("结构化辩论（Claim → Evidence → Reasoning，共 2 轮）", completed.stdout)
        self.assertIn("第 1 轮 Bull", completed.stdout)
        self.assertIn("第 1 轮 Bear", completed.stdout)
        self.assertIn("综合结论（Synthesis）", completed.stdout)
        self.assertIn("目标价研究区间", completed.stdout)
        self.assertIn("Bull 目标价上限", completed.stdout)
        self.assertIn("Bear 目标价下限", completed.stdout)
        self.assertIn("置信度: 69 / 100", completed.stdout)
        self.assertIn("Consistency Check: passed", completed.stdout)
        self.assertIn("Bias Detector: passed", completed.stdout)
        self.assertIn("仓位上限 30% -> 15%", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)
        self.assertIn("c1.completed", completed.stdout)
        self.assertIn("C1 已完成", completed.stdout)


if __name__ == "__main__":
    unittest.main()
