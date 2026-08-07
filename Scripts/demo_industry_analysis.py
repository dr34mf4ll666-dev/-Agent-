"""Run the B2 industry specialist through Data Hub, Loop, and Harness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from agent_platform.finance import (  # noqa: E402
    FinancialDataPolicy,
    IndustryAnalysisQuery,
    build_default_industry_analysis_runtime,
)


SCORE_LABELS = {
    "strong_positive": "强正面（strong_positive）",
    "positive": "正面（positive）",
    "neutral": "中性（neutral）",
    "negative": "负面（negative）",
    "strong_negative": "强负面（strong_negative）",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B2 行业 Agent 演示")
    parser.add_argument("--live", action="store_true", help="显式请求真实行业和政策数据")
    parser.add_argument("--sector", default="玻璃行业")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--start-date", default="20260101")
    parser.add_argument("--end-date", default="20260807")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    runtime = build_default_industry_analysis_runtime(
        project_root=PROJECT_ROOT,
        policy=FinancialDataPolicy(
            timeout_seconds=arguments.timeout,
            max_attempts=arguments.attempts,
        ),
    )
    result = runtime.run(
        IndustryAnalysisQuery(
            sector=arguments.sector,
            mode="live" if arguments.live else "offline",
            limit=arguments.limit,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
    ).to_mapping()
    report = result["report"]
    analysis = report["analysis"]
    profile = analysis["industry_profile"]
    policy = analysis["policy"]

    print("=== B2 行业 Agent 演示 ===")
    print(f"运行模式（mode）: {report['query']['mode']}")
    print(f"目标行业（sector）: {analysis['sector']}")
    print(f"数据来源（sources）: {', '.join(analysis['sources'])}")
    print(
        "行业画像（industry profile）: "
        f"公司数={profile['company_count']}, 平均价格={profile['average_price']}, "
        f"板块涨跌幅={profile['change_percent']}%, "
        f"代表股={profile['representative_stock_name']}({profile['representative_stock_code']})"
    )
    print(
        "景气度（prosperity）: "
        f"{analysis['prosperity']['label']}，规则={analysis['prosperity']['rule']}"
    )
    print(
        "政策环境（policy）: "
        f"1Y LPR={policy['lpr_1y']}%, 5Y LPR={policy['lpr_5y']}%, "
        f"变化={policy['change_1y']}%，信号={policy['signal']}"
    )
    print(
        "竞争格局（competition）: "
        f"样本行业数={analysis['competition']['sector_count']}, "
        f"样本中公司数最多={analysis['competition']['largest_sector_by_company_count']} "
        f"({analysis['competition']['largest_sector_company_count']} 家)"
    )
    print("产业链（industry chain）:")
    chain = analysis["industry_chain"]
    print(f"- 上游（upstream）: {'、'.join(chain['upstream'])}")
    print(f"- 中游（midstream）: {'、'.join(chain['midstream'])}")
    print(f"- 下游（downstream）: {'、'.join(chain['downstream'])}")
    print("龙头排序（leaders）:")
    for leader in analysis["leaders"]:
        print(
            f"- {leader['rank']}. {leader['sector']} / "
            f"{leader['stock_name']}({leader['stock_code']})，代表股涨跌幅="
            f"{leader['representative_change_percent']}%"
        )
    print(
        f"综合行业评分（score）: {analysis['score']}，"
        f"{SCORE_LABELS.get(analysis['score_label'], analysis['score_label'])}"
    )
    print("评分组成（score components）:")
    for component in analysis["score_components"]:
        print(f"- {component['name']}: {component['points']:+d} ({component['rule']})")
    print("循环追踪（Loop trace）:")
    for event in result["loop"]["trace"]:
        print(f"- {event['event']}")
    print("Harness 追踪（Harness trace）:")
    for event in result["loop"]["harness_trace"]:
        owner = f" [{event['agent']}]" if event["agent"] else ""
        detail = f" ({event['detail']})" if event["detail"] else ""
        print(f"- {event['event']}{owner}{detail}")
    print("说明: 产业链为项目规则模板，龙头是数据源提供的代表股排序；结果不构成投资建议。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
