"""Deterministic market and macro analysis over the B1 datasets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


class MacroAnalysisError(ValueError):
    """Market/macro analysis input is incomplete or violates an invariant."""


def _datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise MacroAnalysisError(f"{label} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise MacroAnalysisError(f"{label} must be an ISO datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MacroAnalysisError(f"{label} must include a timezone")
    return parsed


def _decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise MacroAnalysisError(f"{label} must be numeric") from error
    if not parsed.is_finite():
        raise MacroAnalysisError(f"{label} must be finite")
    return parsed


def _number(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _records(bundle: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    payload = bundle.get(key)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("records"), list):
        raise MacroAnalysisError(f"missing macro payload: {key}")
    records = payload["records"]
    if not records:
        raise MacroAnalysisError(f"{key} must contain records")
    checked = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or not isinstance(record.get("fields"), Mapping):
            raise MacroAnalysisError(f"{key}.records[{index}] has an invalid shape")
        for field in ("source", "timestamp", "as_of"):
            if not record.get(field):
                raise MacroAnalysisError(f"{key}.records[{index}] is missing {field}")
        _datetime(record["timestamp"], f"{key}.timestamp")
        _datetime(record["as_of"], f"{key}.as_of")
        checked.append(record)
    return checked


def _field(record: Mapping[str, Any], names: Sequence[str], label: str) -> Decimal:
    fields = record["fields"]
    for name in names:
        if name in fields and fields[name] not in (None, ""):
            return _decimal(fields[name], label)
    raise MacroAnalysisError(f"{label} is unavailable")


def _text(record: Mapping[str, Any], names: Sequence[str], label: str) -> str:
    fields = record["fields"]
    for name in names:
        if name in fields and fields[name] not in (None, ""):
            return str(fields[name])
    return "unknown"


def _component(name: str, points: int, rule: str) -> dict[str, Any]:
    return {"name": name, "points": points, "rule": rule}


POSITIVE_RATINGS = {"买入", "强烈推荐", "推荐", "增持", "跑赢大盘", "优于大市"}
NEGATIVE_RATINGS = {"卖出", "强烈卖出", "减持", "回避", "弱于大市"}


class MacroAnalysisEngine:
    """Deep calculation module behind one analyze(bundle, ...) interface."""

    def analyze(
        self,
        bundle: Mapping[str, Any],
        *,
        index_symbol: str,
        symbol: str,
    ) -> dict[str, Any]:
        index_records = sorted(
            _records(bundle, "macro_index"),
            key=lambda record: _datetime(record["as_of"], "macro_index.as_of"),
        )
        fund_records = sorted(
            _records(bundle, "fund_flow"),
            key=lambda record: _datetime(record["as_of"], "fund_flow.as_of"),
        )
        gdp_records = sorted(
            _records(bundle, "macro_gdp"),
            key=lambda record: _datetime(record["as_of"], "macro_gdp.as_of"),
        )
        shibor_records = sorted(
            _records(bundle, "macro_shibor"),
            key=lambda record: _datetime(record["as_of"], "macro_shibor.as_of"),
        )
        policy_records = sorted(
            _records(bundle, "policy_lpr"),
            key=lambda record: _datetime(record["as_of"], "policy_lpr.as_of"),
        )
        research_records = sorted(
            _records(bundle, "research"),
            key=lambda record: _datetime(record["as_of"], "research.as_of"),
        )

        latest_index = index_records[-1]
        previous_index = index_records[-2] if len(index_records) > 1 else latest_index
        oldest_index = index_records[0]
        latest_close = _field(latest_index, ("close", "收盘"), "latest index close")
        previous_close = _field(previous_index, ("close", "收盘"), "previous index close")
        oldest_close = _field(oldest_index, ("close", "收盘"), "oldest index close")
        latest_return = (
            (latest_close / previous_close - Decimal("1")) * Decimal("100")
            if previous_close
            else Decimal("0")
        )
        window_return = (
            (latest_close / oldest_close - Decimal("1")) * Decimal("100")
            if oldest_close
            else Decimal("0")
        )
        latest_volume = _field(latest_index, ("volume", "成交量"), "latest index volume")
        if window_return >= Decimal("1"):
            index_trend, index_points, index_rule = "bullish", 20, "window return >= 1%"
        elif window_return <= Decimal("-1"):
            index_trend, index_points, index_rule = "bearish", -20, "window return <= -1%"
        else:
            index_trend, index_points, index_rule = "flat", 0, "-1% < window return < 1%"

        latest_fund = fund_records[-1]
        fund_last = _field(latest_fund, ("last", "最新价"), "fund-flow latest price")
        fund_change = _field(
            latest_fund, ("change_percent", "涨跌幅"), "fund-flow change percent"
        )
        inflow = _field(latest_fund, ("inflow_cny", "主力流入"), "fund-flow inflow")
        outflow = _field(latest_fund, ("outflow_cny", "主力流出"), "fund-flow outflow")
        net_flow = _field(latest_fund, ("net_flow_cny", "主力净流入"), "fund-flow net flow")
        turnover = _field(
            latest_fund, ("turnover_amount_cny", "成交额"), "fund-flow turnover"
        )
        flow_ratio = net_flow / turnover * Decimal("100") if turnover else Decimal("0")
        if net_flow > 0:
            flow_direction, fund_points, fund_rule = "inflow", 20, "target net flow > 0"
        elif net_flow < 0:
            flow_direction, fund_points, fund_rule = "outflow", -20, "target net flow < 0"
        else:
            flow_direction, fund_points, fund_rule = "balanced", 0, "target net flow = 0"

        latest_gdp = gdp_records[-1]
        gdp_current = _field(latest_gdp, ("今值", "current"), "latest GDP")
        gdp_previous = _field(latest_gdp, ("前值", "previous"), "previous GDP")
        gdp_change = gdp_current - gdp_previous

        latest_shibor = shibor_records[-1]
        previous_shibor = shibor_records[-2] if len(shibor_records) > 1 else latest_shibor
        shibor_1w = _field(latest_shibor, ("1W-定价", "shibor_1w"), "latest 1W SHIBOR")
        previous_shibor_1w = _field(
            previous_shibor, ("1W-定价", "shibor_1w"), "previous 1W SHIBOR"
        )
        shibor_change = shibor_1w - previous_shibor_1w

        latest_policy = policy_records[-1]
        previous_policy = policy_records[-2] if len(policy_records) > 1 else latest_policy
        lpr_1y = _field(latest_policy, ("LPR1Y",), "latest 1Y LPR")
        previous_lpr_1y = _field(previous_policy, ("LPR1Y",), "previous 1Y LPR")
        lpr_change = lpr_1y - previous_lpr_1y

        ratings: dict[str, int] = {}
        for record in research_records:
            rating = _text(record, ("东财评级", "rating"), "research rating")
            if rating == "not_available":
                continue
            ratings[rating] = ratings.get(rating, 0) + 1
        latest_rating = _text(
            research_records[-1], ("东财评级", "rating"), "latest research rating"
        )
        positive_count = sum(
            count for rating, count in ratings.items() if rating in POSITIVE_RATINGS
        )
        negative_count = sum(
            count for rating, count in ratings.items() if rating in NEGATIVE_RATINGS
        )
        if not ratings:
            research_label, research_points, research_rule = (
                "neutral",
                0,
                "no research ratings were available; neutral evidence used",
            )
        elif positive_count > negative_count and positive_count >= max(ratings.values()):
            research_label, research_points, research_rule = (
                "positive",
                10,
                "positive research ratings lead",
            )
        elif negative_count > positive_count and negative_count >= max(ratings.values()):
            research_label, research_points, research_rule = (
                "negative",
                -10,
                "negative research ratings lead",
            )
        else:
            research_label, research_points, research_rule = (
                "neutral",
                0,
                "research ratings are mixed or neutral",
            )
        if latest_return >= 0 and net_flow >= 0:
            market_proxy_points, market_proxy_rule = (
                5,
                "latest index return and target net flow are non-negative",
            )
        elif latest_return < 0 and net_flow < 0:
            market_proxy_points, market_proxy_rule = (
                -5,
                "latest index return and target net flow are negative",
            )
        else:
            market_proxy_points, market_proxy_rule = (
                0,
                "index return and target net flow are mixed",
            )
        sentiment_points = research_points + market_proxy_points
        sentiment_label = (
            "positive"
            if sentiment_points >= 10
            else "negative"
            if sentiment_points <= -10
            else "neutral"
        )

        if gdp_change >= 0 and shibor_change <= 0 and lpr_change <= 0:
            macro_points, macro_rule = (
                10,
                "GDP is not slowing and short rate/LPR are not rising",
            )
        elif gdp_change < 0 and shibor_change > 0 and lpr_change >= 0:
            macro_points, macro_rule = (
                -10,
                "GDP is slowing while short rate/LPR are not easing",
            )
        else:
            macro_points, macro_rule = 0, "macro indicators are mixed"

        if window_return >= Decimal("1") and net_flow >= 0:
            regime, regime_rule = (
                "risk_on",
                "index window trend is bullish and target flow is inflow",
            )
        elif window_return <= Decimal("-1") and net_flow < 0:
            regime, regime_rule = (
                "risk_off",
                "index window trend is bearish and target flow is outflow",
            )
        else:
            regime, regime_rule = (
                "mixed",
                "index trend and target flow do not form a one-sided regime",
            )

        components = (
            _component("index_trend", index_points, index_rule),
            _component("funds", fund_points, fund_rule),
            _component(
                "sentiment",
                sentiment_points,
                f"{research_rule}; {market_proxy_rule}",
            ),
            _component("macro", macro_points, macro_rule),
        )
        score = sum(item["points"] for item in components)
        risk_appetite = (
            "high"
            if score >= 35
            else "moderate"
            if score >= 10
            else "cautious"
            if score > -10
            else "low"
        )
        all_records = [
            *index_records,
            *fund_records,
            *gdp_records,
            *shibor_records,
            *policy_records,
            *research_records,
        ]
        return {
            "index_symbol": index_symbol,
            "symbol": symbol,
            "as_of": max(
                _datetime(record["as_of"], "as_of") for record in all_records
            ).isoformat(),
            "timestamp": max(
                _datetime(record["timestamp"], "timestamp") for record in all_records
            ).isoformat(),
            "index": {
                "sample_size": len(index_records),
                "latest_as_of": latest_index["as_of"],
                "latest_close": _number(latest_close),
                "previous_close": _number(previous_close),
                "latest_return_percent": _number(latest_return),
                "window_return_percent": _number(window_return),
                "latest_volume": _number(latest_volume),
                "trend": index_trend,
                "trend_rule": index_rule,
            },
            "funds": {
                "subject_symbol": symbol,
                "latest_as_of": latest_fund["as_of"],
                "last": _number(fund_last),
                "change_percent": _number(fund_change),
                "inflow_cny": _number(inflow),
                "outflow_cny": _number(outflow),
                "net_flow_cny": _number(net_flow),
                "flow_ratio_percent": _number(flow_ratio),
                "direction": flow_direction,
            },
            "sentiment": {
                "research_count": sum(ratings.values()),
                "latest_rating": latest_rating,
                "rating_counts": dict(sorted(ratings.items())),
                "research_label": research_label,
                "market_proxy_points": market_proxy_points,
                "label": sentiment_label,
            },
            "macro": {
                "gdp_latest_as_of": latest_gdp["as_of"],
                "gdp_current_percent": _number(gdp_current),
                "gdp_previous_percent": _number(gdp_previous),
                "gdp_change_percent": _number(gdp_change),
                "shibor_latest_as_of": latest_shibor["as_of"],
                "shibor_1w": _number(shibor_1w),
                "previous_shibor_1w": _number(previous_shibor_1w),
                "shibor_1w_change": _number(shibor_change),
                "lpr_1y": _number(lpr_1y),
                "previous_lpr_1y": _number(previous_lpr_1y),
                "lpr_1y_change": _number(lpr_change),
                "lpr_5y": _number(
                    _field(latest_policy, ("LPR5Y",), "latest 5Y LPR")
                ),
            },
            "market_regime": {
                "label": regime,
                "rule": regime_rule,
            },
            "risk_appetite": {
                "label": risk_appetite,
                "score": score,
                "rule": "score >= 35 high; >= 10 moderate; > -10 cautious; otherwise low",
            },
            "score": score,
            "score_label": self._score_label(score),
            "score_components": list(components),
            "sources": sorted({str(record["source"]) for record in all_records}),
            "caveats": [
                "funds uses the selected stock's fund-flow data as a capital-flow proxy, not a full-market aggregate",
                "sentiment combines research ratings with an index/stock-flow proxy; it is not a survey index",
                "market_regime and risk_appetite are transparent project heuristics, not official classifications",
                *(
                    ["no research report was available; research sentiment contributed zero points"]
                    if not ratings
                    else []
                ),
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


__all__ = [
    "MacroAnalysisEngine",
    "MacroAnalysisError",
    "POSITIVE_RATINGS",
    "NEGATIVE_RATINGS",
]
