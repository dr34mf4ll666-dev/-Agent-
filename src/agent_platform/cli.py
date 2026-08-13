"""通用 Agent 平台稳定命令入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .d2_engineering import D2EngineeringRuntime
from .dashboard import serve_dashboard
from .dashboard_startup import configure_deepseek_for_dashboard
from .final_delivery import FinalDeliveryRuntime
from .finance import DynamicDebateEvaluationRuntime, print_dynamic_debate_evaluation
from .product_acceptance import ProductAcceptanceRuntime, print_product_acceptance


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-platform")
    subparsers = parser.add_subparsers(dest="command", required=True)
    d2 = subparsers.add_parser("d2-verify", help="运行 D2 Harness 工程化离线总验收")
    d2.add_argument("--config", type=Path, default=None, help="可选 Harness JSON 配置")
    d2.add_argument("--dataset", type=Path, default=None, help="可选固定评估数据集")
    d3 = subparsers.add_parser("d3-compare", help="单独运行 D3 Harness 价值对比")
    d3.add_argument("--config", type=Path, default=None, help="可选 Harness JSON 配置")
    d3.add_argument("--dataset", type=Path, default=None, help="可选固定评估数据集")
    subparsers.add_parser("d4-verify", help="运行最终交付离线总验收")
    dashboard = subparsers.add_parser("dashboard", help="启动 A-D 一体化 Web 控制台")
    dashboard.add_argument("--port", type=int, default=8765, help="本机端口，默认 8765")
    dashboard.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    dashboard.add_argument(
        "--no-key-prompt",
        action="store_true",
        help="不询问 DeepSeek API Key，直接使用已有环境变量或固定格式",
    )
    subparsers.add_parser("verify-all", help="验收 A-D、客户前台和团队后台")
    debate = subparsers.add_parser("debate-eval", help="运行受约束动态多空辩论固定评测")
    debate.add_argument("--live", action="store_true", help="使用真实 DeepSeek")
    debate.add_argument("--model", default="deepseek-v4-flash", help="DeepSeek 模型名")
    debate.add_argument("--dataset", type=Path, default=None, help="可选固定评测集")
    debate.add_argument("--no-key-prompt", action="store_true", help="不询问 DeepSeek API Key")
    debate.add_argument("--output", type=Path, default=None, help="可选：保存含逐次原始结果的 JSON")
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
    if args.command == "d4-verify":
        try:
            report = FinalDeliveryRuntime.from_project().run()
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"最终交付验收失败: {error}", file=sys.stderr)
            return 2
        _print_d4_report(report.to_mapping())
        return 0 if report.passed else 1
    if args.command == "dashboard":
        selection = configure_deepseek_for_dashboard(
            prompt_enabled=not args.no_key_prompt,
            interactive=sys.stdin.isatty(),
        )
        print(selection.message)
        serve_dashboard(port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "verify-all":
        try:
            report = ProductAcceptanceRuntime.from_project().run()
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"项目整体验收失败: {error}", file=sys.stderr)
            return 2
        print_product_acceptance(report)
        return 0 if report.passed else 1
    if args.command == "debate-eval":
        if args.live:
            selection = configure_deepseek_for_dashboard(
                prompt_enabled=not args.no_key_prompt,
                interactive=sys.stdin.isatty(),
            )
            print(selection.message)
            if not selection.enabled:
                print("真实评测未启动：需要 DeepSeek API Key。", file=sys.stderr)
                return 2
        try:
            report = DynamicDebateEvaluationRuntime.from_project(
                live=args.live,
                model=args.model,
            ).run(args.dataset)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"动态辩论评测失败: {error}", file=sys.stderr)
            return 2
        value = report.to_mapping()
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"原始评测结果已保存: {args.output}")
        print_dynamic_debate_evaluation(value)
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


def _print_d4_report(report: dict) -> None:
    print("=== D4 最终交付统一验收 ===")
    print("默认模式: offline（真实数据证据由已验证记录和本地账本说明）")

    print("\n【1. 环境检查】")
    for item in report["environment"]:
        print(
            f"- {'通过' if item['passed'] else '失败'}: "
            f"{item['name']}（{item['detail']}）"
        )

    print("\n【2. 主要流程复现】")
    for item in report["workflows"]:
        print(
            f"- {'通过' if item['passed'] else '失败'}: {item['name']}，"
            f"耗时={item['duration_ms']}ms"
        )
        for line in item["summary"]:
            print(f"  {line}")
        if item["error"]:
            print(f"  error={item['error']}")

    print("\n【3. 最终文档包】")
    for item in report["documents"]:
        print(f"- {'通过' if item['passed'] else '失败'}: {item['name']} -> {item['path']}")

    paper = report["paper_evidence"]
    print("\n【4. 本地模拟运行证据】")
    if paper["available"]:
        print(
            f"- session={paper['session_id']}，cycles={paper['cycle_count']}，"
            f"failures={paper['failure_count']}，真实日期={paper['live_trading_dates']}"
        )
        print(f"- 账户={paper['account']}")
    else:
        print(f"- 当前环境没有本地账本；{paper['note']}")

    duration = report["duration_requirement"]
    print("\n【5. 时间要求处理】")
    print(f"- 原要求: {duration['original_requirement']}")
    print(f"- 已观察真实行情日期数: {duration['observed_live_trading_days']}")
    print(f"- 证据状态: {duration['proof_status']}")
    print("- 用户已明确豁免等待时间；不宣称完成长周期稳定性证明。")

    safety = report["safety"]
    print("\n【6. 安全边界】")
    print(f"- simulation_only={str(safety['simulation_only']).lower()}")
    print(f"- order_created={str(safety['order_created']).lower()}")
    print(f"- real_trading_allowed={str(safety['real_trading_allowed']).lower()}")

    print("\n【最终结论】")
    print(
        "D4 调整后验收通过；项目交付完成。"
        if report["passed"]
        else "D4 验收未通过，请查看失败项。"
    )
    print("边界: 时间条件已豁免但未被证明，项目不保证盈利且不执行真实交易。")


if __name__ == "__main__":
    raise SystemExit(main())
