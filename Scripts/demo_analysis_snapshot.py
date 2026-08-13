"""P2 unified snapshot acceptance demo without network or output files."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.finance import (  # noqa: E402
    AnalysisSnapshotError,
    AnalysisSnapshotRuntime,
    CombinedAnalysisQuery,
    FinancialDataError,
    FinancialDataErrorCode,
    JsonSnapshotFallbackCache,
)


class DemoSource:
    def __init__(self, name: str, *, fail: tuple[str, ...] = ()) -> None:
        self.name = name
        self.fail = set(fail)
        self.calls: list[str] = []

    def fetch(self, dataset, params, *, mode):
        del mode
        self.calls.append(json.dumps({"dataset": dataset, "params": params}, sort_keys=True, ensure_ascii=False))
        if dataset in self.fail:
            raise FinancialDataError(
                f"{self.name} unavailable",
                code=FinancialDataErrorCode.PROVIDER_UNAVAILABLE,
                source=self.name,
            )
        return {
            "dataset": dataset,
            "record_count": 1,
            "source": self.name,
            "timestamp": "2026-08-13T12:00:00+08:00",
            "attempts": 1,
            "cache_hit": False,
            "mode": "live",
            "records": [{
                "subject": params.get("symbol", "CN"),
                "fields": {"availability": "available"},
                "source": self.name,
                "timestamp": "2026-08-13T12:00:00+08:00",
                "as_of": "2026-08-13T11:00:00+08:00",
            }],
            "trace": [],
        }


def main() -> int:
    query = CombinedAnalysisQuery.for_symbol(
        symbol="sz000001", sector="金融行业", mode="live", end_date="20260813"
    )
    now = lambda: datetime(2026, 8, 13, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = JsonSnapshotFallbackCache(Path(temp_dir) / "fallback.json")
        primary = DemoSource("primary")
        normal = AnalysisSnapshotRuntime(primary=primary, fallback_cache=cache, now=now).acquire(query)
        backup = AnalysisSnapshotRuntime(
            primary=DemoSource("primary", fail=("market.daily",)),
            backup=DemoSource("tushare", fail=()),
            fallback_cache=cache,
            now=now,
        ).acquire(query)
        stale = AnalysisSnapshotRuntime(
            primary=DemoSource("primary", fail=("market.daily",)),
            backup=DemoSource("tushare", fail=("market.daily",)),
            fallback_cache=cache,
            now=now,
        ).acquire(query)
        partial = AnalysisSnapshotRuntime(
            primary=DemoSource("primary", fail=("market.fund_flow",)), now=now
        ).acquire(query)
        try:
            AnalysisSnapshotRuntime(
                primary=DemoSource("primary", fail=("market.daily",)), now=now
            ).acquire(query)
        except AnalysisSnapshotError as error:
            blocked = str(error)
        else:
            raise AssertionError("required dataset failure must reject snapshot")

    daily_backup = next(item for item in backup.datasets if item.request.dataset == "market.daily")
    daily_stale = next(item for item in stale.datasets if item.request.dataset == "market.daily")
    unavailable = next(item for item in partial.datasets if item.request.dataset == "market.fund_flow")
    print("=== P2 统一分析快照验收 ===")
    print(f"snapshot_id: {normal.snapshot_id}")
    print(f"统一数据时点: {normal.to_mapping(include_records=False)['as_of']}")
    print(f"去重结果: {len(primary.calls)} 次请求 = {len(set(primary.calls))} 个唯一数据请求")
    print(f"主源成功: {normal.available_count}/{len(normal.datasets)} 类可用")
    print(f"备用源接管: market.daily -> {daily_backup.status} ({daily_backup.source})")
    print(f"缓存降级: market.daily -> {daily_stale.status} ({daily_stale.freshness})")
    print(f"部分结果: market.fund_flow -> {unavailable.status}，其余数据继续可用")
    print(f"关键数据阻断: {blocked}")
    print("文件输出: 无；真实交易: 关闭")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
