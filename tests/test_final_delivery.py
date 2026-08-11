import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.final_delivery import CommandExecution, FinalDeliveryRuntime


OUTPUTS = {
    "demo_echo.py": "agent: echo\noutput: final delivery verification\npreflight.passed\npostflight.passed\n",
    "demo_financial_graph.py": (
        "C3 最终标准化金融分析报告\n- 决策状态: approved\n"
        "- 批准动作: buy\nreal_trading_allowed=false\n"
    ),
    "demo_backtest_experiment.py": (
        "- 组合收益率: -0.5408%\n- 最大回撤: 0.7754%\n"
        "- 年化夏普: -0.8463\n总体结果: 通过\nreal_trading_allowed=false\n"
    ),
    "demo_d2_engineering.py": (
        "用例=4\n阈值=3\n幻觉率 80% 0%\n结论: D2 验收通过\n"
    ),
    "demo_paper_trading.py": (
        "D4 持续模拟运行\n本次状态: simulated_fill\n"
        "数据模式: offline\n账户现金: 85336.67 元\nreal_trading_allowed=false\n"
    ),
}


class _FakeRunner:
    def __init__(self, *, fail_script=None):
        self.fail_script = fail_script
        self.commands = []

    def run(self, command, *, cwd):
        self.commands.append((tuple(command), cwd))
        script = Path(command[1]).name
        if script == self.fail_script:
            return CommandExecution(1, "incomplete", "expected failure", 10)
        return CommandExecution(0, OUTPUTS[script], "", 10)


class FinalDeliveryTests(unittest.TestCase):
    def test_one_interface_checks_environment_workflows_documents_and_waiver(self):
        runner = _FakeRunner()
        report = FinalDeliveryRuntime.from_project(
            PROJECT_ROOT,
            command_runner=runner,
        ).run()
        value = report.to_mapping()

        self.assertTrue(report.passed)
        self.assertEqual(value["status"], "final_delivery_completed")
        self.assertEqual(len(value["workflows"]), 5)
        self.assertEqual(len(runner.commands), 5)
        self.assertTrue(all(item["passed"] for item in value["environment"]))
        self.assertTrue(all(item["passed"] for item in value["documents"]))
        self.assertEqual(
            value["duration_requirement"]["proof_status"],
            "waived_not_proven",
        )
        self.assertTrue(
            value["duration_requirement"]["accepted_for_revised_scope"]
        )
        self.assertFalse(value["safety"]["real_trading_allowed"])

    def test_failed_required_workflow_fails_the_final_delivery(self):
        report = FinalDeliveryRuntime.from_project(
            PROJECT_ROOT,
            command_runner=_FakeRunner(fail_script="demo_financial_graph.py"),
        ).run()
        value = report.to_mapping()

        self.assertFalse(report.passed)
        failed = [item for item in value["workflows"] if not item["passed"]]
        self.assertEqual([item["name"] for item in failed], ["C3 完整金融 Graph"])
        self.assertEqual(failed[0]["error"], "expected failure")

    def test_final_document_contains_every_required_delivery_section(self):
        content = (PROJECT_ROOT / "docs/final-delivery.md").read_text(
            encoding="utf-8"
        )
        for heading in (
            "最终架构图",
            "Graph Schema",
            "Agent 卡片",
            "数据字典",
            "运行手册",
            "回测报告",
            "Harness 对比实验报告",
            "模拟运行复盘",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, content)


if __name__ == "__main__":
    unittest.main()
