"""Isolated true-external provider worker with JSON stdin/stdout."""

from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")
SYMBOL = re.compile(r"^(?:sh|sz|bj)\d{6}$")
RAW_SYMBOL = re.compile(r"^\d{6}$")
TS_CODE = re.compile(r"^\d{6}\.(?:SZ|SH|BJ)$")

SOURCES = {
    "market.daily": "akshare.stock_zh_a_hist_tx",
    "market.weekly": "akshare.stock_zh_a_hist_tx",
    "market.minute": "akshare.stock_zh_a_minute",
    "market.realtime": "tencent.qt.gtimg.cn",
    "market.fund_flow": "akshare.stock_fund_flow_individual",
    "fundamental.balance_sheet": "akshare.stock_financial_report_sina",
    "fundamental.income_statement": "akshare.stock_financial_report_sina",
    "fundamental.cash_flow": "akshare.stock_financial_report_sina",
    "fundamental.indicators": "akshare.stock_financial_analysis_indicator",
    "fundamental.valuation": "tencent.quote+sina.financial_report",
    "macro.index": "akshare.stock_zh_index_daily",
    "industry.snapshot": "akshare.stock_sector_spot",
    "macro.gdp": "akshare.macro_china_gdp_yearly",
    "macro.shibor": "akshare.macro_china_shibor_all",
    "macro.policy_lpr": "akshare.macro_china_lpr",
    "sentiment.news": "akshare.stock_news_main_cx",
    "sentiment.announcements": "akshare.stock_zh_a_disclosure_report_cninfo",
    "sentiment.research": "akshare.stock_research_report_em",
    "tushare.daily": "tushare.pro.daily",
}


def _limit(params: dict[str, Any], default: int, maximum: int = 200) -> int:
    value = params.get("limit", default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"limit must be an integer from 1 to {maximum}")
    return value


def _symbol(params: dict[str, Any], key: str = "symbol") -> str:
    value = params.get(key)
    if not isinstance(value, str) or not SYMBOL.fullmatch(value.lower()):
        raise ValueError(f"{key} must include a market prefix, for example sz000001")
    return value.lower()


def _raw_symbol(params: dict[str, Any], key: str = "symbol") -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a six-digit stock code")
    value = value.lower().removeprefix("sh").removeprefix("sz").removeprefix("bj")
    if not RAW_SYMBOL.fullmatch(value):
        raise ValueError(f"{key} must be a six-digit stock code")
    return value


def _date_text(params: dict[str, Any], key: str, default: str | None = None) -> str:
    value = params.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must use YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def _as_of_date(value: Any, *, close_time: bool = False) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        parsed = None
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(f"unsupported provider date: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    if close_time and parsed.hour == parsed.minute == parsed.second == 0:
        parsed = parsed.replace(hour=15)
    return parsed


def _number_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    try:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        item_method = getattr(value, "item", None)
        if callable(item_method):
            value = item_method()
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, (int, float, Decimal)):
            return str(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _fields(row: dict[str, Any], *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    return {
        str(key): _number_text(value)
        for key, value in row.items()
        if str(key) not in excluded and _number_text(value) is not None
    }


def _record(
    *,
    subject: str,
    fields: dict[str, Any],
    source: str,
    timestamp: datetime,
    as_of: datetime,
) -> dict[str, Any]:
    if as_of > timestamp:
        observed_now = datetime.now(TZ)
        if as_of <= observed_now:
            timestamp = observed_now
        else:
            raise ValueError(
                f"provider as_of is later than fetch time: {as_of.isoformat()}"
            )
    return {
        "subject": subject,
        "fields": fields,
        "source": source,
        "timestamp": timestamp.isoformat(),
        "as_of": as_of.isoformat(),
    }


def _cn_amount(value: Any) -> str:
    text = str(value).strip().replace(",", "")
    multiplier = Decimal("1")
    if text.endswith("亿"):
        multiplier = Decimal("100000000")
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = Decimal("10000")
        text = text[:-1]
    try:
        return str(Decimal(text) * multiplier)
    except InvalidOperation as error:
        raise ValueError(f"invalid Chinese amount: {value}") from error


def _tencent_quote(symbol: str) -> tuple[list[str], datetime]:
    import requests

    response = requests.get(f"https://qt.gtimg.cn/q={symbol}", timeout=8)
    response.raise_for_status()
    response.encoding = "gbk"
    parts = response.text.split("~")
    if len(parts) < 50 or parts[2] != symbol[2:]:
        raise ValueError("Tencent quote response shape changed")
    quote_time = datetime.strptime(parts[30], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
    return parts, quote_time


def _market_weekly(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _symbol(params)
    start_date = _date_text(params, "start_date")
    end_date = _date_text(params, "end_date")
    frame = ak.stock_zh_a_hist_tx(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        adjust="",
        timeout=8,
    )
    if frame.empty:
        return []
    rows = frame.to_dict(orient="records")
    weeks: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        trading_date = _as_of_date(row["date"]).date()
        iso = trading_date.isocalendar()
        weeks.setdefault((iso.year, iso.week), []).append(row)
    records = []
    for week_rows in weeks.values():
        week_rows.sort(key=lambda item: _as_of_date(item["date"]))
        first, last = week_rows[0], week_rows[-1]
        volume = sum(Decimal(str(item["amount"])) * 100 for item in week_rows)
        records.append(
            _record(
                subject=symbol,
                source=SOURCES["market.weekly"],
                timestamp=now,
                as_of=_as_of_date(last["date"], close_time=True),
                fields={
                    "period": "weekly",
                    "week_start": str(first["date"]),
                    "week_end": str(last["date"]),
                    "open": _number_text(first["open"]),
                    "high": str(max(Decimal(str(item["high"])) for item in week_rows)),
                    "low": str(min(Decimal(str(item["low"])) for item in week_rows)),
                    "close": _number_text(last["close"]),
                    "volume_shares": str(volume),
                },
            )
        )
    return records[-_limit(params, 20):]


def _market_daily(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _symbol(params)
    start_date = _date_text(params, "start_date")
    end_date = _date_text(params, "end_date")
    frame = ak.stock_zh_a_hist_tx(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        adjust="",
        timeout=8,
    ).tail(_limit(params, 100, 500))
    return [
        _record(
            subject=symbol,
            source=SOURCES["market.daily"],
            timestamp=now,
            as_of=_as_of_date(row["date"], close_time=True),
            fields={
                "period": "daily",
                "open": _number_text(row["open"]),
                "high": _number_text(row["high"]),
                "low": _number_text(row["low"]),
                "close": _number_text(row["close"]),
                "volume_shares": str(Decimal(str(row["amount"])) * 100),
            },
        )
        for row in frame.to_dict(orient="records")
    ]


def _market_minute(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _symbol(params)
    period = str(params.get("period", "60"))
    if period not in {"1", "5", "15", "30", "60"}:
        raise ValueError("period must be 1, 5, 15, 30, or 60")
    frame = ak.stock_zh_a_minute(symbol=symbol, period=period, adjust="")
    records = []
    for row in frame.tail(_limit(params, 50)).to_dict(orient="records"):
        records.append(
            _record(
                subject=symbol,
                source=SOURCES["market.minute"],
                timestamp=now,
                as_of=_as_of_date(row["day"]),
                fields={
                    "period_minutes": period,
                    "open": _number_text(row["open"]),
                    "high": _number_text(row["high"]),
                    "low": _number_text(row["low"]),
                    "close": _number_text(row["close"]),
                    "volume_shares": _number_text(row["volume"]),
                    "turnover_amount_cny": _number_text(row["amount"]),
                },
            )
        )
    return records


def _market_realtime(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    symbol = _symbol(params)
    parts, quote_time = _tencent_quote(symbol)
    amount = parts[35].split("/")[2] if len(parts[35].split("/")) >= 3 else ""
    return [
        _record(
            subject=symbol,
            source=SOURCES["market.realtime"],
            timestamp=now,
            as_of=quote_time,
            fields={
                "name": parts[1],
                "last": parts[3],
                "previous_close": parts[4],
                "open": parts[5],
                "high": parts[33],
                "low": parts[34],
                "volume_shares": str(Decimal(parts[36]) * 100),
                "turnover_amount_cny": amount,
                "turnover_rate_percent": parts[38],
            },
        )
    ]


def _market_fund_flow(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _raw_symbol(params)
    frame = ak.stock_fund_flow_individual(symbol="即时")
    matches = frame[frame["股票代码"].astype(str).str.zfill(6) == symbol]
    records = []
    for row in matches.head(1).to_dict(orient="records"):
        records.append(
            _record(
                subject=symbol,
                source=SOURCES["market.fund_flow"],
                timestamp=now,
                as_of=now,
                fields={
                    "name": row["股票简称"],
                    "last": _number_text(row["最新价"]),
                    "change_percent": str(row["涨跌幅"]).removesuffix("%"),
                    "turnover_rate_percent": str(row["换手率"]).removesuffix("%"),
                    "inflow_cny": _cn_amount(row["流入资金"]),
                    "outflow_cny": _cn_amount(row["流出资金"]),
                    "net_flow_cny": _cn_amount(row["净额"]),
                    "turnover_amount_cny": _cn_amount(row["成交额"]),
                },
            )
        )
    return records


def _financial_statement(
    dataset: str,
    params: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _symbol(params)
    statement_names = {
        "fundamental.balance_sheet": "资产负债表",
        "fundamental.income_statement": "利润表",
        "fundamental.cash_flow": "现金流量表",
    }
    frame = ak.stock_financial_report_sina(
        stock=symbol,
        symbol=statement_names[dataset],
    )
    frame = frame.sort_values("报告日", ascending=False).head(_limit(params, 4, 12))
    return [
        _record(
            subject=symbol,
            source=SOURCES[dataset],
            timestamp=now,
            as_of=_as_of_date(row["报告日"]),
            fields={"statement": statement_names[dataset], **_fields(row, exclude={"报告日"})},
        )
        for row in frame.to_dict(orient="records")
    ]


def _financial_indicators(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _raw_symbol(params)
    start_year = str(params.get("start_year", max(1900, now.year - 3)))
    if not re.fullmatch(r"\d{4}", start_year):
        raise ValueError("start_year must use YYYY")
    frame = ak.stock_financial_analysis_indicator(symbol=symbol, start_year=start_year)
    frame = frame.sort_values("日期", ascending=False).head(_limit(params, 8, 20))
    selected = {
        "净资产收益率(%)": "roe_percent",
        "加权净资产收益率(%)": "weighted_roe_percent",
        "资产负债率(%)": "debt_to_asset_percent",
        "总资产净利润率(%)": "return_on_assets_percent",
        "主营业务收入增长率(%)": "revenue_growth_percent",
        "净利润增长率(%)": "net_profit_growth_percent",
        "总资产(元)": "total_assets_cny",
    }
    records = []
    for row in frame.to_dict(orient="records"):
        fields = {
            output: _number_text(row.get(source))
            for source, output in selected.items()
            if _number_text(row.get(source)) is not None
        }
        records.append(
            _record(
                subject=symbol,
                source=SOURCES["fundamental.indicators"],
                timestamp=now,
                as_of=_as_of_date(row["日期"]),
                fields=fields,
            )
        )
    return records


def _valuation(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _symbol(params)
    parts, quote_time = _tencent_quote(symbol)
    market_cap_cny = Decimal(parts[44]) * Decimal("100000000")
    income = ak.stock_financial_report_sina(stock=symbol, symbol="利润表")
    annual = income[income["报告日"].astype(str).str.endswith("1231")].sort_values(
        "报告日", ascending=False
    )
    if annual.empty or "营业收入" not in annual:
        raise ValueError("latest annual revenue is unavailable for PS")
    revenue = Decimal(str(annual.iloc[0]["营业收入"]))
    ps = market_cap_cny / revenue if revenue > 0 else None
    return [
        _record(
            subject=symbol,
            source=SOURCES["fundamental.valuation"],
            timestamp=now,
            as_of=quote_time,
            fields={
                "pe_dynamic": parts[39] or None,
                "pb": parts[46] or None,
                "ps": str(ps) if ps is not None else None,
                "ps_basis": "market_cap/latest_annual_revenue",
                "market_cap_cny": str(market_cap_cny),
                "annual_revenue_cny": str(revenue),
                "annual_revenue_period": str(annual.iloc[0]["报告日"]),
            },
        )
    ]


def _macro_index(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = params.get("symbol", "sh000300")
    if not isinstance(symbol, str) or not re.fullmatch(r"^(?:sh|sz)\d{6}$", symbol):
        raise ValueError("index symbol must look like sh000300")
    frame = ak.stock_zh_index_daily(symbol=symbol).tail(_limit(params, 30))
    return [
        _record(
            subject=symbol,
            source=SOURCES["macro.index"],
            timestamp=now,
            as_of=_as_of_date(row["date"], close_time=True),
            fields=_fields(row, exclude={"date"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _industry_snapshot(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    indicator = params.get("indicator", "新浪行业")
    if indicator not in {"新浪行业", "启明星行业", "概念", "地域", "行业"}:
        raise ValueError("unsupported sector indicator")
    frame = ak.stock_sector_spot(indicator=indicator)
    sector = params.get("sector")
    if sector is not None:
        frame = frame[frame["板块"].astype(str) == str(sector)]
    frame = frame.head(_limit(params, 50, 100))
    return [
        _record(
            subject=str(row["板块"]),
            source=SOURCES["industry.snapshot"],
            timestamp=now,
            as_of=now,
            fields=_fields(row, exclude={"板块"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _macro_gdp(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    frame = ak.macro_china_gdp_yearly().tail(_limit(params, 12, 50))
    return [
        _record(
            subject="CN_GDP_YOY",
            source=SOURCES["macro.gdp"],
            timestamp=now,
            as_of=_as_of_date(row["日期"]),
            fields=_fields(row, exclude={"日期"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _macro_shibor(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    frame = ak.macro_china_shibor_all().tail(_limit(params, 30, 100))
    return [
        _record(
            subject="SHIBOR",
            source=SOURCES["macro.shibor"],
            timestamp=now,
            as_of=_as_of_date(row["日期"]),
            fields=_fields(row, exclude={"日期"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _policy_lpr(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak
    import pandas as pd

    start_date = _date_text(params, "start_date")
    end_date = _date_text(params, "end_date")
    frame = ak.macro_china_lpr()
    trade_dates = pd.to_datetime(frame["TRADE_DATE"])
    frame = frame.loc[
        (trade_dates >= pd.to_datetime(start_date))
        & (trade_dates <= pd.to_datetime(end_date))
    ].sort_values("TRADE_DATE").tail(_limit(params, 30, 200))
    return [
        _record(
            subject="CN_LPR",
            source=SOURCES["macro.policy_lpr"],
            timestamp=now,
            as_of=_as_of_date(row["TRADE_DATE"]),
            fields=_fields(row, exclude={"TRADE_DATE"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _news(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    frame = ak.stock_news_main_cx().head(_limit(params, 20, 100))
    return [
        _record(
            subject=str(row["tag"]),
            source=SOURCES["sentiment.news"],
            timestamp=now,
            as_of=now,
            fields=_fields(row, exclude={"tag"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _announcements(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak

    symbol = _raw_symbol(params)
    start_date = _date_text(params, "start_date")
    end_date = _date_text(params, "end_date")
    frame = ak.stock_zh_a_disclosure_report_cninfo(
        symbol=symbol,
        market="沪深京",
        keyword=str(params.get("keyword", "")),
        category=str(params.get("category", "")),
        start_date=start_date,
        end_date=end_date,
    ).head(_limit(params, 20, 100))
    return [
        _record(
            subject=symbol,
            source=SOURCES["sentiment.announcements"],
            timestamp=now,
            as_of=_as_of_date(row["公告时间"]),
            fields=_fields(row, exclude={"公告时间"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _tushare_client():
    import tushare as ts

    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is required")
    return ts.pro_api(token)


def _research(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    import akshare as ak
    import pandas as pd

    ts_code = params.get("ts_code")
    if isinstance(ts_code, str) and TS_CODE.fullmatch(ts_code.upper()):
        subject = ts_code.upper()
        symbol = ts_code[:6]
    else:
        symbol = _raw_symbol(params)
        subject = symbol
    start_date = _date_text(params, "start_date")
    end_date = _date_text(params, "end_date")
    frame = ak.stock_research_report_em(symbol=symbol)
    report_dates = pd.to_datetime(frame["日期"])
    frame = frame.loc[
        (report_dates >= pd.to_datetime(start_date))
        & (report_dates <= pd.to_datetime(end_date))
    ].sort_values("日期", ascending=False).head(_limit(params, 20, 100))
    return [
        _record(
            subject=subject,
            source=SOURCES["sentiment.research"],
            timestamp=now,
            as_of=_as_of_date(row["日期"]),
            fields=_fields(row, exclude={"日期"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _tushare_daily(params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    ts_code = params.get("ts_code")
    if not isinstance(ts_code, str) or not TS_CODE.fullmatch(ts_code.upper()):
        raise ValueError("ts_code must look like 000001.SZ")
    start_date = _date_text(params, "start_date")
    end_date = _date_text(params, "end_date")
    frame = _tushare_client().daily(
        ts_code=ts_code.upper(),
        start_date=start_date,
        end_date=end_date,
    ).sort_values("trade_date").tail(_limit(params, 100, 500))
    return [
        _record(
            subject=ts_code.upper(),
            source=SOURCES["tushare.daily"],
            timestamp=now,
            as_of=_as_of_date(row["trade_date"], close_time=True),
            fields=_fields(row, exclude={"trade_date", "ts_code"}),
        )
        for row in frame.to_dict(orient="records")
    ]


def _dispatch(dataset: str, params: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    if dataset == "market.daily":
        return _market_daily(params, now)
    if dataset == "market.weekly":
        return _market_weekly(params, now)
    if dataset == "market.minute":
        return _market_minute(params, now)
    if dataset == "market.realtime":
        return _market_realtime(params, now)
    if dataset == "market.fund_flow":
        return _market_fund_flow(params, now)
    if dataset in {
        "fundamental.balance_sheet",
        "fundamental.income_statement",
        "fundamental.cash_flow",
    }:
        return _financial_statement(dataset, params, now)
    if dataset == "fundamental.indicators":
        return _financial_indicators(params, now)
    if dataset == "fundamental.valuation":
        return _valuation(params, now)
    if dataset == "macro.index":
        return _macro_index(params, now)
    if dataset == "industry.snapshot":
        return _industry_snapshot(params, now)
    if dataset == "macro.gdp":
        return _macro_gdp(params, now)
    if dataset == "macro.shibor":
        return _macro_shibor(params, now)
    if dataset == "macro.policy_lpr":
        return _policy_lpr(params, now)
    if dataset == "sentiment.news":
        return _news(params, now)
    if dataset == "sentiment.announcements":
        return _announcements(params, now)
    if dataset == "sentiment.research":
        return _research(params, now)
    if dataset == "tushare.daily":
        return _tushare_daily(params, now)
    raise ValueError(f"unsupported financial dataset: {dataset}")


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        dataset = request["dataset"]
        params = request.get("params", {})
        if not isinstance(dataset, str) or dataset not in SOURCES:
            raise ValueError("unknown dataset")
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        now = datetime.now(TZ)
        records = _dispatch(dataset, params, now)
        if not records:
            raise ValueError("provider returned no records")
        finished_at = datetime.now(TZ)
        for record in records:
            record["timestamp"] = finished_at.isoformat()
        print(
            json.dumps(
                {
                    "dataset": dataset,
                    "source": SOURCES[dataset],
                    "timestamp": finished_at.isoformat(),
                    "records": records,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    }
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
