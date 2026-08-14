"""Read-only customer report projections for basic and professional views."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol


class ReportViewError(ValueError):
    """A frozen report cannot be projected into the requested customer view."""


class ReportArchiveReader(Protocol):
    def get_report(self, report_id: str) -> dict[str, Any]:
        """Return one complete frozen report archive."""


class ReportViewRuntime:
    """Deep read-only module: one frozen report becomes one customer projection."""

    VALID_VIEWS = frozenset({"basic", "professional"})

    def __init__(self, repository: ReportArchiveReader) -> None:
        self._repository = repository

    def project(self, report_id: str, view: str = "basic") -> dict[str, Any]:
        normalized_view = str(view).strip().lower()
        if normalized_view not in self.VALID_VIEWS:
            raise ReportViewError("view 只允许 basic 或 professional。")
        try:
            archive = self._repository.get_report(report_id)
            result = archive["result"]
            agents = archive["agents"]
            task = archive["task"]
        except (KeyError, TypeError) as error:
            raise ReportViewError(f"冻结报告缺少展示数据: {error}") from error
        if not isinstance(result, Mapping) or not isinstance(agents, Mapping):
            raise ReportViewError("冻结报告的数据结构无效。")

        shared = self._shared_projection(archive, result)
        output: dict[str, Any] = {
            "view": normalized_view,
            "report_id": str(archive["report_id"]),
            "report_version": int(archive["report_version"]),
            "projection_fingerprint": _fingerprint(shared),
            "shared": shared,
            "basic": self._basic_projection(result, task),
        }
        if normalized_view == "professional":
            output["professional"] = self._professional_projection(
                result=result,
                agents=agents,
                task=task,
                snapshot=archive.get("snapshot"),
            )
        return output

    @staticmethod
    def _shared_projection(
        archive: Mapping[str, Any], result: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            data = result["data"]
            return {
                "security": deepcopy(result["security"]),
                "data": {
                    "mode": data["mode"],
                    "label": data["label"],
                    "as_of": data["as_of"],
                    "timestamp": data["timestamp"],
                    "source_count": data["source_count"],
                    "snapshot_id": data.get("snapshot_id")
                    or (data.get("snapshot") or {}).get("snapshot_id"),
                    "health": _snapshot_health_projection(
                        archive.get("snapshot") or data.get("snapshot")
                    ),
                },
                "quote": deepcopy(result["quote"]),
                "verdict": deepcopy(result["verdict"]),
                "price_band": deepcopy(result["price_band"]),
                "risk": deepcopy(result["risk"]),
                "safety": deepcopy(result["safety"]),
                "chart": _chart_projection(data["bars"]),
                "archived_at": archive.get("archived_at"),
            }
        except (KeyError, TypeError) as error:
            raise ReportViewError(f"冻结报告缺少共享展示字段: {error}") from error

    @staticmethod
    def _basic_projection(
        result: Mapping[str, Any], task: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            dimensions = list(result["dimensions"])
            strongest = max(dimensions, key=lambda item: Decimal(str(item["score"])))
            weakest = min(dimensions, key=lambda item: Decimal(str(item["score"])))
            debate = result["debate"]
            verdict = result["verdict"]
            price_band = result["price_band"]
            risk_view = result["risk"]
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise ReportViewError(f"冻结报告缺少普通版字段: {error}") from error
        headline = _beginner_headline(verdict)
        support_detail = _beginner_dimension_explanation(strongest, is_risk=False)
        risk_detail = _beginner_dimension_explanation(weakest, is_risk=True)
        price_explanation = (
            f"参考价 {price_band['reference']}。价格接近 {price_band['lower']} 时风险增大；"
            f"{price_band['upper']} 为本次研究区间上沿，不代表后续仍会上涨。"
        )
        return {
            "headline": headline,
            "summary": (
                "综合近期价格、公司经营、所属行业和整体市场环境形成。"
                "结论仅供研究参考，不构成交易指令。"
            ),
            "support": {
                "title": strongest["name"],
                "score": strongest["score"],
                "summary": support_detail,
                "reasoning": debate["positive_reasoning"],
            },
            "risk": {
                "title": weakest["name"],
                "score": weakest["score"],
                "summary": risk_detail,
                "reasoning": debate["risk_reasoning"],
            },
            "guide": [
                {
                    "id": "outlook",
                    "label": "结论摘要",
                    "answer": headline,
                    "detail": _beginner_outlook_detail(verdict),
                },
                {
                    "id": "support",
                    "label": "主要依据",
                    "answer": f"{_plain_dimension_name(strongest)}提供主要支撑",
                    "detail": support_detail,
                },
                {
                    "id": "risk",
                    "label": "主要风险",
                    "answer": f"{_plain_dimension_name(weakest)}是相对薄弱项",
                    "detail": risk_detail,
                },
                {
                    "id": "watch",
                    "label": "关注区间",
                    "answer": f"{price_band['lower']}–{price_band['upper']}",
                    "detail": price_explanation,
                },
            ],
            "price_explanation": price_explanation,
            "risk_explanation": _beginner_risk_explanation(risk_view),
            "stages": _plain_stages(task.get("stages", [])),
            "notice": result["safety"]["notice"],
        }

    @staticmethod
    def _professional_projection(
        *,
        result: Mapping[str, Any],
        agents: Mapping[str, Any],
        task: Mapping[str, Any],
        snapshot: Any,
    ) -> dict[str, Any]:
        dimensions = {
            str(item["id"]): item for item in result.get("dimensions", [])
        }
        details = []
        evidence = []
        for agent_id, title in (
            ("technical", "技术走势"),
            ("fundamental", "经营质量"),
            ("industry", "行业温度"),
            ("macro", "市场环境"),
        ):
            payload = agents.get(agent_id)
            dimension = dimensions.get(agent_id)
            if not isinstance(payload, Mapping) or not isinstance(dimension, Mapping):
                raise ReportViewError(f"冻结报告缺少 {agent_id} 专业证据。")
            sources = [str(item) for item in payload.get("sources", [])]
            details.append(
                {
                    "id": agent_id,
                    "name": title,
                    "score": dimension["score"],
                    "label": dimension["label"],
                    "summary": dimension["summary"],
                    "as_of": payload.get("as_of"),
                    "timestamp": payload.get("timestamp"),
                    "sources": sources,
                    "caveats": [str(item) for item in payload.get("caveats", [])],
                    "metrics": _agent_metrics(agent_id, payload),
                }
            )
            for index, source in enumerate(sources, start=1):
                evidence.append(
                    {
                        "id": f"{agent_id}-{index}",
                        "agent_id": agent_id,
                        "agent_name": title,
                        "source": source,
                        "as_of": payload.get("as_of"),
                        "timestamp": payload.get("timestamp"),
                    }
                )
        return {
            "dimensions": deepcopy(result["dimensions"]),
            "debate": deepcopy(result["debate"]),
            "quality": deepcopy(result["quality"]),
            "snapshot": _snapshot_projection(snapshot),
            "sources": deepcopy(result["data"]["sources"]),
            "task_nodes": deepcopy(task.get("stages", [])),
            "agent_details": details,
            "evidence_index": evidence,
            "score_weights": [
                {"name": "技术走势", "weight_percent": 25},
                {"name": "经营质量", "weight_percent": 30},
                {"name": "行业温度", "weight_percent": 20},
                {"name": "市场环境", "weight_percent": 25},
            ],
            "calculation_note": "指标、评分、价格区间、仓位和风控均来自冻结的确定性计算。",
        }


def _plain_stages(value: Any) -> list[dict[str, Any]]:
    stages = list(value) if isinstance(value, list) else []
    groups = (
        ("prepare", "准备数据", {"c1_research", "planner"}),
        (
            "research",
            "多维研究",
            {
                "technical", "fundamental", "industry", "macro", "aggregate",
                "c1_debate", "c1_quality", "c1_synthesis",
            },
        ),
        (
            "risk",
            "风险检查",
            {"trader", "market_route", "risk_manager", "market_bearish_skip", "finalize"},
        ),
        ("report", "生成报告", {"chart", "report"}),
    )
    return [
        {
            "id": group_id,
            "label": label,
            "status": _combined_status(
                [item.get("status", "pending") for item in stages if item.get("id") in ids]
            ),
        }
        for group_id, label, ids in groups
    ]


def _beginner_headline(verdict: Mapping[str, Any]) -> str:
    action = str(verdict.get("action", "")).lower()
    if action == "buy" or verdict.get("action_label") == "偏多关注":
        return "谨慎关注"
    if action == "sell" or verdict.get("action_label") == "偏空回避":
        return "偏弱，注意风险"
    if action == "reduce" or verdict.get("action_label") == "降低风险暴露":
        return "风险偏高"
    return "中性观察"


def _beginner_outlook_detail(verdict: Mapping[str, Any]) -> str:
    action = str(verdict.get("action", "")).lower()
    if action == "buy" or verdict.get("action_label") == "偏多关注":
        return "综合信号略偏积极，风险因素仍需同时考虑。"
    if action == "sell" or verdict.get("action_label") == "偏空回避":
        return "综合信号偏弱，当前以下行风险为主要关注点。"
    if action == "reduce" or verdict.get("action_label") == "降低风险暴露":
        return "现有风险超过常规观察范围，风险控制优先。"
    return "四类信息尚未形成一致方向，当前以观察为主。"


def _plain_dimension_name(dimension: Mapping[str, Any]) -> str:
    return {
        "technical": "近期价格走势",
        "fundamental": "公司的经营和估值",
        "industry": "所属行业环境",
        "macro": "整体市场环境",
    }.get(str(dimension.get("id", "")), str(dimension.get("name", "这个方面")))


def _beginner_dimension_explanation(
    dimension: Mapping[str, Any], *, is_risk: bool
) -> str:
    name = _plain_dimension_name(dimension)
    score = Decimal(str(dimension.get("score", 0)))
    if is_risk:
        if score < Decimal("-15"):
            return f"{name}偏弱，是当前报告中的主要风险。"
        if score < Decimal("15"):
            return f"{name}方向不明，是四类信息中的相对薄弱项。"
        return f"{name}表现并不弱，但相较其他方面支撑有限。"
    if score > Decimal("15"):
        return f"{name}表现相对较好，是当前结论的主要依据。"
    return f"{name}未见明显转弱，在四类信息中相对稳定。"


def _beginner_risk_explanation(risk: Mapping[str, Any]) -> str:
    status = str(risk.get("status", ""))
    if "阻断" in status:
        return "风险检查未通过模拟方案。当前结果仅用于观察价格与风险变化。"
    if "人工" in status:
        return "风险检查要求人工复核，系统不会自动执行交易。"
    if "降低" in status:
        return "风险检查显示当前风险偏高，系统不会自动执行交易。"
    if "通过" in status:
        return "风险检查未发现需要直接阻断的问题。该结果不代表未来收益。"
    return "结果仅供研究参考。系统不连接券商，也不会自动创建订单。"


def _combined_status(statuses: list[str]) -> str:
    if not statuses:
        return "pending"
    for status in ("failed", "cancelled", "running", "retrying"):
        if status in statuses:
            return status
    if all(status in {"completed", "skipped"} for status in statuses):
        return "completed"
    return "pending"


def _chart_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, list) or not value:
        raise ReportViewError("冻结报告没有可展示的 K 线。")
    bars = deepcopy(value)
    weekly = _aggregate_weekly(bars)
    return {
        "series": {
            "daily": _chart_series(bars),
            "weekly": _chart_series(weekly),
        },
        "periods": [
            {"id": "daily", "label": "日 K"},
            {"id": "weekly", "label": "周 K"},
        ],
        "default_period": "daily",
        "ranges": [20, 40, 60],
        "default_range": min(40, len(bars)),
    }


def _chart_series(bars: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [Decimal(str(item["close"])) for item in bars]
    return {
        "bars": bars,
        "indicators": {
            "sma5": _moving_average(closes, 5),
            "sma20": _moving_average(closes, 20),
        },
    }


def _aggregate_weekly(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    current_key: tuple[int, int] | None = None
    for bar in bars:
        try:
            parsed = date.fromisoformat(str(bar["date"])[:10])
        except (KeyError, ValueError) as error:
            raise ReportViewError(f"K 线日期无效: {error}") from error
        iso = parsed.isocalendar()
        key = (iso.year, iso.week)
        if key != current_key:
            groups.append([])
            current_key = key
        groups[-1].append(bar)
    output = []
    for group in groups:
        output.append(
            {
                "date": group[-1]["date"],
                "open": group[0]["open"],
                "high": format(max(Decimal(str(item["high"])) for item in group), "f"),
                "low": format(min(Decimal(str(item["low"])) for item in group), "f"),
                "close": group[-1]["close"],
                "volume": format(sum(Decimal(str(item["volume"])) for item in group), "f"),
            }
        )
    return output


def _moving_average(values: list[Decimal], window: int) -> list[str | None]:
    output: list[str | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            output.append(None)
            continue
        average = sum(values[index + 1 - window:index + 1]) / Decimal(window)
        output.append(format(average.quantize(Decimal("0.0001")), "f"))
    return output


def _agent_metrics(agent_id: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    paths: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
        "technical": (
            ("SMA5", ("ma", "sma_5")), ("SMA10", ("ma", "sma_10")),
            ("SMA20", ("ma", "sma_20")), ("MACD DIF", ("macd", "dif")),
            ("MACD DEA", ("macd", "dea")), ("RSI14", ("rsi", "rsi_14")),
            ("KDJ K", ("kdj", "k")), ("布林带上轨", ("bollinger", "upper")),
            ("20 日支撑", ("levels", "support_20")),
            ("20 日压力", ("levels", "resistance_20")),
        ),
        "fundamental": (
            ("动态 PE", ("valuation", "pe_dynamic")), ("PB", ("valuation", "pb")),
            ("PS", ("valuation", "ps")), ("加权 ROE", ("indicators", "weighted_roe_percent")),
            ("净利润增长", ("growth", "net_profit_growth_percent")),
            ("安全边际", ("dcf", "margin_of_safety_percent")),
        ),
        "industry": (
            ("行业涨跌", ("prosperity", "sector_change_percent")),
            ("行业景气", ("prosperity", "label")),
            ("一年期 LPR", ("policy", "lpr_1y")),
            ("行业公司数", ("industry_profile", "company_count")),
        ),
        "macro": (
            ("指数区间收益", ("index", "window_return_percent")),
            ("目标资金净流", ("funds", "net_flow_cny")),
            ("GDP", ("macro", "gdp_current_percent")),
            ("SHIBOR 1W", ("macro", "shibor_1w")),
            ("市场状态", ("market_regime", "label")),
            ("风险偏好", ("risk_appetite", "label")),
        ),
    }
    return [
        {"label": label, "path": ".".join(path), "value": _dig(payload, path)}
        for label, path in paths[agent_id]
    ]


def _dig(value: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return deepcopy(current)


def _snapshot_projection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    output = {key: deepcopy(item) for key, item in value.items() if key != "datasets"}
    datasets = value.get("datasets", [])
    output["datasets"] = [
        {
            key: deepcopy(item)
            for key, item in dataset.items()
            if key not in {"records", "raw_records"}
        }
        for dataset in datasets
        if isinstance(dataset, Mapping)
    ]
    return output


def _snapshot_health_projection(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    datasets = value.get("datasets", [])
    statuses = [
        str(item.get("status", "unknown"))
        for item in datasets
        if isinstance(item, Mapping)
    ]
    return {
        "available_count": value.get("available_count"),
        "dataset_count": value.get("dataset_count"),
        "degraded": bool(value.get("degraded", False)),
        "unavailable_count": statuses.count("not_available"),
        "degraded_count": sum(
            status in {"backup", "cache_stale", "not_available"}
            for status in statuses
        ),
    }


def _fingerprint(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
