"""Deterministic industry analysis over the B1 industry and policy datasets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


class IndustryAnalysisError(ValueError):
    """Industry-analysis input is incomplete or violates an invariant."""


def _datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise IndustryAnalysisError(f"{label} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise IndustryAnalysisError(f"{label} must be an ISO datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IndustryAnalysisError(f"{label} must include a timezone")
    return parsed


def _decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise IndustryAnalysisError(f"{label} must be numeric") from error
    if not parsed.is_finite():
        raise IndustryAnalysisError(f"{label} must be finite")
    return parsed


def _number(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _records(bundle: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    payload = bundle.get(key)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("records"), list):
        raise IndustryAnalysisError(f"missing industry payload: {key}")
    records = payload["records"]
    if not records:
        raise IndustryAnalysisError(f"{key} must contain records")
    checked = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or not isinstance(record.get("fields"), Mapping):
            raise IndustryAnalysisError(f"{key}.records[{index}] has an invalid shape")
        for field in ("source", "timestamp", "as_of"):
            if not record.get(field):
                raise IndustryAnalysisError(f"{key}.records[{index}] is missing {field}")
        _datetime(record["timestamp"], f"{key}.timestamp")
        _datetime(record["as_of"], f"{key}.as_of")
        checked.append(record)
    return checked


def _field(record: Mapping[str, Any], names: Sequence[str], label: str) -> Decimal:
    fields = record["fields"]
    for name in names:
        if name in fields and fields[name] not in (None, ""):
            return _decimal(fields[name], label)
    raise IndustryAnalysisError(f"{label} is unavailable")


def _text(record: Mapping[str, Any], names: Sequence[str], label: str) -> str:
    fields = record["fields"]
    for name in names:
        if name in fields and fields[name] not in (None, ""):
            return str(fields[name])
    return "unknown"


def _component(name: str, points: int, rule: str) -> dict[str, Any]:
    return {"name": name, "points": points, "rule": rule}


INDUSTRY_CHAIN_TAXONOMY = {
    "玻璃行业": {
        "upstream": ["纯碱", "石英砂", "能源"],
        "midstream": ["浮法玻璃", "光伏玻璃", "玻璃深加工"],
        "downstream": ["房地产和建筑", "汽车", "家电和光伏组件"],
    },
    "金融行业": {
        "upstream": ["资本和负债来源", "利率和流动性环境", "金融基础设施"],
        "midstream": ["银行", "证券", "保险"],
        "downstream": ["企业融资", "居民信贷", "财富管理和资产配置"],
    },
    "酿酒行业": {
        "upstream": ["高粱和粮食原料", "包装材料", "能源和物流"],
        "midstream": ["白酒酿造", "品牌和渠道", "质量检测"],
        "downstream": ["餐饮消费", "礼赠消费", "大众和高端消费"],
    },
}


class IndustryAnalysisEngine:
    """Deep calculation module behind one ``analyze(bundle, sector)`` interface."""

    def analyze(self, bundle: Mapping[str, Any], *, sector: str) -> dict[str, Any]:
        if not isinstance(sector, str) or not sector.strip():
            raise IndustryAnalysisError("sector must be a non-empty string")
        snapshot = _records(bundle, "industry_snapshot")
        policy = _records(bundle, "policy_lpr")
        target = next(
            (record for record in snapshot if str(record.get("subject")) == sector),
            None,
        )
        if target is None:
            raise IndustryAnalysisError(
                f"sector {sector} is not present in the industry snapshot"
            )

        target_change = _field(target, ("涨跌幅",), "sector change percent")
        target_company_count = _field(target, ("公司家数",), "company count")
        target_average_price = _field(target, ("平均价格",), "average price")
        target_volume = _field(target, ("总成交量",), "sector volume")
        target_turnover = _field(target, ("总成交额",), "sector turnover")

        ranking = sorted(
            snapshot,
            key=lambda record: _field(
                record,
                ("个股-涨跌幅", "涨跌幅"),
                "representative stock change",
            ),
            reverse=True,
        )
        leaders = []
        for rank, record in enumerate(ranking[:3], 1):
            leaders.append(
                {
                    "rank": rank,
                    "sector": str(record.get("subject", "unknown")),
                    "stock_code": _text(record, ("股票代码",), "stock code"),
                    "stock_name": _text(record, ("股票名称",), "stock name"),
                    "representative_change_percent": _number(
                        _field(record, ("个股-涨跌幅", "涨跌幅"), "representative stock change")
                    ),
                    "sector_change_percent": _number(
                        _field(record, ("涨跌幅",), "sector change percent")
                    ),
                    "company_count": int(
                        _field(record, ("公司家数",), "company count")
                    ),
                }
            )

        largest = max(
            snapshot,
            key=lambda record: _field(record, ("公司家数",), "company count"),
        )
        latest_policy = max(policy, key=lambda record: _datetime(record["as_of"], "as_of"))
        ordered_policy = sorted(
            policy, key=lambda record: _datetime(record["as_of"], "as_of"), reverse=True
        )
        previous_policy = ordered_policy[1] if len(ordered_policy) > 1 else latest_policy
        latest_lpr = _field(latest_policy, ("LPR1Y",), "latest 1Y LPR")
        previous_lpr = _field(previous_policy, ("LPR1Y",), "previous 1Y LPR")
        lpr_change = latest_lpr - previous_lpr
        if lpr_change < 0:
            policy_signal, policy_points, policy_rule = "easing", 10, "1Y LPR decreased"
        elif lpr_change > 0:
            policy_signal, policy_points, policy_rule = "tightening", -10, "1Y LPR increased"
        else:
            policy_signal, policy_points, policy_rule = "stable", 5, "1Y LPR unchanged"

        if target_change >= Decimal("2"):
            prosperity_label, prosperity_points, prosperity_rule = "hot", 20, "sector change >= 2%"
        elif target_change >= 0:
            prosperity_label, prosperity_points, prosperity_rule = "improving", 10, "0% <= sector change < 2%"
        else:
            prosperity_label, prosperity_points, prosperity_rule = "weakening", -10, "sector change < 0%"

        representative_change = _field(
            target,
            ("个股-涨跌幅", "涨跌幅"),
            "target representative stock change",
        )
        leader_points, leader_rule = (
            (10, "target representative stock change >= 0%")
            if representative_change >= 0
            else (-10, "target representative stock change < 0%")
        )
        if target_company_count >= 100:
            competition_points, competition_rule = -5, "target industry has at least 100 companies"
        else:
            competition_points, competition_rule = 5, "target industry has fewer than 100 companies"

        components = (
            _component("prosperity", prosperity_points, prosperity_rule),
            _component("policy", policy_points, policy_rule),
            _component("competition", competition_points, competition_rule),
            _component("leader", leader_points, leader_rule),
        )
        score = sum(item["points"] for item in components)
        chain = INDUSTRY_CHAIN_TAXONOMY.get(
            sector,
            {
                "upstream": ["not_available_in_current_dataset"],
                "midstream": ["not_available_in_current_dataset"],
                "downstream": ["not_available_in_current_dataset"],
            },
        )
        all_records = [*snapshot, *policy]
        return {
            "sector": sector,
            "as_of": max(_datetime(record["as_of"], "as_of") for record in all_records).isoformat(),
            "timestamp": max(
                _datetime(record["timestamp"], "timestamp") for record in all_records
            ).isoformat(),
            "industry_profile": {
                "company_count": int(target_company_count),
                "average_price": _number(target_average_price),
                "change_percent": _number(target_change),
                "total_volume": _number(target_volume),
                "total_turnover": _number(target_turnover),
                "representative_stock_code": _text(target, ("股票代码",), "stock code"),
                "representative_stock_name": _text(target, ("股票名称",), "stock name"),
            },
            "competition": {
                "sector_count": len(snapshot),
                "largest_sector_by_company_count": str(largest.get("subject", "unknown")),
                "largest_sector_company_count": int(
                    _field(largest, ("公司家数",), "company count")
                ),
                "leader_ranking_method": "provider_representative_stock_sort",
            },
            "policy": {
                "latest_as_of": latest_policy["as_of"],
                "lpr_1y": _number(latest_lpr),
                "lpr_5y": _number(_field(latest_policy, ("LPR5Y",), "latest 5Y LPR")),
                "previous_lpr_1y": _number(previous_lpr),
                "change_1y": _number(lpr_change),
                "signal": policy_signal,
            },
            "prosperity": {
                "label": prosperity_label,
                "rule": prosperity_rule,
                "sector_change_percent": _number(target_change),
            },
            "industry_chain": {
                "method": "project_taxonomy_rule",
                **chain,
            },
            "leaders": leaders,
            "score": score,
            "score_label": self._score_label(score),
            "score_components": list(components),
            "sources": sorted({str(record["source"]) for record in all_records}),
            "caveats": [
                "industry_chain is a project taxonomy template, not a newly retrieved causal supply-chain dataset",
                "leader ranking sorts the provider's representative stock for each sector; it is not a full constituent ranking",
                "policy_signal is an indicator association, not a causal investment conclusion",
            ],
        }

    @staticmethod
    def _score_label(score: int) -> str:
        if score >= 45:
            return "strong_positive"
        if score >= 20:
            return "positive"
        if score > -20:
            return "neutral"
        if score > -45:
            return "negative"
        return "strong_negative"


__all__ = ["INDUSTRY_CHAIN_TAXONOMY", "IndustryAnalysisEngine", "IndustryAnalysisError"]
