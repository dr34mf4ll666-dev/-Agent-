"""Run the first C3 slice as one complete single-symbol financial Graph."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    C1DecisionQuery,
    CombinedAnalysisQuery,
    FinancialDataPolicy,
    FinancialGraphQuery,
    FinancialGraphRuntime,
    RiskContext,
    build_default_financial_graph_runtime,
    build_default_c1_decision_runtime,
    build_default_risk_manager_runtime,
    build_default_trader_runtime,
)
from agent_platform.core import GraphExecutionError, JsonCheckpointStore  # noqa: E402


DISPLAY_LABELS = {
    "positive": "正向",
    "strong_positive": "强正向",
    "cautious_positive": "谨慎正向",
    "negative": "负向",
    "strong_negative": "强负向",
    "neutral": "中性",
    "mixed": "分化",
    "hot": "高景气",
    "low": "低风险偏好",
    "risk_on": "风险偏好开启",
    "risk_off": "风险偏好关闭",
    "bearish": "看空",
    "bullish": "看多",
    "buy": "买入",
    "sell": "卖出",
    "hold": "持有",
    "reduce": "减仓",
    "approved": "批准",
    "adjusted": "调整后批准",
    "blocked": "阻断",
    "pending_human_confirmation": "等待人工确认",
    "forced_reduction": "强制减仓",
    "no_action": "无操作",
    "risk_review": "进入风控复核",
    "skip_bearish_buy": "看空买入阻断",
    "risk_manager": "风控管理器",
    "market_route": "市场条件路由",
    "completed": "完成",
    "skipped": "跳过",
    "passed": "通过",
    "triggered": "触发",
    "failed": "失败",
}

RISK_CHECK_LABELS = {
    "trader_candidate": "Trader候选一致性",
    "trading_session": "交易时段",
    "market_regime": "市场环境",
    "liquidity": "流动性",
    "stop_loss_take_profit": "止损止盈",
    "drawdown": "组合回撤",
    "single_trade_loss": "单笔亏损",
    "sector_exposure": "行业暴露",
    "human_confirmation": "人工确认",
    "real_trading_disabled": "真实交易关闭",
}


def _bilingual(value: object) -> str:
    code = str(value)
    label = DISPLAY_LABELS.get(code)
    return f"{label}（{code}）" if label else code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="C3 单股票完整金融 Graph 演示")
    parser.add_argument("--live", action="store_true", help="显式请求真实市场数据")
    parser.add_argument("--confirm", action="store_true", help="显式模拟人工确认")
    parser.add_argument("--symbol", default="sz000001")
    parser.add_argument("--sector", default="玻璃行业")
    parser.add_argument("--index-symbol", default="sh000300")
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default="20260807")
    parser.add_argument("--rounds", type=int, choices=(2, 3), default=2)
    parser.add_argument("--base-position-cap", type=int, default=30)
    parser.add_argument("--account-equity", default="100000")
    parser.add_argument("--current-position", default="0")
    parser.add_argument("--requested-position", default="15")
    parser.add_argument("--sector-exposure-other", default="5")
    parser.add_argument("--drawdown", default="5")
    parser.add_argument("--average-daily-turnover", default="500000000")
    parser.add_argument(
        "--evaluation-time",
        default="2026-08-07T10:00:00+08:00",
        help="带 +08:00 时区的模拟评估时间",
    )
    parser.add_argument("--stop-loss")
    parser.add_argument("--take-profit")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument(
        "--output-dir",
        help="可选：显式指定后才保存完整报告与审计日志",
    )
    parser.add_argument(
        "--verify-recovery",
        action="store_true",
        help="模拟 Risk Manager 首次故障并从临时 Checkpoint 恢复",
    )
    return parser


class _CountingC1Runtime:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def run(self, query):
        self.calls += 1
        return self.delegate.run(query)


class _CountingTraderRuntime:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def run_graph_node(self, state):
        self.calls += 1
        return self.delegate.run_graph_node(state)


class _FailOnceRiskRuntime:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def run_graph_node(self, state):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("演示用 Risk Manager 瞬时故障")
        return self.delegate.run_graph_node(state)


def _run_recovery_demo(query, policy):
    c1 = _CountingC1Runtime(
        build_default_c1_decision_runtime(project_root=PROJECT_ROOT, policy=policy)
    )
    trader = _CountingTraderRuntime(build_default_trader_runtime())
    risk = _FailOnceRiskRuntime(build_default_risk_manager_runtime())
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime = FinancialGraphRuntime(
            c1_runtime=c1,
            trader_runtime=trader,
            risk_manager_runtime=risk,
            checkpoint_store=JsonCheckpointStore(Path(temp_dir) / "checkpoint.json"),
        )
        try:
            runtime.run(query)
        except GraphExecutionError as error:
            failed_status = error.statuses["risk_manager"]
        else:
            raise RuntimeError("恢复演示未触发预期故障")
        result = runtime.run(resume=True).to_mapping()
    print("=== C3 Checkpoint 恢复验证 ===")
    print(f"首次故障节点: risk_manager，状态={failed_status}")
    print(
        "恢复后的实际调用次数: "
        f"C1={c1.calls}, Trader={trader.calls}, Risk Manager={risk.calls}"
    )
    print("结论: C1 和 Trader 未重复执行，只重试失败的 Risk Manager。")
    print("临时 Checkpoint 已自动清理，没有留下报告文件。\n")
    return result


def _write_outputs(result: dict, output_dir: str) -> tuple[Path, Path]:
    target = Path(output_dir)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target.mkdir(parents=True, exist_ok=True)
    report = result["report"]
    prefix = f"{report['symbol']}-{report['mode']}"
    report_path = target / f"{prefix}-financial-report.json"
    audit_path = target / f"{prefix}-audit-log.json"
    risk = report.get("risk_manager") or {}
    audit = {
        "symbol": report["symbol"],
        "mode": report["mode"],
        "graph": result["graph"],
        "c1_trace": report["research"].get("trace", []),
        "trader_trace": report["trader"].get("trace", []),
        "trader_harness_trace": report["trader"].get("harness_trace", []),
        "risk_manager_trace": risk.get("trace", []),
        "risk_manager_harness_trace": risk.get("harness_trace", []),
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path, audit_path


def _print_standardized_report(
    result: dict,
    report_path: Path | None,
    audit_path: Path | None,
) -> None:
    report = result["report"]
    graph = result["graph"]
    research = report["research"]["report"]
    combined = research["combined_analysis"]
    summary = combined["summary"]
    debate = research["debate"]
    synthesis = research["synthesis"]
    gate = research["market_regime_gate"]
    trader = report["trader"]["report"]
    decision = report["final_decision"]
    interval = synthesis["target_price_interval"]

    print("=== C3 最终标准化金融分析报告 ===")
    print("\n【1. 基本信息】")
    print(f"- 运行模式: {report['mode']}")
    print(f"- 分析标的: {report['symbol']}")
    print(f"- 报告状态: {report['status']}")

    print("\n【2. 四个专业 Agent 结论】")
    print(
        f"- 技术分析（Technical）: "
        f"{_bilingual(summary['technical']['signal_label'])}，"
        f"评分={summary['technical']['signal_score']}"
    )
    print(
        f"- 基本面分析（Fundamental）: "
        f"{_bilingual(summary['fundamental']['score_label'])}，"
        f"评分={summary['fundamental']['score']}"
    )
    print(
        f"- 行业分析（Industry）: "
        f"{_bilingual(summary['industry']['score_label'])}，"
        f"评分={summary['industry']['score']}，"
        f"景气度={_bilingual(summary['industry']['prosperity'])}"
    )
    print(
        f"- 大盘/宏观（Macro）: Regime="
        f"{_bilingual(summary['macro']['market_regime'])}，"
        f"风险偏好={_bilingual(summary['macro']['risk_appetite'])}，"
        f"评分={summary['macro']['score']}"
    )

    print("\n【3. 数据来源与时间】")
    for name in ("technical", "fundamental", "industry", "macro"):
        specialist = combined["reports"][name]
        print(
            f"- {name}: as_of={specialist['as_of']}，"
            f"timestamp={specialist['timestamp']}"
        )
        print("  sources=" + ", ".join(specialist["sources"]))

    print(f"\n【4. Bull/Bear 结构化辩论，共 {len(debate['rounds'])} 轮】")
    for debate_round in debate["rounds"]:
        number = debate_round["round"]
        for side, label in (("bull", "看多方"), ("bear", "看空方")):
            argument = debate_round[side]
            evidence = ", ".join(
                f"{item['specialist']}:{item['path']}"
                for item in argument["evidence"]
            )
            print(f"- 第{number}轮 {label}观点: {argument['claim']}")
            print(f"  证据: {evidence}")
            print(f"  推理: {argument['reasoning']}")

    print("\n【5. 综合研判（Synthesis）】")
    print(
        f"- 综合倾向: {_bilingual(synthesis['inclination'])} "
        f"（门控前={_bilingual(synthesis['raw_inclination'])}）"
    )
    print(f"- 四 Agent 加权评分: {synthesis['weighted_score']}")
    print(f"- 证据一致性置信度: {synthesis['confidence']}/100（不是盈利概率）")
    print(
        "- 研究价格区间: "
        f"{interval['lower']} <= {interval['reference']} <= {interval['upper']}"
    )
    print(f"- Consistency Check: {research['quality']['consistency_check']['status']}")
    print(f"- Bias Detector: {research['quality']['bias_detector']['status']}")
    print(
        f"- Market Regime: {_bilingual(gate['regime'])}，"
        f"风险偏好={_bilingual(gate['risk_appetite'])}，"
        f"研究仓位上限={gate['effective_position_cap_percent']}%"
    )

    print("\n【6. Trader 与条件路由】")
    print(
        f"- Trader 候选: {_bilingual(trader['signal']['action'])} "
        f"（{trader['signal']['label']}）"
    )
    print(f"- 候选评分: {trader['signal']['weighted_score']}")
    print(f"- 条件路由: {_bilingual(report['route']['selected_path'])}")
    print(f"- 路由原因: {report['route']['reason']}")
    print(
        f"- 止损/止盈来源: {report['route']['stop_loss_source']} / "
        f"{report['route']['take_profit_source']}"
    )

    print("\n【7. Risk Manager 与最终决策】")
    print(f"- 决策状态: {_bilingual(decision['status'])}")
    print(f"- 批准动作: {_bilingual(decision['approved_action'])}")
    print(f"- 决策来源: {_bilingual(report['decision_source'])}")
    if report["risk_manager"] is not None:
        risk = report["risk_manager"]["report"]
        position = risk["position"]
        prices = risk["price_controls"]
        execution = risk["execution"]
        print(f"- 决策原因: {risk['risk_decision']['reason']}")
        print(
            f"- 仓位: 当前={position['current_percent']}%，"
            f"请求={position['requested_percent']}%，"
            f"批准={position['approved_percent']}%"
        )
        print(
            f"- 预计单笔亏损: {position['estimated_single_trade_loss_percent']}%，"
            f"最终行业暴露={position['final_sector_exposure_percent']}%"
        )
        print(
            f"- 价格控制: 止损={prices['stop_loss_price']}，"
            f"参考={prices['reference_price']}，止盈={prices['take_profit_price']}，"
            f"收益风险比={prices['reward_risk_ratio']}"
        )
        print("- 十项风控检查:")
        for check in risk["risk_checks"]:
            label = RISK_CHECK_LABELS.get(check["name"], check["name"])
            print(
                f"  - {label}（{check['name']}）: "
                f"{_bilingual(check['status'])} ({check['detail']})"
            )
        print(
            "- 模拟执行许可: "
            f"{str(execution['simulation_execution_allowed']).lower()}"
        )
    else:
        print(f"- 决策原因: {decision['reason']}")
        print(f"- 保持仓位: {decision['approved_position_percent']}%")

    print("\n【8. Graph 执行与审计】")
    print("- 顶层执行顺序: " + " -> ".join(graph["execution_order"]))
    print(
        "- Specialist 执行顺序: "
        + " -> ".join(report["research"]["specialist_graph"]["execution_order"])
    )
    for name, status in graph["statuses"].items():
        print(f"- {name}: {_bilingual(status)}，attempts={graph['attempts'][name]}")

    print("\n【9. 执行安全边界】")
    print(f"- simulation_only={str(report['simulation_only']).lower()}")
    print(f"- order_created={str(report['order_created']).lower()}")
    print(f"- real_trading_allowed={str(report['real_trading_allowed']).lower()}")

    print("\n【10. 输出文件】")
    if report_path is None or audit_path is None:
        print("- 本次未生成文件；完整报告已显示在当前终端。")
        print("- 以后如需保存，可显式添加 --output-dir 输出目录。")
    else:
        print(f"- 完整结构化报告: {report_path}")
        print(f"- Graph/Harness 审计日志: {audit_path}")
    print(
        "\n当前阶段结论: C3 已完成。此入口展示单股票完整 Graph；"
        "Checkpoint 恢复可用 --verify-recovery 验证，20只股票验收使用 "
        "demo_financial_batch.py。"
    )
    if decision["status"] == "pending_human_confirmation":
        print("提示: 添加 --confirm 可模拟完成本次人工确认。")


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    query = FinancialGraphQuery(
        c1_query=C1DecisionQuery(
            combined_query=CombinedAnalysisQuery.for_symbol(
                symbol=arguments.symbol,
                sector=arguments.sector,
                index_symbol=arguments.index_symbol,
                mode="live" if arguments.live else "offline",
                start_date=arguments.start_date,
                end_date=arguments.end_date,
            ),
            debate_rounds=arguments.rounds,
            base_position_cap_percent=arguments.base_position_cap,
        ),
        risk_context=RiskContext(
            account_equity=arguments.account_equity,
            current_position_percent=arguments.current_position,
            requested_position_percent=arguments.requested_position,
            sector_exposure_other_percent=arguments.sector_exposure_other,
            current_drawdown_percent=arguments.drawdown,
            average_daily_turnover=arguments.average_daily_turnover,
            evaluation_time=arguments.evaluation_time,
            stop_loss_price=arguments.stop_loss,
            take_profit_price=arguments.take_profit,
            human_confirmed=arguments.confirm,
        ),
    )
    policy = FinancialDataPolicy(
        timeout_seconds=arguments.timeout,
        max_attempts=arguments.attempts,
    )
    if arguments.verify_recovery:
        result = _run_recovery_demo(query, policy)
    else:
        result = build_default_financial_graph_runtime(
            project_root=PROJECT_ROOT,
            policy=policy,
        ).run(query).to_mapping()
    if arguments.output_dir:
        report_path, audit_path = _write_outputs(result, arguments.output_dir)
    else:
        report_path, audit_path = None, None
    _print_standardized_report(result, report_path, audit_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
