"""Deterministic fundamental analysis over normalized Data Hub payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


class FundamentalAnalysisError(ValueError):
    """Fundamental-analysis input is incomplete or violates an invariant."""


DATASETS = (
    "fundamental.balance_sheet",
    "fundamental.income_statement",
    "fundamental.cash_flow",
    "fundamental.indicators",
    "fundamental.valuation",
    "market.realtime",
)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise FundamentalAnalysisError(f"{field_name} must be an ISO datetime")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise FundamentalAnalysisError(
            f"{field_name} must be an ISO datetime"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FundamentalAnalysisError(f"{field_name} must include a timezone")
    return parsed


def _decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise FundamentalAnalysisError(f"{field_name} must be numeric") from error
    if not parsed.is_finite():
        raise FundamentalAnalysisError(f"{field_name} must be finite")
    return parsed


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _price(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _percent(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _ratio(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _records(bundle: Mapping[str, Any], dataset: str) -> list[Mapping[str, Any]]:
    payload = bundle.get(dataset)
    if not isinstance(payload, Mapping):
        raise FundamentalAnalysisError(f"missing Data Hub payload: {dataset}")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise FundamentalAnalysisError(f"{dataset} must contain records")
    checked: list[Mapping[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise FundamentalAnalysisError(f"{dataset}.records[{index}] is invalid")
        fields = record.get("fields")
        for required in ("source", "timestamp", "as_of"):
            if not record.get(required):
                raise FundamentalAnalysisError(
                    f"{dataset}.records[{index}] is missing {required}"
                )
        _parse_datetime(record["timestamp"], f"{dataset}.timestamp")
        _parse_datetime(record["as_of"], f"{dataset}.as_of")
        if not isinstance(fields, Mapping):
            raise FundamentalAnalysisError(
                f"{dataset}.records[{index}].fields must be an object"
            )
        checked.append(record)
    return checked


def _latest(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return max(records, key=lambda record: _parse_datetime(record["as_of"], "as_of"))


def _annual_or_latest(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    annual = [
        record
        for record in records
        if _parse_datetime(record["as_of"], "as_of").month == 12
        and _parse_datetime(record["as_of"], "as_of").day == 31
    ]
    return _latest(annual or records)


def _field(
    record: Mapping[str, Any],
    names: Sequence[str],
    *,
    label: str,
) -> Decimal:
    fields = record["fields"]
    for name in names:
        if name in fields and fields[name] not in (None, ""):
            return _decimal(fields[name], label)
    raise FundamentalAnalysisError(f"{label} is unavailable in Data Hub payload")


def _field_text(record: Mapping[str, Any], name: str, *, label: str) -> str:
    fields = record["fields"]
    value = fields.get(name)
    if value in (None, ""):
        raise FundamentalAnalysisError(f"{label} is unavailable in Data Hub payload")
    return str(value)


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))


def _band_percentile(value: Decimal, upper_bound: Decimal) -> Decimal:
    if value < 0:
        raise FundamentalAnalysisError("valuation multiple must not be negative")
    return _clamp(
        (Decimal("1") - value / upper_bound) * Decimal("100"),
        Decimal("0"),
        Decimal("100"),
    )


def _component(name: str, points: int, rule: str) -> dict[str, Any]:
    return {"name": name, "points": points, "rule": rule}


class FundamentalAnalysisEngine:
    """Deep calculation module behind one ``analyze(bundle, symbol)`` interface."""

    DCF_YEARS = 5
    DCF_DISCOUNT_RATE = Decimal("0.10")
    DCF_TERMINAL_GROWTH = Decimal("0.03")

    def analyze(
        self,
        bundle: Mapping[str, Any],
        *,
        symbol: str,
    ) -> dict[str, Any]:
        if not isinstance(bundle, Mapping):
            raise FundamentalAnalysisError("fundamental bundle must be an object")
        if not isinstance(symbol, str) or not symbol.strip():
            raise FundamentalAnalysisError("symbol must be a non-empty string")

        records_by_dataset = {
            dataset: _records(bundle, dataset) for dataset in DATASETS
        }
        balance = _latest(records_by_dataset["fundamental.balance_sheet"])
        income_latest = _latest(records_by_dataset["fundamental.income_statement"])
        income_base = _annual_or_latest(records_by_dataset["fundamental.income_statement"])
        cash_flow = _latest(records_by_dataset["fundamental.cash_flow"])
        indicators = _latest(records_by_dataset["fundamental.indicators"])
        valuation = _latest(records_by_dataset["fundamental.valuation"])
        realtime = _latest(records_by_dataset["market.realtime"])

        assets = _field(balance, ("资产总计",), label="total assets")
        liabilities = _field(balance, ("负债合计",), label="total liabilities")
        equity = _field(
            balance,
            ("归属于母公司股东的权益", "归属于母公司所有者权益"),
            label="shareholder equity",
        )
        if assets <= 0 or equity <= 0:
            raise FundamentalAnalysisError("assets and shareholder equity must be positive")
        computed_debt_ratio = liabilities / assets * Decimal("100")

        revenue = _field(income_base, ("营业收入",), label="annual revenue")
        net_profit = _field(
            income_base,
            ("归属于母公司的净利润", "净利润"),
            label="annual net profit",
        )
        basic_eps = _field(
            income_base,
            ("基本每股收益", "稀释每股收益"),
            label="annual basic EPS",
        )
        latest_revenue = _field(income_latest, ("营业收入",), label="latest revenue")
        latest_net_profit = _field(
            income_latest,
            ("归属于母公司的净利润", "净利润"),
            label="latest net profit",
        )
        net_margin = net_profit / revenue * Decimal("100") if revenue else Decimal("0")

        operating_cash_flow = _field(
            cash_flow,
            ("经营活动产生的现金流量净额",),
            label="operating cash flow",
        )
        capex = abs(
            _field(
                cash_flow,
                (
                    "购建固定资产、无形资产和其他长期资产支付的现金",
                    "购建固定资产、无形资产及其他长期资产支付的现金",
                ),
                label="capital expenditure",
            )
        )
        free_cash_flow_proxy = operating_cash_flow - capex

        roe = _field(indicators, ("roe_percent",), label="ROE")
        weighted_roe = _field(
            indicators,
            ("weighted_roe_percent",),
            label="weighted ROE",
        )
        provider_debt_ratio = _field(
            indicators,
            ("debt_to_asset_percent",),
            label="provider debt ratio",
        )
        roa = _field(
            indicators,
            ("return_on_assets_percent",),
            label="ROA",
        )
        profit_growth = _field(
            indicators,
            ("net_profit_growth_percent",),
            label="net profit growth",
        )

        pe = _field(valuation, ("pe_dynamic",), label="dynamic PE")
        pb = _field(valuation, ("pb",), label="PB")
        ps = _field(valuation, ("ps",), label="PS")
        market_cap = _field(valuation, ("market_cap_cny",), label="market cap")
        current_price = _field(realtime, ("last",), label="current price")
        if current_price <= 0 or basic_eps <= 0:
            raise FundamentalAnalysisError("current price and EPS must be positive")

        pe_percentile = _band_percentile(pe, Decimal("20"))
        pb_percentile = _band_percentile(pb, Decimal("3"))
        ps_percentile = _band_percentile(ps, Decimal("10"))
        valuation_percentile = (pe_percentile + pb_percentile + ps_percentile) / Decimal("3")

        growth_assumption = _clamp(
            profit_growth / Decimal("100"),
            Decimal("-0.02"),
            Decimal("0.08"),
        )
        dcf = self._discounted_earnings(
            basic_eps=basic_eps,
            current_price=current_price,
            growth_assumption=growth_assumption,
        )

        if weighted_roe >= Decimal("10"):
            profitability_points, profitability_rule = 20, "weighted ROE >= 10%"
        elif weighted_roe >= Decimal("5"):
            profitability_points, profitability_rule = 10, "5% <= weighted ROE < 10%"
        else:
            profitability_points, profitability_rule = 0, "weighted ROE < 5%"

        if profit_growth >= Decimal("10"):
            growth_points, growth_rule = 20, "net profit growth >= 10%"
        elif profit_growth >= 0:
            growth_points, growth_rule = 10, "0% <= net profit growth < 10%"
        else:
            growth_points, growth_rule = -10, "net profit growth < 0%"

        if computed_debt_ratio <= Decimal("90"):
            balance_points, balance_rule = 10, "computed debt ratio <= 90%"
        elif computed_debt_ratio <= Decimal("95"):
            balance_points, balance_rule = 5, "90% < computed debt ratio <= 95%"
        else:
            balance_points, balance_rule = -10, "computed debt ratio > 95%"

        if valuation_percentile >= Decimal("75"):
            valuation_points, valuation_rule = 15, "rule-based valuation percentile >= 75"
        elif valuation_percentile >= Decimal("50"):
            valuation_points, valuation_rule = 10, "50 <= rule-based valuation percentile < 75"
        else:
            valuation_points, valuation_rule = 0, "rule-based valuation percentile < 50"

        if free_cash_flow_proxy > 0:
            cash_flow_points, cash_flow_rule = 10, "operating cash flow exceeds capex proxy"
        else:
            cash_flow_points, cash_flow_rule = -10, "operating cash flow does not cover capex proxy"

        margin_of_safety = _decimal(dcf["margin_of_safety_percent"], "margin of safety")
        if margin_of_safety >= Decimal("30"):
            dcf_points, dcf_rule = 20, "DCF margin of safety >= 30%"
        elif margin_of_safety >= 0:
            dcf_points, dcf_rule = 10, "0% <= DCF margin of safety < 30%"
        else:
            dcf_points, dcf_rule = -15, "DCF margin of safety < 0%"

        components = (
            _component("profitability", profitability_points, profitability_rule),
            _component("growth", growth_points, growth_rule),
            _component("balance_sheet", balance_points, balance_rule),
            _component("valuation", valuation_points, valuation_rule),
            _component("cash_flow", cash_flow_points, cash_flow_rule),
            _component("dcf", dcf_points, dcf_rule),
        )
        score = sum(item["points"] for item in components)

        all_records = [record for records in records_by_dataset.values() for record in records]
        timestamp = max(
            _parse_datetime(record["timestamp"], "timestamp") for record in all_records
        )
        as_of = max(_parse_datetime(record["as_of"], "as_of") for record in all_records)
        sources = sorted({str(record["source"]) for record in all_records})

        return {
            "symbol": symbol,
            "as_of": as_of.isoformat(),
            "timestamp": timestamp.isoformat(),
            "latest_financial_period": income_latest["as_of"],
            "annual_base_period": income_base["as_of"],
            "statements": {
                "balance_sheet": {
                    "assets_cny": _money(assets),
                    "liabilities_cny": _money(liabilities),
                    "shareholder_equity_cny": _money(equity),
                    "computed_debt_to_asset_percent": _percent(computed_debt_ratio),
                    "provider_debt_to_asset_percent": _percent(provider_debt_ratio),
                },
                "income_statement": {
                    "annual_revenue_cny": _money(revenue),
                    "annual_net_profit_cny": _money(net_profit),
                    "latest_revenue_cny": _money(latest_revenue),
                    "latest_net_profit_cny": _money(latest_net_profit),
                    "net_margin_percent": _percent(net_margin),
                    "basic_eps_cny": _price(basic_eps),
                },
                "cash_flow": {
                    "operating_cash_flow_cny": _money(operating_cash_flow),
                    "capex_cny": _money(capex),
                    "free_cash_flow_proxy_cny": _money(free_cash_flow_proxy),
                },
            },
            "indicators": {
                "roe_percent": _percent(roe),
                "weighted_roe_percent": _percent(weighted_roe),
                "roa_percent": _percent(roa),
                "net_profit_growth_percent": _percent(profit_growth),
            },
            "valuation": {
                "current_price": _price(current_price),
                "market_cap_cny": _money(market_cap),
                "pe_dynamic": _ratio(pe),
                "pb": _ratio(pb),
                "ps": _ratio(ps),
                "valuation_percentile": _percent(valuation_percentile),
                "valuation_percentile_method": "rule_based_not_historical",
                "valuation_band": "PE<=20, PB<=3, PS<=10; lower multiple gets a higher percentile",
            },
            "growth": {
                "net_profit_growth_percent": _percent(profit_growth),
                "growth_rule": "uses the latest Data Hub net_profit_growth_percent",
            },
            "dcf": dcf,
            "score": score,
            "score_label": self._score_label(score),
            "score_components": list(components),
            "sources": sources,
            "caveats": [
                "valuation_percentile is a transparent rule-band percentile, not a historical market percentile",
                "DCF uses discounted earnings per share as a banking-stock proxy; it is not a generic industrial free-cash-flow DCF",
                "the report is research evidence, not investment advice or a trading instruction",
            ],
        }

    def _discounted_earnings(
        self,
        *,
        basic_eps: Decimal,
        current_price: Decimal,
        growth_assumption: Decimal,
    ) -> dict[str, Any]:
        discount = self.DCF_DISCOUNT_RATE
        terminal_growth = self.DCF_TERMINAL_GROWTH
        forecasts: list[dict[str, str]] = []
        present_value = Decimal("0")
        eps = basic_eps
        for year in range(1, self.DCF_YEARS + 1):
            eps *= Decimal("1") + growth_assumption
            discounted = eps / (Decimal("1") + discount) ** year
            present_value += discounted
            forecasts.append(
                {
                    "year": str(year),
                    "eps": _price(eps),
                    "present_value": _price(discounted),
                }
            )
        terminal_value = eps * (Decimal("1") + terminal_growth) / (discount - terminal_growth)
        discounted_terminal = terminal_value / (Decimal("1") + discount) ** self.DCF_YEARS
        intrinsic = present_value + discounted_terminal
        margin = (intrinsic - current_price) / intrinsic * Decimal("100")
        return {
            "method": "discounted_earnings_proxy",
            "base_eps_cny": _price(basic_eps),
            "growth_assumption_percent": _percent(growth_assumption * Decimal("100")),
            "discount_rate_percent": _percent(discount * Decimal("100")),
            "terminal_growth_percent": _percent(terminal_growth * Decimal("100")),
            "forecast_years": self.DCF_YEARS,
            "forecasts": forecasts,
            "intrinsic_value_per_share": _price(intrinsic),
            "current_price": _price(current_price),
            "margin_of_safety_percent": _percent(margin),
        }

    @staticmethod
    def _score_label(score: int) -> str:
        if score >= 60:
            return "strong_positive"
        if score >= 25:
            return "positive"
        if score > -25:
            return "neutral"
        if score > -60:
            return "negative"
        return "strong_negative"


__all__ = ["DATASETS", "FundamentalAnalysisEngine", "FundamentalAnalysisError"]
