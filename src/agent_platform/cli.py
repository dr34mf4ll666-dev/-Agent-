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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "d2-verify":
        try:
            report = D2EngineeringRuntime.from_files(
                config_path=args.config,
                dataset_path=args.dataset,
            ).run()
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"配置或数据集校验失败: {error}", file=sys.stderr)
            return 2
        _print_d2_report(report.to_mapping())
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


if __name__ == "__main__":
    raise SystemExit(main())
