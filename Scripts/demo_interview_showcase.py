"""面试展示版：一次演示数据可信度、故障恢复和报告差异解释。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.reliability_experiment import (  # noqa: E402
    OfflineReliabilityExperimentRuntime,
)


def main() -> int:
    result = OfflineReliabilityExperimentRuntime().run()
    metrics = result["metrics"]
    print("=== 面试展示版离线可靠性演示 ===")
    print("模式: offline")
    print("网络访问: 未使用")
    print("文件输出: 未生成")
    print(f"固定数据集: {result['task_config']['dataset']}")
    print("\n五类场景:")
    for item in result["scenarios"]:
        print(
            f"- {item['title']}: {item['outcome']} | "
            f"耗时={item['duration_ms']}ms | 重试={item['retry_count']} | "
            f"降级={item['degradation_count']} | "
            f"重复成功节点={item['duplicate_successful_nodes']}"
        )
        print(f"  说明: {item['detail']}")
    print("\n统计结果:")
    print(f"- 成功率: {metrics['success_rate_percent']}%")
    print(
        f"- 故障恢复成功率: {metrics['fault_recovery_rate_percent']}% "
        f"({metrics['fault_recovery_success_count']}/{metrics['fault_recovery_cases']})"
    )
    print(f"- 重试次数: {metrics['retry_count']}")
    print(f"- 降级次数: {metrics['degradation_count']}")
    print(f"- 缓存命中率: {metrics['cache_hit_rate_percent']}%")
    print(f"- 重复执行成功节点数: {metrics['duplicate_successful_node_count']}")
    print(f"- 模型调用次数: {metrics['model_call_count']}")
    print(f"- Token 总数: {metrics['total_tokens']}")
    print(
        f"- 耗时分位数: P50={metrics['p50_latency_ms']}ms / "
        f"P95={metrics['p95_latency_ms']}ms / P99={metrics['p99_latency_ms']}ms"
    )
    print("\n两份报告为什么不同:")
    for item in result["comparison"]["change_reasons"]:
        print(f"- {item['label']}: {item['detail']}")
    print(f"\n结论: {result['conclusion']}")
    print("\nJSON 摘要:")
    print(json.dumps({"metrics": metrics, "change_reasons": result["comparison"]["change_reasons"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
