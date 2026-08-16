"""Deployment configuration and health seams for the Web adapter."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import __version__


class DeploymentConfigurationError(ValueError):
    """Raised when the process cannot be started within its safety contract."""


@dataclass(frozen=True)
class DeploymentConfig:
    """Small, explicit configuration surface for a local or future hosted adapter."""

    host: str = "127.0.0.1"
    port: int = 8765
    max_request_bytes: int = 32_768
    request_timeout_seconds: float = 180.0
    model_max_calls: int = 3
    model_max_total_tokens: int = 7_200
    allow_live_trading: bool = False
    container_mode: bool = False
    auth_enabled: bool = True
    environment: str = "local"
    maintenance_message: str = ""

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> DeploymentConfig:
        values = os.environ if environment is None else environment
        return cls(
            host=host if host is not None else values.get("AGENT_PLATFORM_HOST", "127.0.0.1"),
            port=port if port is not None else _read_int(values, "AGENT_PLATFORM_PORT", 8765),
            max_request_bytes=_read_int(
                values, "AGENT_PLATFORM_MAX_REQUEST_BYTES", 32_768
            ),
            request_timeout_seconds=_read_float(
                values, "AGENT_PLATFORM_REQUEST_TIMEOUT_SECONDS", 180.0
            ),
            model_max_calls=_read_int(values, "AGENT_PLATFORM_MODEL_MAX_CALLS", 3),
            model_max_total_tokens=_read_int(
                values, "AGENT_PLATFORM_MODEL_MAX_TOTAL_TOKENS", 7_200
            ),
            allow_live_trading=_read_bool(values, "ALLOW_LIVE_TRADING", False),
            container_mode=_read_bool(
                values, "AGENT_PLATFORM_CONTAINER_MODE", False
            ),
            auth_enabled=_read_bool(
                values, "AGENT_PLATFORM_AUTH_ENABLED", True
            ),
            environment=values.get("AGENT_PLATFORM_ENV", "local").strip() or "local",
            maintenance_message=values.get("AGENT_PLATFORM_MAINTENANCE_MESSAGE", "")[
                :240
            ],
        )


class DeploymentRuntime:
    """Deep module for startup safety, version and health/readiness metadata.

    The Web adapter only needs ``health()``, ``readiness()``, ``version()`` and
    ``assert_startable()``.  Configuration parsing and all safety checks stay
    behind this seam so a future container adapter can reuse them unchanged.
    """

    WEB_ASSETS = (
        "client.html",
        "client.css",
        "client.js",
        "index.html",
        "styles.css",
        "app.js",
        "login.html",
        "login.css",
        "login.js",
    )

    def __init__(
        self,
        project_root: Path,
        *,
        config: DeploymentConfig | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config or DeploymentConfig.from_environment()

    @classmethod
    def from_environment(
        cls,
        project_root: Path,
        *,
        host: str | None = None,
        port: int | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> DeploymentRuntime:
        return cls(
            project_root,
            config=DeploymentConfig.from_environment(
                environment, host=host, port=port
            ),
        )

    def version(self) -> dict[str, object]:
        return {
            "application": "agent-platform-finance",
            "version": __version__,
            "environment": self.config.environment,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "maintenance_message": self.config.maintenance_message,
        }

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "service": "dashboard",
            "version": __version__,
            "environment": self.config.environment,
            "maintenance_message": self.config.maintenance_message,
            "safety": {
                "simulation_only": True,
                "order_created": False,
                "real_trading_allowed": False,
            },
        }

    def readiness(self) -> dict[str, object]:
        checks = self._checks()
        ready = all(item["status"] == "passed" for item in checks.values())
        return {
            "status": "ready" if ready else "not_ready",
            "ready": ready,
            "version": __version__,
            "environment": self.config.environment,
            "maintenance_message": self.config.maintenance_message,
            "checks": checks,
            "safety": {
                "simulation_only": True,
                "order_created": False,
                "real_trading_allowed": False,
            },
        }

    def assert_startable(self) -> None:
        checks = self._checks()
        if all(item["status"] == "passed" for item in checks.values()):
            return
        failed = [
            f"{name}: {item['detail']}"
            for name, item in checks.items()
            if item["status"] != "passed"
        ]
        raise DeploymentConfigurationError("部署前检查未通过：" + "；".join(failed))

    def _checks(self) -> dict[str, dict[str, str]]:
        # Runtime assets live beside this module both in a source checkout and
        # in an installed wheel. They must not be resolved from the checkout
        # layout because containers run the installed package.
        web_root = Path(__file__).resolve().with_name("web")
        assets_missing = [name for name in self.WEB_ASSETS if not (web_root / name).is_file()]
        checks: dict[str, dict[str, str]] = {
            "project_root": _check(
                self.project_root.is_dir(),
                "项目目录可访问" if self.project_root.is_dir() else "项目目录不存在",
            ),
            "python_runtime": _check(
                sys.version_info >= (3, 11),
                "Python >= 3.11" if sys.version_info >= (3, 11) else "需要 Python >= 3.11",
            ),
            "web_assets": _check(
                not assets_missing,
                "前后台静态资源齐全"
                if not assets_missing
                else "缺少资源：" + ", ".join(assets_missing),
            ),
            "host_binding": _check(
                self.config.host in {"127.0.0.1", "localhost"}
                or (
                    self.config.container_mode
                    and self.config.auth_enabled
                    and self.config.host == "0.0.0.0"
                ),
                (
                    "仅监听本机地址"
                    if self.config.host in {"127.0.0.1", "localhost"}
                    else "容器绑定已启用且身份认证保持开启"
                )
                if self.config.host in {"127.0.0.1", "localhost"}
                or (
                    self.config.container_mode
                    and self.config.auth_enabled
                    and self.config.host == "0.0.0.0"
                )
                else "禁止远程绑定；只允许在容器模式且身份认证开启时使用",
            ),
            "authentication": _check(
                self.config.auth_enabled,
                "身份认证已开启"
                if self.config.auth_enabled
                else "P8 正式运行不允许关闭身份认证",
            ),
            "port": _check(
                isinstance(self.config.port, int) and 0 <= self.config.port <= 65_535,
                "端口范围有效"
                if isinstance(self.config.port, int) and 0 <= self.config.port <= 65_535
                else "端口必须在 0 到 65535 之间",
            ),
            "request_limit": _check(
                1_024 <= self.config.max_request_bytes <= 1_048_576,
                "请求体上限有效"
                if 1_024 <= self.config.max_request_bytes <= 1_048_576
                else "请求体上限必须在 1 KB 到 1 MB 之间",
            ),
            "timeout_limit": _check(
                1.0 <= self.config.request_timeout_seconds <= 600.0,
                "请求超时上限有效"
                if 1.0 <= self.config.request_timeout_seconds <= 600.0
                else "请求超时必须在 1 到 600 秒之间",
            ),
            "model_budget": _check(
                1 <= self.config.model_max_calls <= 20
                and 100 <= self.config.model_max_total_tokens <= 100_000,
                "模型调用和 Token 预算有效"
                if 1 <= self.config.model_max_calls <= 20
                and 100 <= self.config.model_max_total_tokens <= 100_000
                else "模型预算超出允许范围",
            ),
            "trading_safety": _check(
                not self.config.allow_live_trading,
                "真实交易保持关闭"
                if not self.config.allow_live_trading
                else "ALLOW_LIVE_TRADING 必须为 false",
            ),
        }
        return checks


def _check(passed: bool, detail: str) -> dict[str, str]:
    return {"status": "passed" if passed else "failed", "detail": detail}


def _read_int(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise DeploymentConfigurationError(f"{name} 必须是整数。") from error


def _read_float(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise DeploymentConfigurationError(f"{name} 必须是数字。") from error


def _read_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise DeploymentConfigurationError(f"{name} 必须是 true 或 false。")


__all__ = [
    "DeploymentConfig",
    "DeploymentConfigurationError",
    "DeploymentRuntime",
]
