"""Secure, process-local DeepSeek selection for dashboard launchers."""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardModelSelection:
    enabled: bool
    source: str
    message: str


def configure_deepseek_for_dashboard(
    *,
    env: MutableMapping[str, str] | None = None,
    secret_reader: Callable[[str], str] = getpass.getpass,
    prompt_enabled: bool = True,
    interactive: bool = True,
) -> DashboardModelSelection:
    """Choose DeepSeek or deterministic fallback without persisting a secret."""

    environment = os.environ if env is None else env
    existing = environment.get("DEEPSEEK_API_KEY", "")
    if isinstance(existing, str) and existing.strip():
        return DashboardModelSelection(
            enabled=True,
            source="environment",
            message="DeepSeek 已启用：使用当前进程已有的环境变量，密钥不会显示。",
        )

    if not prompt_enabled or not interactive:
        return DashboardModelSelection(
            enabled=False,
            source="local_fallback",
            message="DeepSeek 未启用：使用本地安全解释和固定多空辩论。",
        )

    try:
        secret = secret_reader(
            "请输入 DeepSeek API Key（输入不显示，直接回车使用固定格式）: "
        )
    except (EOFError, KeyboardInterrupt):
        secret = ""
    if not isinstance(secret, str) or not secret.strip():
        return DashboardModelSelection(
            enabled=False,
            source="local_fallback",
            message="未输入 DeepSeek API Key：使用本地安全解释和固定多空辩论。",
        )

    environment["DEEPSEEK_API_KEY"] = secret.strip()
    return DashboardModelSelection(
        enabled=True,
        source="prompt",
        message="DeepSeek 已启用：密钥仅保存在当前运行进程中，关闭服务后失效。",
    )


__all__ = ["DashboardModelSelection", "configure_deepseek_for_dashboard"]
