"""通用 Agent 平台稳定命令入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .d2_engineering import D2EngineeringRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-platform")
    subparsers = parser.add_subparsers(dest="command", required=True)
    d2 = subparsers.add_parser("d2-verify", help="运行 D2 Harness 工程化离线总验收")
    d2.add_argument("--config", type=Path, default=None, help="可选 Harness JSON 配置")
    d2.add_argument("--dataset", type=Path, default=None, help="可选固定评估数据集")
    d3 = subparsers.add_parser("d3-compare", help="单独运行 D3 Harness 价值对比")
    d3.add_argument("--config", type=Path, default=None, help="可选 Harness JSON 配置")
    d3.add_argument("--dataset", type=Path, default=None, help="可选固定评估数据集")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"d2-verify", "d3-compare"}:
        try:
            report = D2EngineeringRuntime.from_files(
                config_path=args.config,
                dataset_path=args.dataset,
            ).run()
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"配置或数据集校验失败: {error}", file=sys.stderr)
            return 2
        report_mapping = report.to_mapping()
        if args.command == "d2-verify":
            _print_d2_report(report_mapping)
        else:
            _print_d3_report(report_mapping)
        return 0 if report.passed else 1
    return 2


def _print_d2_report(report: dict) -> None:
    evaluator = report["evaluator"]["summary"]
    comparison = report["comparison"]
    before = comparison["without_harness"]["summary"]
    after = comparison["with_harness"]["summary"]
    circuit = report["circuit_breaker"]
    permissions = report["tool_permissions"]

    print("=== D2 Harness 工程化总验收 ===")
    print("模式（mode）: offline")
    print(f"固定数据集: {report['configuration']['dataset']}")
    print("\n【独立 Evaluator】")
    print(
        f"用例={evaluator['case_count']}，平均分={evaluator['average_score']:.2f}，"
        f"端到端成功率={evaluator['end_to_end_success_rate_percent']:.2f}%"
    )
    print("评分来源: 固定预期事实、禁用短语和工具规则；不读取 Agent 自评分。")

    print("\n【连续失败熔断与告警】")
    print(
        f"阈值={circuit['failure_threshold']}，实际执行={circuit['operation_calls']}，"
        f"状态={circuit['state']}，暂停={str(circuit['paused']).lower()}"
    )
    print(
        f"告警={circuit['alert_code']}，下一次阻断={circuit['blocked_code']}"
    )

    print("\n【Agent 最小工具权限】")
    print(
        f"已登记 Agent={permissions['policy_count']}，"
        f"越权操作实际执行次数={permissions['operation_calls']}"
    )
    for agent, tools in permissions["policies"].items():
        print(f"- {agent}: {tools}")

    print("\n【同任务、同数据、同候选配置对比】")
    print("指标                         无 Harness      有 Harness")
    print(
        f"幻觉率                       {before['hallucination_rate_percent']:>7.2f}%"
        f"        {after['hallucination_rate_percent']:>7.2f}%"
    )
    print(
        f"无效工具/API 调用            {before['invalid_api_calls']:>7}"
        f"        {after['invalid_api_calls']:>7}"
    )
    print(
        f"端到端成功率                 {before['end_to_end_success_rate_percent']:>7.2f}%"
        f"        {after['end_to_end_success_rate_percent']:>7.2f}%"
    )
    print(
        f"平均耗时(ms)                 {before['average_latency_ms']:>7.2f}"
        f"        {after['average_latency_ms']:>7.2f}"
    )
    print(
        f"Token 总成本                 {before['total_tokens']:>7}"
        f"        {after['total_tokens']:>7}"
    )
    print(
        "失败恢复成功率                    -"
        f"        {after['recovery_success_rate_percent']:>7.2f}%"
    )
    print("说明: 两组使用同一固定任务、数据和脚本化模型首轮输出；有 Harness 组会拦截并使用固定恢复输出。")
    print("该离线工程实验不代表真实模型线上质量。")

    print("\n【直观验收】")
    for item, passed in report["acceptance"].items():
        print(f"- {'通过' if passed else '失败'}: {item}")
    print(f"结论: {'D2 验收通过' if report['passed'] else 'D2 验收失败'}")


def _print_d3_report(report: dict) -> None:
    comparison = report["comparison"]
    before_report = comparison["without_harness"]
    after_report = comparison["with_harness"]
    before = before_report["summary"]
    after = after_report["summary"]

    print("=== D3 量化 Harness 价值：独立验收 ===")
    print("模式（mode）: offline")
    print(f"固定数据集: {before_report['dataset']}")
    print("实验控制: 相同任务、数据和脚本化模型首轮输出")
    print("\n【总指标对比】")
    print("指标                         无 Harness      有 Harness")
    print(
        f"幻觉率                       {before['hallucination_rate_percent']:>7.2f}%"
        f"        {after['hallucination_rate_percent']:>7.2f}%"
    )
    print(
        f"无效工具/API 调用            {before['invalid_api_calls']:>7}"
        f"        {after['invalid_api_calls']:>7}"
    )
    print(
        f"端到端成功率                 {before['end_to_end_success_rate_percent']:>7.2f}%"
        f"        {after['end_to_end_success_rate_percent']:>7.2f}%"
    )
    print(
        f"平均耗时(ms)                 {before['average_latency_ms']:>7.2f}"
        f"        {after['average_latency_ms']:>7.2f}"
    )
    print(
        f"Token 总成本                 {before['total_tokens']:>7}"
        f"        {after['total_tokens']:>7}"
    )
    print(
        "失败恢复成功率                    -"
        f"        {after['recovery_success_rate_percent']:>7.2f}%"
    )

    print("\n【逐用例原始结果】")
    after_by_id = {case["case_id"]: case for case in after_report["cases"]}
    for before_case in before_report["cases"]:
        after_case = after_by_id[before_case["case_id"]]
        print(f"- {before_case['case_id']}")
        print(
            "  无 Harness: "
            f"passed={str(before_case['passed']).lower()}，"
            f"hallucinations={before_case['hallucinated_claims']}，"
            f"invalid_calls={before_case['invalid_tool_calls']}，"
            f"latency={before_case['latency_ms']}ms，tokens={before_case['total_tokens']}"
        )
        print(
            "  有 Harness: "
            f"passed={str(after_case['passed']).lower()}，"
            f"recovered={str(after_case['recovery_succeeded']).lower()}，"
            f"latency={after_case['latency_ms']}ms，tokens={after_case['total_tokens']}"
        )

    improvement = comparison["improvement"]
    checks = {
        "两组使用同一固定数据集": (
            before_report["dataset"] == after_report["dataset"]
        ),
        "幻觉率已量化": improvement["hallucination_rate_change_points"] < 0,
        "无效调用已量化": improvement["invalid_api_calls_change"] < 0,
        "成功率已量化": improvement["success_rate_change_points"] > 0,
        "耗时与 Token 成本已如实展示": (
            improvement["average_latency_change_ms"] > 0
            and improvement["token_cost_change"] > 0
        ),
        "故障恢复率已量化": (
            improvement["recovery_success_rate_percent"] == 100.0
        ),
    }
    print("\n【直观验收】")
    for item, passed in checks.items():
        print(f"- {'通过' if passed else '失败'}: {item}")
    print(f"结论: {'D3 验收通过' if all(checks.values()) else 'D3 验收失败'}")
    print("边界: 这是固定离线工程实验，不代表真实模型线上质量。")


if __name__ == "__main__":
    raise SystemExit(main())
