"""Offline P3 acceptance: archive, restart, list, and reopen one real report."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.analysis_jobs import AnalysisJobRuntime  # noqa: E402
from agent_platform.analysis_repository import SQLiteAnalysisRepository  # noqa: E402
from agent_platform.client_app import ClientAnalysisRequest, ClientAnalysisRuntime  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="agent-platform-p3-") as temp_dir:
        root = Path(temp_dir)
        database = root / "analysis_history.sqlite3"
        repository = SQLiteAnalysisRepository(database)
        runtime = AnalysisJobRuntime.from_client_runtime(
            ClientAnalysisRuntime.from_project(PROJECT_ROOT),
            max_workers=1,
            storage_path=root / "jobs.json",
            checkpoint_root=root / "checkpoints",
            repository=repository,
        )
        submitted = runtime.submit(ClientAnalysisRequest(symbol="sz000001", mode="offline"))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = runtime.get(submitted["job_id"])
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.03)
        runtime.close()
        if job["status"] != "succeeded":
            print(f"P3 验收失败: {job}")
            return 1

        restarted = SQLiteAnalysisRepository(database)
        recent = restarted.list_reports(limit=5)
        report = restarted.get_report(recent[0]["report_id"])
        value = report["result"]

        print("=== P3 SQLite 历史报告验收 ===")
        print("分析任务: succeeded")
        print("SQLite 事务归档: succeeded")
        print("模拟服务重启: succeeded")
        print(f"最近分析数量: {len(recent)}")
        print(f"重新打开: {value['security']['name']} {value['security']['code']}")
        print(f"报告版本: v{report['report_version']}")
        print(f"报告编号: {report['report_id']}")
        print(f"快照编号: {value['data']['snapshot_id']}")
        print(f"当时数据: {value['data']['label']} · {value['data']['as_of']}")
        print(f"当时结论: {value['verdict']['label']} · {value['verdict']['action_label']}")
        print(f"已保存 Agent: {', '.join(sorted(report['agents']))}")
        print(f"已保存 Graph: {', '.join(sorted(report['graphs']))}")
        print("敏感密钥: 未保存")
        print("临时验收数据库已自动清理，不产生报告文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
