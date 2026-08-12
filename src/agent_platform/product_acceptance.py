"""One command-line acceptance interface for core delivery and both Web surfaces."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .client_app import ClientAnalysisRequest, ClientAnalysisRuntime, SECURITIES
from .dashboard import ACTIONS
from .final_delivery import FinalDeliveryRuntime


class FinalDeliveryPort(Protocol):
    def run(self) -> Any:
        """Return the final A-D delivery report."""


class ClientAnalysisPort(Protocol):
    def analyze(self, request: ClientAnalysisRequest) -> Any:
        """Return one customer-facing analysis report."""


@dataclass(frozen=True)
class ProductAcceptanceReport:
    core_delivery: dict[str, Any]
    client_app: dict[str, Any]
    admin_console: dict[str, Any]
    model_assistance: dict[str, Any]
    safety: dict[str, Any]

    @property
    def passed(self) -> bool:
        return (
            self.core_delivery["passed"]
            and self.client_app["passed"]
            and self.admin_console["passed"]
            and self.safety["passed"]
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": "product_acceptance_passed" if self.passed else "failed",
            "core_delivery": dict(self.core_delivery),
            "client_app": dict(self.client_app),
            "admin_console": dict(self.admin_console),
            "model_assistance": dict(self.model_assistance),
            "safety": dict(self.safety),
            "passed": self.passed,
        }


class ProductAcceptanceRuntime:
    """Deep acceptance module for A-D, customer UI, admin UI, and safety."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        final_delivery: FinalDeliveryPort,
        client_analysis: ClientAnalysisPort,
    ) -> None:
        self._root = Path(project_root).resolve()
        self._final_delivery = final_delivery
        self._client_analysis = client_analysis

    @classmethod
    def from_project(
        cls,
        project_root: str | Path | None = None,
    ) -> "ProductAcceptanceRuntime":
        root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        return cls(
            project_root=root,
            final_delivery=FinalDeliveryRuntime.from_project(root),
            client_analysis=ClientAnalysisRuntime.from_project(root),
        )

    def run(self) -> ProductAcceptanceReport:
        final = self._final_delivery.run()
        final_mapping = final.to_mapping()
        client = self._client_analysis.analyze(ClientAnalysisRequest()).to_mapping()
        client_assets = self._check_assets(
            "client.html",
            "client.css",
            "client.js",
        )
        admin_assets = self._check_assets("index.html", "styles.css", "app.js")
        client_checks = {
            "页面资源齐全": all(client_assets.values()),
            "客户股票池不少于20只": len(SECURITIES) >= 20,
            "客户股票池覆盖沪深两市": {item["exchange"] for item in SECURITIES.values()}
            == {"上交所", "深交所"},
            "四个研究维度齐全": len(client.get("dimensions", [])) == 4,
            "K线可视数据不少于30根": len(client.get("data", {}).get("bars", [])) >= 30,
            "综合观点可读": bool(client.get("verdict", {}).get("label")),
            "风险区间可读": all(
                client.get("price_band", {}).get(key)
                for key in ("lower", "reference", "upper")
            ),
        }
        admin_checks = {
            "后台页面资源齐全": all(admin_assets.values()),
            "A-D四阶段均有入口": {action.stage for action in ACTIONS}
            == {"A", "B", "C", "D"},
            "后台登记功能不少于18项": len(ACTIONS) >= 18,
        }
        safety_checks = {
            "客户报告仅用于模拟研究": client["safety"]["simulation_only"] is True,
            "客户报告没有创建订单": client["safety"]["order_created"] is False,
            "真实交易保持关闭": client["safety"]["real_trading_allowed"] is False,
            "核心交付真实交易关闭": final_mapping["safety"]["real_trading_allowed"] is False,
        }
        return ProductAcceptanceReport(
            core_delivery={
                "passed": final.passed,
                "workflow_count": len(final_mapping["workflows"]),
                "status": final_mapping["status"],
            },
            client_app={
                "passed": all(client_checks.values()),
                "checks": client_checks,
                "symbol": client["security"]["symbol"],
                "verdict": client["verdict"],
                "bar_count": len(client["data"]["bars"]),
                "source_count": client["data"]["source_count"],
            },
            admin_console={
                "passed": all(admin_checks.values()),
                "checks": admin_checks,
                "action_count": len(ACTIONS),
                "path": "/admin",
            },
            model_assistance={
                "configured": bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()),
                "provider": "deepseek" if os.environ.get("DEEPSEEK_API_KEY", "").strip() else "local_fallback",
                "required_for_offline_acceptance": False,
            },
            safety={
                "passed": all(safety_checks.values()),
                "checks": safety_checks,
            },
        )

    def _check_assets(self, *names: str) -> dict[str, bool]:
        web_root = self._root / "src" / "agent_platform" / "web"
        return {name: (web_root / name).is_file() for name in names}


def print_product_acceptance(report: ProductAcceptanceReport) -> None:
    value = report.to_mapping()
    print("=== 通用 Agent 平台整体验收 ===")
    print("验收范围: A-D 核心链路 + 客户分析前台 + 团队验收后台 + 安全边界")

    core = value["core_delivery"]
    print("\n【1. A-D 核心交付】")
    print(
        f"- {'通过' if core['passed'] else '失败'}: "
        f"status={core['status']}，主要流程={core['workflow_count']}"
    )

    client = value["client_app"]
    print("\n【2. 客户分析前台 /】")
    for name, passed in client["checks"].items():
        print(f"- {'通过' if passed else '失败'}: {name}")
    print(
        f"- 示例结果: {client['symbol']}，观点={client['verdict']['label']}，"
        f"K线={client['bar_count']}根，来源={client['source_count']}类"
    )

    admin = value["admin_console"]
    print("\n【3. 团队验收后台 /admin】")
    for name, passed in admin["checks"].items():
        print(f"- {'通过' if passed else '失败'}: {name}")
    print(f"- 已登记可操作功能: {admin['action_count']} 项")

    model = value["model_assistance"]
    print("\n【4. 智能解读】")
    print(f"- 当前解释层: {model['provider']}")
    print("- DeepSeek 未配置时使用本地安全解释，不影响离线验收。")

    print("\n【5. 安全边界】")
    for name, passed in value["safety"]["checks"].items():
        print(f"- {'通过' if passed else '失败'}: {name}")

    print("\n【最终结论】")
    print("整体验收通过。" if report.passed else "整体验收失败，请查看失败项。")
    print("客户前台只展示研究成果；后台保留工程证据；系统不执行真实交易。")


__all__ = [
    "ClientAnalysisPort",
    "FinalDeliveryPort",
    "ProductAcceptanceReport",
    "ProductAcceptanceRuntime",
    "print_product_acceptance",
]
