"""Real Chromium acceptance for login and role-separated Dashboard pages."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _wait_for_health(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{url}/healthz", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("Dashboard health check timed out")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 Playwright；请安装 requirements-ci.lock 并运行 playwright install chromium。")
        return 2
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "AGENT_PLATFORM_CLIENT_USERNAME": "reader",
        "AGENT_PLATFORM_CLIENT_PASSWORD": "reader-password-123",
        "AGENT_PLATFORM_ADMIN_USERNAME": "operator",
        "AGENT_PLATFORM_ADMIN_PASSWORD": "operator-password-123",
        "AGENT_PLATFORM_AUTH_ENABLED": "true",
        "ALLOW_LIVE_TRADING": "false",
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "Scripts" / "run_dashboard.py"),
            "--port",
            str(port),
            "--no-browser",
            "--no-key-prompt",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        _wait_for_health(base_url)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            client = browser.new_context()
            page = client.new_page()
            page.goto(base_url, wait_until="domcontentloaded")
            if not page.url.endswith("/login"):
                raise AssertionError("anonymous user was not redirected to login")
            page.get_by_label("用户名").fill("reader")
            page.get_by_label("密码").fill("reader-password-123")
            page.get_by_role("button", name="安全登录").click()
            page.wait_for_url(base_url + "/")
            page.get_by_role("button", name="reader").click()
            page.get_by_text("账户与模型设置").wait_for()
            response = page.goto(base_url + "/admin", wait_until="domcontentloaded")
            if response is None or response.status != 403:
                raise AssertionError("client account unexpectedly reached /admin")
            client.close()

            admin = browser.new_context()
            page = admin.new_page()
            page.goto(base_url + "/login")
            page.get_by_label("用户名").fill("operator")
            page.get_by_label("密码").fill("operator-password-123")
            page.get_by_role("button", name="安全登录").click()
            page.wait_for_url(base_url + "/admin")
            page.get_by_role("button", name="账户与安全").click()
            page.get_by_text("账户与安全运行状态").wait_for()
            page.get_by_text("当前管理员").wait_for()
            admin.close()
            browser.close()
        print("=== P8 Chromium 端到端验收 ===")
        print("- 通过：未登录访问会进入登录页")
        print("- 通过：客户账户进入研究前台")
        print("- 通过：客户账户访问 /admin 返回 403")
        print("- 通过：管理员进入安全审计界面")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
