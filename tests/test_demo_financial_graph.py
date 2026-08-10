import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "Scripts" / "demo_financial_graph.py"


class FinancialGraphDemoTests(unittest.TestCase):
    def run_demo(self, *arguments):
        return subprocess.run(
            [sys.executable, str(DEMO_SCRIPT), *arguments],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

    def test_confirmed_demo_prints_complete_graph_and_safe_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            completed = self.run_demo(
                "--confirm",
                "--output-dir",
                temp_dir,
            )
            report_path = Path(temp_dir) / "sz000001-offline-financial-report.json"
            audit_path = Path(temp_dir) / "sz000001-offline-audit-log.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            audit = json.loads(audit_path.read_text(encoding="utf-8"))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("C3 最终标准化金融分析报告", completed.stdout)
        self.assertIn("【2. 四个专业 Agent 结论】", completed.stdout)
        self.assertIn("【4. Bull/Bear 结构化辩论，共 2 轮】", completed.stdout)
        self.assertIn("条件路由: 进入风控复核（risk_review）", completed.stdout)
        self.assertIn("决策状态: 批准（approved）", completed.stdout)
        self.assertIn("批准=15.00%", completed.stdout)
        self.assertIn("预计单笔亏损: 0.98%", completed.stdout)
        self.assertIn("market_bearish_skip: 跳过（skipped）", completed.stdout)
        self.assertIn("order_created=false", completed.stdout)
        self.assertIn("real_trading_allowed=false", completed.stdout)
        self.assertIn("完整结构化报告:", completed.stdout)
        self.assertIn("Graph/Harness 审计日志:", completed.stdout)
        self.assertEqual(report["status"], "financial_graph_completed")
        self.assertEqual(report["final_decision"]["status"], "approved")
        self.assertFalse(report["real_trading_allowed"])
        self.assertEqual(
            audit["graph"]["execution_order"],
            ["c1_research", "trader", "market_route", "risk_manager", "finalize"],
        )

    def test_default_demo_displays_report_without_writing_files(self):
        completed = self.run_demo("--confirm")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("【10. 输出文件】", completed.stdout)
        self.assertIn("本次未生成文件", completed.stdout)
        self.assertIn("完整报告已显示在当前终端", completed.stdout)

    def test_recovery_demo_retries_only_risk_manager_and_cleans_checkpoint(self):
        completed = self.run_demo("--confirm", "--verify-recovery")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("C3 Checkpoint 恢复验证", completed.stdout)
        self.assertIn("C1=1, Trader=1, Risk Manager=2", completed.stdout)
        self.assertIn("C1 和 Trader 未重复执行", completed.stdout)
        self.assertIn("临时 Checkpoint 已自动清理", completed.stdout)
        self.assertIn("C3 最终标准化金融分析报告", completed.stdout)


if __name__ == "__main__":
    unittest.main()
