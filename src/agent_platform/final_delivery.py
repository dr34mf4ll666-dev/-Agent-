"""One stable interface for the final D4 project acceptance workflow."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence


class FinalDeliveryError(ValueError):
    """The final delivery configuration or local evidence is invalid."""


@dataclass(frozen=True)
class CommandExecution:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


class CommandRunner(Protocol):
    def run(self, command: Sequence[str], *, cwd: Path) -> CommandExecution:
        """Run one local acceptance entry and capture its result."""


class SubprocessCommandRunner:
    """Production adapter for the local process seam."""

    def run(self, command: Sequence[str], *, cwd: Path) -> CommandExecution:
        started = time.perf_counter()
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )
        return CommandExecution(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )


@dataclass(frozen=True)
class FinalDeliveryReport:
    environment: tuple[dict[str, Any], ...]
    workflows: tuple[dict[str, Any], ...]
    documents: tuple[dict[str, Any], ...]
    paper_evidence: dict[str, Any]
    duration_requirement: dict[str, Any]

    @property
    def passed(self) -> bool:
        return (
            all(item["passed"] for item in self.environment)
            and all(item["passed"] for item in self.workflows)
            and all(item["passed"] for item in self.documents)
            and self.duration_requirement["accepted_for_revised_scope"] is True
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": "final_delivery_completed" if self.passed else "failed",
            "environment": [dict(item) for item in self.environment],
            "workflows": [dict(item) for item in self.workflows],
            "documents": [dict(item) for item in self.documents],
            "paper_evidence": dict(self.paper_evidence),
            "duration_requirement": dict(self.duration_requirement),
            "safety": {
                "simulation_only": True,
                "order_created": False,
                "real_trading_allowed": False,
            },
            "passed": self.passed,
        }


@dataclass(frozen=True)
class _WorkflowSpec:
    name: str
    script: str
    arguments: tuple[str, ...]
    required_markers: tuple[str, ...]
    summary_prefixes: tuple[str, ...]


WORKFLOWS = (
    _WorkflowSpec(
        name="通用 Harness",
        script="demo_echo.py",
        arguments=("--task", "final delivery verification"),
        required_markers=("preflight.passed", "postflight.passed"),
        summary_prefixes=("agent:", "output:"),
    ),
    _WorkflowSpec(
        name="C3 完整金融 Graph",
        script="demo_financial_graph.py",
        arguments=("--confirm",),
        required_markers=(
            "C3 最终标准化金融分析报告",
            "real_trading_allowed=false",
        ),
        summary_prefixes=("- 决策状态:", "- 批准动作:", "- 仓位:"),
    ),
    _WorkflowSpec(
        name="D1 固定回测",
        script="demo_backtest_experiment.py",
        arguments=(),
        required_markers=("总体结果: 通过", "real_trading_allowed=false"),
        summary_prefixes=(
            "- 组合收益率:",
            "- 最大回撤:",
            "- 年化夏普:",
            "- 沪深300收益率:",
        ),
    ),
    _WorkflowSpec(
        name="D2/D3 Harness 工程验收",
        script="demo_d2_engineering.py",
        arguments=(),
        required_markers=("结论: D2 验收通过", "幻觉率"),
        summary_prefixes=("用例=", "阈值=", "结论:"),
    ),
    _WorkflowSpec(
        name="D4 本地模拟执行",
        script="demo_paper_trading.py",
        arguments=("--confirm",),
        required_markers=("D4 持续模拟运行", "real_trading_allowed=false"),
        summary_prefixes=(
            "本次状态:",
            "数据模式:",
            "C3 决策:",
            "账户现金:",
        ),
    ),
)


REQUIRED_DOCUMENTS = (
    ("最终总交付文档", "docs/final-delivery.md"),
    ("架构图", "docs/architecture.md"),
    ("Graph Schema", "docs/financial-graph.md"),
    ("金融数据字典", "docs/finance-data-contract.md"),
    ("运行手册", "README.md"),
    ("回测报告", "docs/backtest.md"),
    ("Harness 对比报告", "docs/d2-harness-engineering.md"),
    ("模拟运行复盘", "docs/paper-trading.md"),
)


class FinalDeliveryRuntime:
    """Deep D4 module: environment, workflows, documents, and final status."""

    def __init__(
        self,
        *,
        project_root: str | Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._root = Path(project_root).resolve()
        self._runner = command_runner or SubprocessCommandRunner()

    @classmethod
    def from_project(
        cls,
        project_root: str | Path | None = None,
        *,
        command_runner: CommandRunner | None = None,
    ) -> "FinalDeliveryRuntime":
        root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )
        return cls(project_root=root, command_runner=command_runner)

    def run(self) -> FinalDeliveryReport:
        environment = self._check_environment()
        documents = self._check_documents()
        workflows = tuple(self._run_workflow(spec) for spec in WORKFLOWS)
        return FinalDeliveryReport(
            environment=environment,
            workflows=workflows,
            documents=documents,
            paper_evidence=self._read_local_paper_evidence(),
            duration_requirement={
                "original_requirement": "真实行情连续运行 1-2 周",
                "observed_live_trading_days": self._observed_live_days(),
                "proof_status": "waived_not_proven",
                "waived_by_user": True,
                "waived_at": "2026-08-11",
                "accepted_for_revised_scope": True,
                "interpretation": (
                    "D4 engineering and delivery are accepted without waiting for elapsed "
                    "calendar time; long-run stability is not claimed"
                ),
            },
        )

    def _check_environment(self) -> tuple[dict[str, Any], ...]:
        checks: list[tuple[str, bool, str]] = []
        checks.append(
            (
                "Python 版本",
                sys.version_info >= (3, 11),
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            )
        )
        checks.append(
            (
                "项目元数据",
                (self._root / "pyproject.toml").is_file(),
                "pyproject.toml",
            )
        )
        checks.append(
            (
                "锁定依赖",
                (self._root / "requirements.lock").is_file(),
                "requirements.lock",
            )
        )
        checks.append(
            (
                "离线金融 fixture",
                (self._root / "tests/fixtures/financial_data_hub.json").is_file(),
                "tests/fixtures/financial_data_hub.json",
            )
        )
        safety_path = self._root / ".env.example"
        safety_text = (
            safety_path.read_text(encoding="utf-8") if safety_path.is_file() else ""
        )
        checks.append(
            (
                "真实交易默认关闭",
                "ALLOW_LIVE_TRADING=false" in safety_text,
                ".env.example: ALLOW_LIVE_TRADING=false",
            )
        )
        gitignore_path = self._root / ".gitignore"
        gitignore = (
            gitignore_path.read_text(encoding="utf-8")
            if gitignore_path.is_file()
            else ""
        )
        checks.append(
            (
                "密钥和运行账本不提交",
                ".env" in gitignore and ".runtime/" in gitignore,
                ".gitignore excludes .env and .runtime/",
            )
        )
        return tuple(
            {"name": name, "passed": passed, "detail": detail}
            for name, passed, detail in checks
        )

    def _check_documents(self) -> tuple[dict[str, Any], ...]:
        required_sections = (
            "## 1. 最终架构图",
            "## 2. Graph Schema",
            "## 3. Agent 卡片",
            "## 4. 数据字典",
            "## 5. 运行手册",
            "## 6. 回测报告",
            "## 7. Harness 对比实验报告",
            "## 8. 模拟运行复盘",
        )
        results = []
        for name, relative in REQUIRED_DOCUMENTS:
            path = self._root / relative
            passed = path.is_file() and path.stat().st_size > 0
            detail = relative
            if relative == "docs/final-delivery.md" and passed:
                content = path.read_text(encoding="utf-8")
                missing = [item for item in required_sections if item not in content]
                passed = not missing
                detail = relative if passed else "missing sections: " + ", ".join(missing)
            results.append({"name": name, "path": relative, "passed": passed, "detail": detail})
        return tuple(results)

    def _run_workflow(self, spec: _WorkflowSpec) -> dict[str, Any]:
        command = (
            sys.executable,
            str(self._root / "Scripts" / spec.script),
            *spec.arguments,
        )
        execution = self._runner.run(command, cwd=self._root)
        missing = [
            marker for marker in spec.required_markers if marker not in execution.stdout
        ]
        passed = execution.returncode == 0 and not missing
        summaries = [
            line.strip()
            for line in execution.stdout.splitlines()
            if any(line.strip().startswith(prefix) for prefix in spec.summary_prefixes)
        ]
        return {
            "name": spec.name,
            "command": f"{spec.script} {' '.join(spec.arguments)}".strip(),
            "passed": passed,
            "returncode": execution.returncode,
            "duration_ms": execution.duration_ms,
            "summary": summaries[:8],
            "missing_markers": missing,
            "error": execution.stderr.strip()[-500:] if execution.stderr else "",
        }

    def _paper_path(self) -> Path:
        directory = self._root / ".runtime/paper_trading"
        candidates = sorted(directory.glob("*.json")) if directory.is_dir() else []
        return (
            candidates[-1]
            if candidates
            else directory / "d4-live-session.json"
        )

    def _read_local_paper_evidence(self) -> dict[str, Any]:
        path = self._paper_path()
        if not path.is_file():
            return {
                "available": False,
                "path": str(path.relative_to(self._root)),
                "note": "local runtime evidence is optional and excluded from Git",
            }
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
            cycles = ledger["cycles"]
            live_dates = sorted(
                {
                    item["quote"]["as_of"][:10]
                    for item in cycles
                    if item.get("mode") == "live"
                }
            )
            return {
                "available": True,
                "path": str(path.relative_to(self._root)),
                "session_id": ledger["session"]["session_id"],
                "cycle_count": len(cycles),
                "failure_count": len(ledger["failures"]),
                "confirmation_count": len(ledger["confirmations"]),
                "live_trading_dates": live_dates,
                "account": ledger["account"],
            }
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise FinalDeliveryError(f"local paper ledger is invalid: {error}") from error

    def _observed_live_days(self) -> int:
        evidence = self._read_local_paper_evidence()
        return len(evidence.get("live_trading_dates", []))


__all__ = [
    "CommandExecution",
    "CommandRunner",
    "FinalDeliveryError",
    "FinalDeliveryReport",
    "FinalDeliveryRuntime",
    "SubprocessCommandRunner",
]
