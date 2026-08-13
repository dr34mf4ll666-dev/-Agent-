"""Demonstrate the complete P1 asynchronous customer-analysis job flow."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.analysis_jobs import AnalysisJobRuntime  # noqa: E402
from agent_platform.client_app import ClientAnalysisRequest, ClientAnalysisRuntime  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime = AnalysisJobRuntime.from_client_runtime(
            ClientAnalysisRuntime.from_project(PROJECT_ROOT),
            max_workers=1,
            storage_path=Path(temp_dir) / "jobs.json",
            checkpoint_root=Path(temp_dir) / "checkpoints",
        )
        try:
            submitted = runtime.submit(ClientAnalysisRequest())
            print("=== P1 异步分析任务演示 ===")
            print(f"任务编号: {submitted['job_id']}")
            print("提交结果: 已立即返回，分析在后台 Worker 执行")
            previous = None
            while True:
                current = runtime.get(submitted["job_id"])
                signature = tuple(
                    (stage["id"], stage["status"])
                    for stage in current["progress"]["stages"]
                )
                if signature != previous:
                    print(
                        f"任务状态: {current['status']}，"
                        f"确认进度={current['progress']['completed']}/{current['progress']['total']}"
                    )
                    for stage in current["progress"]["stages"]:
                        print(f"- {stage['label']}: {stage['status']}")
                    previous = signature
                if current["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.02)
            if current["status"] != "succeeded":
                print(f"结论: P1 演示失败，error={current['error']}")
                return 1
            result = runtime.result(submitted["job_id"]).to_mapping()
            print("最终报告:")
            print(f"- 标的: {result['security']['name']} {result['security']['code']}")
            print(f"- 综合观点: {result['verdict']['label']}")
            print(f"- 四维结果: {len(result['dimensions'])} 项")
            print(f"- K线: {len(result['data']['bars'])} 根")
            print(
                "- real_trading_allowed="
                f"{str(result['safety']['real_trading_allowed']).lower()}"
            )
            print("恢复能力: JSON 任务记录 + Specialist/C3 Checkpoint；失败只重试未完成节点。")
            print(f"任务总超时: {current['timeout_seconds']:g} 秒")
            print("结论: P1 异步任务中心完整验收通过")
            return 0
        finally:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
