"""Persistent customer research workspace built from immutable report archives."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

from .report_views import ReportViewRuntime
from .security_master import DEFAULT_SECURITY_MASTER, SecurityMasterError


class ResearchWorkspaceError(ValueError):
    """A workspace preference or frozen-report comparison is invalid."""


@dataclass(frozen=True)
class WorkspacePreferences:
    watchlist: tuple[str, ...] = ()
    favorite_reports: tuple[str, ...] = ()


class ResearchWorkspaceStore(Protocol):
    def load(self) -> WorkspacePreferences:
        """Return persisted customer preferences."""

    def save(self, preferences: WorkspacePreferences) -> None:
        """Replace customer preferences atomically."""


class InMemoryResearchWorkspaceStore:
    """Small test adapter with the same observable contract as the JSON store."""

    def __init__(
        self,
        symbols: list[str] | None = None,
        favorite_reports: list[str] | None = None,
    ) -> None:
        self._preferences = WorkspacePreferences(
            tuple(symbols or []), tuple(favorite_reports or [])
        )
        self._lock = RLock()

    def load(self) -> WorkspacePreferences:
        with self._lock:
            return self._preferences

    def save(self, preferences: WorkspacePreferences) -> None:
        with self._lock:
            self._preferences = preferences


class JsonResearchWorkspaceStore:
    """Local single-user adapter for non-sensitive workspace preferences."""

    SCHEMA_VERSION = 2

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def load(self) -> WorkspacePreferences:
        with self._lock:
            if not self._path.exists():
                return WorkspacePreferences()
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                version = payload.get("schema_version")
                if version not in {1, self.SCHEMA_VERSION}:
                    raise ResearchWorkspaceError("自选股配置版本无法识别。")
                symbols = payload.get("watchlist", [])
                favorites = payload.get("favorite_reports", []) if version == 2 else []
                if not _is_string_list(symbols) or not _is_string_list(favorites):
                    raise ResearchWorkspaceError("自选股配置内容无效。")
                return WorkspacePreferences(tuple(symbols), tuple(favorites))
            except (OSError, json.JSONDecodeError) as error:
                raise ResearchWorkspaceError(f"无法读取自选股配置: {error}") from error

    def save(self, preferences: WorkspacePreferences) -> None:
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "watchlist": list(preferences.watchlist),
            "favorite_reports": list(preferences.favorite_reports),
        }
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self._path.with_suffix(self._path.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(self._path)
            except OSError as error:
                raise ResearchWorkspaceError(f"无法保存自选股配置: {error}") from error


class ReportCatalog(Protocol):
    def list_reports(self, *, limit: int = 12) -> list[dict[str, Any]]:
        """Return frozen report summaries."""

    def get_report(self, report_id: str) -> dict[str, Any]:
        """Return one complete frozen report archive."""


class ResearchWorkspaceRuntime:
    """One interface for watchlists and comparisons of immutable reports."""

    VALID_VIEWS = frozenset({"basic", "professional"})
    MAX_WATCHLIST_SIZE = 20
    MAX_FAVORITE_REPORTS = 50
    LIVE_EXPIRY = timedelta(days=7)

    def __init__(
        self,
        repository: ReportCatalog,
        report_views: ReportViewRuntime,
        store: ResearchWorkspaceStore,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._report_views = report_views
        self._store = store
        self._now = now or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self._lock = RLock()

    def snapshot(self) -> dict[str, Any]:
        preferences = self._clean_preferences(self._store.load())
        symbols = list(preferences.watchlist)
        reports = self._repository.list_reports(limit=50)
        report_ids = {str(item.get("report_id", "")) for item in reports}
        favorites = [
            report_id
            for report_id in preferences.favorite_reports
            if report_id in report_ids
        ]
        if tuple(favorites) != preferences.favorite_reports:
            preferences = WorkspacePreferences(tuple(symbols), tuple(favorites))
            self._store.save(preferences)
        return {
            "watchlist": [self._security(symbol) for symbol in symbols],
            "watchlist_count": len(symbols),
            "watchlist_limit": self.MAX_WATCHLIST_SIZE,
            "reports": [
                {
                    **deepcopy(report),
                    "in_watchlist": report.get("symbol") in symbols,
                    "favorite": report.get("report_id") in favorites,
                    "state": self._report_state(
                        mode=report.get("mode"),
                        as_of=report.get("as_of"),
                        health=report.get("data_health"),
                    ),
                }
                for report in reports
            ],
            "report_count": len(reports),
            "favorite_count": len(favorites),
            "comparison_ready": len(reports) >= 2,
            "frozen_data_only": True,
        }

    def toggle_watchlist(self, symbol: str) -> dict[str, Any]:
        normalized = str(symbol).strip().lower()
        try:
            DEFAULT_SECURITY_MASTER.get(normalized)
        except SecurityMasterError as error:
            raise ResearchWorkspaceError("当前股票不在已验证的客户分析目录中。") from error
        with self._lock:
            preferences = self._clean_preferences(self._store.load())
            symbols = list(preferences.watchlist)
            if normalized in symbols:
                symbols.remove(normalized)
                added = False
            else:
                if len(symbols) >= self.MAX_WATCHLIST_SIZE:
                    raise ResearchWorkspaceError(
                        f"自选股最多保存 {self.MAX_WATCHLIST_SIZE} 只。"
                    )
                symbols.append(normalized)
                added = True
            self._store.save(
                WorkspacePreferences(tuple(symbols), preferences.favorite_reports)
            )
            return {
                "symbol": normalized,
                "added": added,
                "message": "已加入自选。" if added else "已移出自选。",
                "workspace": self.snapshot(),
            }

    def toggle_favorite(self, report_id: str) -> dict[str, Any]:
        normalized = str(report_id).strip()
        if not normalized:
            raise ResearchWorkspaceError("请选择要收藏的报告。")
        self._repository.get_report(normalized)
        with self._lock:
            preferences = self._clean_preferences(self._store.load())
            favorites = list(preferences.favorite_reports)
            if normalized in favorites:
                favorites.remove(normalized)
                added = False
            else:
                if len(favorites) >= self.MAX_FAVORITE_REPORTS:
                    raise ResearchWorkspaceError(
                        f"最多收藏 {self.MAX_FAVORITE_REPORTS} 份报告。"
                    )
                favorites.append(normalized)
                added = True
            self._store.save(
                WorkspacePreferences(preferences.watchlist, tuple(favorites))
            )
            return {
                "report_id": normalized,
                "added": added,
                "message": "报告已收藏。" if added else "已取消报告收藏。",
                "workspace": self.snapshot(),
            }

    def compare(
        self, left_report_id: str, right_report_id: str, *, view: str = "basic"
    ) -> dict[str, Any]:
        normalized_view = self._validate_view(view)
        left_id = str(left_report_id).strip()
        right_id = str(right_report_id).strip()
        if not left_id or not right_id:
            raise ResearchWorkspaceError("请选择两份已保存的报告。")
        if left_id == right_id:
            raise ResearchWorkspaceError("请选择两份不同的报告。")

        left = self._report_views.project(left_id, normalized_view)
        right = self._report_views.project(right_id, normalized_view)
        same_security = (
            left["shared"]["security"]["symbol"]
            == right["shared"]["security"]["symbol"]
        )
        comparison = {
            "view": normalized_view,
            "kind": "same_security_change" if same_security else "cross_security",
            "kind_label": "同一股票前后变化" if same_security else "两只股票横向比较",
            "headline": self._headline(left, right, same_security=same_security),
            "notice": (
                "两份报告均使用保存时的冻结数据；本次比较没有重新取数，也没有调用大模型。"
            ),
            "left": self._comparison_card(left),
            "right": self._comparison_card(right),
            "changes": self._changes(left, right, same_security=same_security),
            "change_reasons": self._change_reasons(
                left, right, same_security=same_security
            ),
            "frozen_data_only": True,
            "model_called": False,
        }
        if normalized_view == "professional":
            comparison["professional"] = {
                "dimension_changes": self._dimension_changes(left, right),
                "left_sources": deepcopy(left["professional"]["sources"]),
                "right_sources": deepcopy(right["professional"]["sources"]),
                "calculation_note": (
                    "差值由冻结报告中的确定性数值计算；不同股票的差值仅用于并排研究，"
                    "不代表收益预测。"
                ),
            }
        return comparison

    def export_report(self, report_id: str, *, view: str = "basic") -> dict[str, Any]:
        normalized_view = self._validate_view(view)
        projection = self._report_views.project(str(report_id).strip(), normalized_view)
        security = projection["shared"]["security"]
        return {
            "filename": f"{security['symbol']}_report_{normalized_view}.html",
            "content_type": "text/html; charset=utf-8",
            "content": self._render_report_export(projection),
            "view": normalized_view,
            "frozen_data_only": True,
            "model_called": False,
        }

    def export_comparison(
        self, left_report_id: str, right_report_id: str, *, view: str = "basic"
    ) -> dict[str, Any]:
        comparison = self.compare(left_report_id, right_report_id, view=view)
        return {
            "filename": (
                f"{comparison['left']['symbol']}_vs_"
                f"{comparison['right']['symbol']}_{comparison['view']}.html"
            ),
            "content_type": "text/html; charset=utf-8",
            "content": self._render_comparison_export(comparison),
            "view": comparison["view"],
            "frozen_data_only": True,
            "model_called": False,
        }

    @classmethod
    def _clean_preferences(
        cls, preferences: WorkspacePreferences
    ) -> WorkspacePreferences:
        symbols = cls._clean_symbols(list(preferences.watchlist))
        favorites: list[str] = []
        for value in preferences.favorite_reports:
            normalized = str(value).strip()
            if normalized and normalized not in favorites:
                favorites.append(normalized)
        return WorkspacePreferences(
            tuple(symbols), tuple(favorites[: cls.MAX_FAVORITE_REPORTS])
        )

    @classmethod
    def _clean_symbols(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        for value in values:
            normalized = str(value).strip().lower()
            try:
                DEFAULT_SECURITY_MASTER.get(normalized)
            except SecurityMasterError:
                continue
            if normalized not in output:
                output.append(normalized)
        return output[: cls.MAX_WATCHLIST_SIZE]

    @classmethod
    def _validate_view(cls, view: str) -> str:
        normalized = str(view).strip().lower()
        if normalized not in cls.VALID_VIEWS:
            raise ResearchWorkspaceError("view 只允许 basic 或 professional。")
        return normalized

    def _report_state(
        self, *, mode: Any, as_of: Any, health: Any
    ) -> dict[str, Any]:
        normalized_mode = str(mode or "")
        if normalized_mode == "offline":
            freshness = {
                "status": "snapshot",
                "label": "历史验证快照",
                "note": "用于稳定复现，不代表最新市场状态。",
            }
        else:
            parsed = _parse_datetime(as_of)
            now = self._now()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ResearchWorkspaceError("工作台时钟必须包含时区。")
            expired = parsed is not None and now - parsed > self.LIVE_EXPIRY
            freshness = (
                {
                    "status": "expired",
                    "label": "数据已过期",
                    "note": "这份报告仍可追溯，但不应当作当前市场结论。",
                }
                if expired
                else {
                    "status": "current" if parsed is not None else "unknown",
                    "label": "数据时点有效" if parsed is not None else "数据时间未知",
                    "note": "请结合页面显示的数据时间阅读。",
                }
            )

        health_value = health if isinstance(health, Mapping) else {}
        available = _optional_int(health_value.get("available_count"))
        total = _optional_int(health_value.get("dataset_count"))
        unavailable = _optional_int(health_value.get("unavailable_count")) or 0
        degraded_count = _optional_int(health_value.get("degraded_count")) or 0
        if total is None or available is None:
            availability = {
                "status": "unknown",
                "label": "数据完整度未知",
                "note": "旧报告未保存完整的数据健康摘要。",
            }
        elif available < total or unavailable > 0:
            availability = {
                "status": "partial",
                "label": f"部分数据可用 {available}/{total}",
                "note": "缺失项已按原报告规则降级，结论不会伪装成完整数据。",
            }
        elif bool(health_value.get("degraded")) or degraded_count > 0:
            availability = {
                "status": "degraded",
                "label": "来源已降级",
                "note": "数据完整，但部分来源使用备用源或历史缓存。",
            }
        else:
            availability = {
                "status": "complete",
                "label": f"数据完整 {available}/{total}",
                "note": "报告所需数据在保存时均可用。",
            }
        return {"freshness": freshness, "availability": availability}

    @staticmethod
    def _security(symbol: str) -> dict[str, str]:
        value = DEFAULT_SECURITY_MASTER.get(symbol)
        return {
            "symbol": symbol,
            "code": value.code,
            "name": value.name,
            "exchange": value.exchange,
            "industry": value.industry,
        }

    def _comparison_card(self, projection: Mapping[str, Any]) -> dict[str, Any]:
        shared = projection["shared"]
        security = shared["security"]
        return {
            "report_id": projection["report_id"],
            "report_version": projection["report_version"],
            "name": security["name"],
            "symbol": security["symbol"],
            "code": security["code"],
            "archived_at": shared.get("archived_at"),
            "as_of": shared["data"]["as_of"],
            "data_label": shared["data"]["label"],
            "snapshot_id": shared["data"].get("snapshot_id"),
            "verdict": deepcopy(shared["verdict"]),
            "latest_close": shared["quote"]["latest_close"],
            "price_band": deepcopy(shared["price_band"]),
            "risk": deepcopy(shared["risk"]),
            "summary": projection["basic"]["headline"],
            "support": deepcopy(projection["basic"]["support"]),
            "risk_summary": deepcopy(projection["basic"]["risk"]),
            "state": self._report_state(
                mode=shared["data"].get("mode"),
                as_of=shared["data"].get("as_of"),
                health=shared["data"].get("health"),
            ),
            "credibility": deepcopy(projection["basic"].get("credibility", {})),
        }

    def _render_report_export(self, projection: Mapping[str, Any]) -> str:
        shared = projection["shared"]
        basic = projection["basic"]
        security = shared["security"]
        data = shared["data"]
        state = self._report_state(
            mode=data.get("mode"), as_of=data.get("as_of"), health=data.get("health")
        )
        credibility = basic.get("credibility", {})
        guide = "".join(
            "<article><small>{}</small><h3>{}</h3><p>{}</p></article>".format(
                escape(str(item["label"])),
                escape(str(item["answer"])),
                escape(str(item["detail"])),
            )
            for item in basic["guide"]
        )
        professional = projection.get("professional")
        professional_html = ""
        if isinstance(professional, Mapping):
            dimensions = "".join(
                "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    escape(str(item["name"])),
                    escape(str(item["score"])),
                    escape(str(item["label"])),
                    escape(str(item["summary"])),
                )
                for item in professional["dimensions"]
            )
            sources = "".join(
                f"<li>{escape(str(item))}</li>" for item in professional["sources"]
            )
            risk = shared["risk"]
            provenance = professional.get("provenance", {})
            quality = provenance.get("quality", {}) if isinstance(provenance, Mapping) else {}
            identity = provenance.get("identity", {}) if isinstance(provenance, Mapping) else {}
            professional_html = f"""
            <section><p class="eyebrow">专业证据</p><h2>四个研究维度</h2>
              <table><thead><tr><th>维度</th><th>分数</th><th>判断</th><th>摘要</th></tr></thead>
              <tbody>{dimensions}</tbody></table>
            </section>
            <section><p class="eyebrow">风险边界</p><h2>确定性风险结果</h2>
              <dl class="metrics">
                <div><dt>状态</dt><dd>{escape(str(risk.get('status', '—')))}</dd></div>
                <div><dt>计划仓位上限</dt><dd>{escape(str(risk.get('position_cap_percent', '—')))}%</dd></div>
                <div><dt>预计单次亏损</dt><dd>{escape(str(risk.get('estimated_loss_percent', '—')))}%</dd></div>
                <div><dt>风险收益比</dt><dd>{escape(str(risk.get('reward_risk_ratio', '—')))}</dd></div>
              </dl>
            </section>
            <section><p class="eyebrow">证据来源</p><h2>本次报告引用</h2><ul>{sources}</ul></section>
            <section><p class="eyebrow">可复现性</p><h2>{escape(str(quality.get('overall_status', 'unknown')))}</h2>
              <p>{escape(str(quality.get('comparison_note', '—')))}</p>
              <p class="stamp">运行指纹 {escape(str(provenance.get('fingerprint') or '历史报告未保存'))}</p>
              <p class="stamp">快照 {escape(str(identity.get('snapshot_id', 'unknown')))} · 证券主数据 {escape(str(identity.get('security_master_version', 'unknown')))} · 代码 {escape(str(identity.get('code_version', 'unknown')))}</p>
            </section>
            """
        body = f"""
        <header>
          <p class="eyebrow">冻结研究报告 · {escape(str(projection['view']))}</p>
          <h1>{escape(str(security['name']))} <span>{escape(str(security['code']))}</span></h1>
          <p class="lead">{escape(str(basic['headline']))}</p>
          <div class="stamp">数据 {escape(str(data['as_of']))} · 报告 {escape(str(shared.get('archived_at') or '—'))}</div>
        </header>
        <section class="state-line">
          <strong>{escape(state['freshness']['label'])}</strong><span>{escape(state['freshness']['note'])}</span>
          <strong>{escape(state['availability']['label'])}</strong><span>{escape(state['availability']['note'])}</span>
        </section>
        <section><p class="eyebrow">本次分析可信度</p><h2>{escape(str(credibility.get('label', '数据状态未知')))}</h2>
          <p>{escape(str(credibility.get('summary', '—')))}</p>
          <p class="stamp">数据对应时间 {escape(str(credibility.get('as_of') or '—'))}</p>
        </section>
        <section><p class="eyebrow">研究摘要</p><div class="guide">{guide}</div></section>
        <section><p class="eyebrow">价格与结论</p><h2>{escape(str(shared['verdict']['label']))} · {escape(str(shared['verdict']['action_label']))}</h2>
          <dl class="metrics">
            <div><dt>参考收盘价</dt><dd>{escape(str(shared['quote']['latest_close']))}</dd></div>
            <div><dt>研究区间</dt><dd>{escape(str(shared['price_band']['lower']))}–{escape(str(shared['price_band']['upper']))}</dd></div>
            <div><dt>判断把握度</dt><dd>{escape(str(shared['verdict']['confidence']))}%</dd></div>
          </dl>
        </section>
        {professional_html}
        <footer>{escape(str(shared['safety']['notice']))} 报告来自已保存数据，导出时未重新取数或调用大模型。</footer>
        """
        return _html_document(
            f"{security['name']}研究报告", body, view=str(projection["view"])
        )

    def _render_comparison_export(self, comparison: Mapping[str, Any]) -> str:
        def card(item: Mapping[str, Any]) -> str:
            state = item["state"]
            risk = item["risk"]
            professional_risk = ""
            if comparison["view"] == "professional":
                professional_risk = f"""
                <div><dt>计划仓位上限</dt><dd>{escape(str(risk.get('position_cap_percent', '—')))}%</dd></div>
                <div><dt>预计单次亏损</dt><dd>{escape(str(risk.get('estimated_loss_percent', '—')))}%</dd></div>
                """
            return f"""
            <article class="compare-card">
              <small>数据 {escape(str(item['as_of']))}</small>
              <h2>{escape(str(item['name']))} <span>{escape(str(item['code']))}</span></h2>
              <strong>{escape(str(item['verdict']['label']))} · {escape(str(item['verdict']['action_label']))}</strong>
              <p>{escape(str(item['support']['summary']))}</p>
              <dl class="metrics">
                <div><dt>参考收盘价</dt><dd>{escape(str(item['latest_close']))}</dd></div>
                <div><dt>研究区间</dt><dd>{escape(str(item['price_band']['lower']))}–{escape(str(item['price_band']['upper']))}</dd></div>
                <div><dt>判断把握度</dt><dd>{escape(str(item['verdict']['confidence']))}%</dd></div>
                {professional_risk}
              </dl>
              <p class="state-note">{escape(state['freshness']['label'])} · {escape(state['availability']['label'])}</p>
            </article>
            """

        changes = "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                escape(str(item["label"])),
                escape(str(item.get("left", "—"))),
                escape(str(item.get("right", "—"))),
                escape(
                    str(
                        item.get("delta")
                        if item.get("delta") is not None
                        else item.get("note", "发生变化" if item.get("changed") else "保持一致")
                    )
                ),
            )
            for item in comparison["changes"]
        )
        reasons = "".join(
            f"<li><strong>{escape(str(item.get('label', '变化原因')))}</strong>"
            f"<span>{escape(str(item.get('detail', '')))}</span></li>"
            for item in comparison.get("change_reasons", [])
        )
        professional_html = ""
        professional = comparison.get("professional")
        if isinstance(professional, Mapping):
            dimensions = "".join(
                f"<li><span>{escape(str(item['label']))}</span><strong>{escape(str(item.get('delta')))} {escape(str(item['unit']))}</strong></li>"
                for item in professional["dimension_changes"]
            )
            left_sources = "".join(
                f"<li>{escape(str(item))}</li>" for item in professional["left_sources"]
            )
            right_sources = "".join(
                f"<li>{escape(str(item))}</li>" for item in professional["right_sources"]
            )
            professional_html = f"""
            <section><p class="eyebrow">专业维度</p><ul class="dimension-list">{dimensions}</ul></section>
            <section class="source-columns"><div><h3>左侧证据来源</h3><ul>{left_sources}</ul></div>
              <div><h3>右侧证据来源</h3><ul>{right_sources}</ul></div></section>
            """
        body = f"""
        <header><p class="eyebrow">冻结报告比较 · {escape(str(comparison['view']))}</p>
          <h1>{escape(str(comparison['kind_label']))}</h1><p class="lead">{escape(str(comparison['headline']))}</p>
        </header>
        <section class="compare-grid">{card(comparison['left'])}{card(comparison['right'])}</section>
        <section><p class="eyebrow">为什么不同</p><ul>{reasons}</ul></section>
        <section><p class="eyebrow">主要变化</p><table><thead><tr><th>项目</th><th>左侧</th><th>右侧</th><th>差异</th></tr></thead><tbody>{changes}</tbody></table></section>
        {professional_html}
        <footer>{escape(str(comparison['notice']))} 不构成投资建议。</footer>
        """
        return _html_document("冻结研究报告比较", body, view=str(comparison["view"]))

    @staticmethod
    def _headline(
        left: Mapping[str, Any], right: Mapping[str, Any], *, same_security: bool
    ) -> str:
        left_name = left["shared"]["security"]["name"]
        right_name = right["shared"]["security"]["name"]
        left_label = left["shared"]["verdict"]["label"]
        right_label = right["shared"]["verdict"]["label"]
        if same_security:
            if left_label == right_label:
                return f"{left_name}两次报告的综合判断保持为“{right_label}”。"
            return f"{left_name}的综合判断由“{left_label}”变为“{right_label}”。"
        return f"{left_name}为“{left_label}”，{right_name}为“{right_label}”。"

    @classmethod
    def _changes(
        cls,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
        *,
        same_security: bool,
    ) -> list[dict[str, Any]]:
        left_shared = left["shared"]
        right_shared = right["shared"]
        values = [
            (
                "confidence",
                "判断把握度",
                left_shared["verdict"].get("confidence"),
                right_shared["verdict"].get("confidence"),
                "个百分点",
            ),
        ]
        if same_security:
            values.insert(
                0,
                (
                    "latest_close",
                    "报告参考收盘价",
                    left_shared["quote"].get("latest_close"),
                    right_shared["quote"].get("latest_close"),
                    "元",
                ),
            )
        output = [cls._numeric_change(*item) for item in values]
        output.append(
            {
                "id": "verdict",
                "label": "综合判断",
                "left": left_shared["verdict"].get("label"),
                "right": right_shared["verdict"].get("label"),
                "changed": left_shared["verdict"].get("label")
                != right_shared["verdict"].get("label"),
                "comparable": True,
            }
        )
        if not same_security:
            output.append(
                {
                    "id": "price_warning",
                    "label": "价格说明",
                    "left": left_shared["quote"].get("latest_close"),
                    "right": right_shared["quote"].get("latest_close"),
                    "changed": None,
                    "comparable": False,
                    "note": "不同股票的价格高低不能直接代表优劣。",
                }
            )
        return output

    @staticmethod
    def _change_reasons(
        left: Mapping[str, Any],
        right: Mapping[str, Any],
        *,
        same_security: bool,
    ) -> list[dict[str, Any]]:
        if not same_security:
            return [
                {
                    "id": "different_security",
                    "label": "标的不同",
                    "detail": "两份报告属于不同证券，价格、行业和结论差异不能解释为同一股票的前后变化。",
                }
            ]

        left_shared = left["shared"]
        right_shared = right["shared"]
        left_data = left_shared["data"]
        right_data = right_shared["data"]
        left_provenance = left_shared.get("provenance", {})
        right_provenance = right_shared.get("provenance", {})
        left_quality = left_provenance.get("quality", {})
        right_quality = right_provenance.get("quality", {})
        left_identity = left_provenance.get("identity", {})
        right_identity = right_provenance.get("identity", {})
        reasons: list[dict[str, Any]] = []

        if (
            left_data.get("as_of") != right_data.get("as_of")
            or left_data.get("snapshot_id") != right_data.get("snapshot_id")
        ):
            reasons.append(
                {
                    "id": "market_data_changed",
                    "label": "行情或数据时间变化",
                    "detail": (
                        f"左侧数据时间为 {left_data.get('as_of') or '未知'}，右侧为 "
                        f"{right_data.get('as_of') or '未知'}；快照编号也会影响可比性。"
                    ),
                }
            )
        if (
            left_quality.get("overall_status") != right_quality.get("overall_status")
            or left_quality.get("comparison_ready") != right_quality.get("comparison_ready")
        ):
            reasons.append(
                {
                    "id": "data_source_status_changed",
                    "label": "数据源状态变化",
                    "detail": (
                        f"左侧为 {left_quality.get('overall_status', '未知')}，右侧为 "
                        f"{right_quality.get('overall_status', '未知')}；需区分真实变化和降级影响。"
                    ),
                }
            )
        if left_identity.get("security_master_version") != right_identity.get(
            "security_master_version"
        ):
            reasons.append(
                {
                    "id": "security_master_changed",
                    "label": "证券主数据变化",
                    "detail": "证券名称、行业或可用数据目录可能来自不同版本。",
                }
            )
        if any(
            left_identity.get(key) != right_identity.get(key)
            for key in ("code_version", "config_version", "report_version")
        ):
            reasons.append(
                {
                    "id": "deterministic_rules_changed",
                    "label": "确定性规则变化",
                    "detail": "两次运行使用的代码、配置或报告版本不同，数值差异不能只归因于行情。",
                }
            )
        if left_identity.get("model_policy_version") != right_identity.get(
            "model_policy_version"
        ):
            reasons.append(
                {
                    "id": "model_explanation_changed",
                    "label": "大模型解释变化",
                    "detail": "两次运行使用的模型治理策略版本不同，解释文字可能发生变化；确定性指标仍需单独核对。",
                }
            )
        if not reasons:
            reasons.append(
                {
                    "id": "no_known_change",
                    "label": "未发现已记录的版本变化",
                    "detail": "当前运行指纹没有显示输入或版本变化，建议继续查看完整报告内容。",
                }
            )
        return reasons

    @staticmethod
    def _numeric_change(
        identifier: str, label: str, left: Any, right: Any, unit: str
    ) -> dict[str, Any]:
        try:
            delta = Decimal(str(right)) - Decimal(str(left))
            delta_text = format(delta, "f")
            if delta > 0:
                delta_text = f"+{delta_text}"
        except (InvalidOperation, TypeError, ValueError):
            delta_text = None
        return {
            "id": identifier,
            "label": label,
            "left": left,
            "right": right,
            "delta": delta_text,
            "unit": unit,
            "changed": str(left) != str(right),
            "comparable": delta_text is not None,
        }

    @classmethod
    def _dimension_changes(
        cls, left: Mapping[str, Any], right: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        left_dimensions = {
            item["id"]: item for item in left["professional"]["dimensions"]
        }
        right_dimensions = {
            item["id"]: item for item in right["professional"]["dimensions"]
        }
        output = []
        for identifier in ("technical", "fundamental", "industry", "macro"):
            left_item = left_dimensions[identifier]
            right_item = right_dimensions[identifier]
            output.append(
                cls._numeric_change(
                    identifier,
                    str(left_item["name"]),
                    left_item["score"],
                    right_item["score"],
                    "分",
                )
            )
        return output


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _html_document(title: str, body: str, *, view: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="generator" content="Agent Platform frozen report exporter">
  <title>{escape(title)}</title>
  <style>
    :root{{--ink:#10283d;--muted:#64737d;--line:#d8e0e4;--paper:#f5f7f8;--red:#c94837;--green:#147663}}
    *{{box-sizing:border-box}} body{{max-width:980px;margin:0 auto;padding:42px;background:var(--paper);color:var(--ink);font-family:"Microsoft YaHei UI",sans-serif;line-height:1.7}}
    header,section,footer{{margin:0 0 18px;padding:24px;background:#fff;border:1px solid var(--line)}}
    header{{border-top:6px solid var(--red)}} h1{{margin:6px 0;font-size:34px}} h1 span,h2 span{{color:var(--muted);font-size:.55em;font-family:Consolas,monospace}}
    h2{{margin:5px 0 14px}} h3{{margin:5px 0 8px}} .eyebrow{{margin:0;color:var(--red);font:bold 11px Consolas,monospace;letter-spacing:.12em}}
    .lead{{font-size:18px;font-weight:700}} .stamp,.state-note,small{{color:var(--muted);font-size:12px}}
    .state-line{{display:grid;grid-template-columns:auto 1fr;gap:8px 18px}} .state-line strong{{color:var(--green)}} .state-line span{{color:var(--muted)}}
    .guide,.compare-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}} .guide article,.compare-card{{padding:16px;border:1px solid var(--line)}} .guide p,.compare-card p{{color:var(--muted)}}
    .metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line)}} .metrics div{{padding:12px;background:#f8fafb}} dt{{color:var(--muted);font-size:12px}} dd{{margin:3px 0 0;font-weight:800}}
    table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{padding:10px;border:1px solid var(--line);text-align:left;vertical-align:top}} th{{background:#edf2f4}}
    .dimension-list{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:0;list-style:none}} .dimension-list li{{padding:12px;background:#edf2f4}} .dimension-list span,.dimension-list strong{{display:block}}
    .source-columns{{display:grid;grid-template-columns:1fr 1fr;gap:20px}} footer{{color:var(--muted);font-size:12px;border-left:4px solid var(--green)}}
    @media(max-width:680px){{body{{padding:14px}}.guide,.compare-grid,.source-columns{{grid-template-columns:1fr}}.metrics,.dimension-list{{grid-template-columns:1fr 1fr}}}}
    @media print{{body{{max-width:none;padding:0;background:#fff}}header,section,footer{{break-inside:avoid}}}}
  </style>
</head>
<body data-view="{escape(view)}">{body}</body>
</html>
"""


__all__ = [
    "InMemoryResearchWorkspaceStore",
    "JsonResearchWorkspaceStore",
    "ResearchWorkspaceError",
    "ResearchWorkspaceRuntime",
    "ResearchWorkspaceStore",
    "WorkspacePreferences",
]
