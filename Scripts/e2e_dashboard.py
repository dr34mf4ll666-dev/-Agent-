"""Real Chromium acceptance for customer trust cards and role-separated pages."""

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


def _wait_for_analysis(page, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.locator("#analysis").is_visible():
            return
        if page.locator("#error-state").is_visible():
            message = page.locator("#error-message").inner_text()
            action = page.locator("#error-action").inner_text()
            raise AssertionError(f"analysis failed: {message} {action}")
        page.wait_for_timeout(250)
    raise AssertionError(
        "analysis did not reach a terminal visible result; "
        f"{page.locator('#job-reference').inner_text()} / "
        f"{page.locator('#loading-message').inner_text()} / "
        f"{page.locator('#job-progress-note').inner_text()}"
    )


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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
            page.get_by_label("按行业筛选").select_option("酿酒")
            if page.get_by_label("选择研究标的").input_value() != "sz000858":
                raise AssertionError("industry filter did not expose the verified non-bank symbol")
            page.get_by_label("按行业筛选").select_option("电力")
            if page.get_by_label("选择研究标的").input_value() != "sh600744":
                raise AssertionError("industry filter did not expose the verified electric symbol")
            page.get_by_label("按行业筛选").select_option("")
            page.get_by_label("选择研究标的").select_option("sz000001")
            page.get_by_role("button", name="已验证快照").click()
            page.locator("#analyze-button").click()
            _wait_for_analysis(page)
            page.locator("#loading-state").wait_for(state="hidden", timeout=5_000)
            if not page.locator("#credibility-card").is_visible():
                raise AssertionError("basic report did not show the credibility card")
            if page.locator("#credibility-status").inner_text().strip() in {"", "—"}:
                raise AssertionError("credibility card did not receive a data status")

            page.locator('[data-report-view="professional"]').click()
            page.locator("#run-provenance").wait_for(state="visible", timeout=10_000)
            fingerprint = page.locator("#run-fingerprint").inner_text().strip()
            if len(fingerprint) != 64:
                raise AssertionError("professional report did not show a SHA-256 run fingerprint")

            page.set_viewport_size({"width": 390, "height": 844})
            if page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"):
                raise AssertionError("customer report overflows the mobile viewport")
            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator('[data-report-view="basic"]').click()

            page.locator("#analyze-button").click()
            page.wait_for_timeout(300)
            _wait_for_analysis(page)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and page.locator("#compare-left option").count() < 2:
                page.wait_for_timeout(250)
            if page.locator("#compare-left option").count() < 2:
                raise AssertionError("two completed reports were not available for comparison")
            page.locator("#compare-button").click()
            page.locator("#comparison-result").wait_for(state="visible", timeout=10_000)
            if not page.locator(".comparison-reasons").is_visible():
                raise AssertionError("comparison did not show change reasons")
            if page.locator(".comparison-reasons li").count() < 1:
                raise AssertionError("comparison change reasons were empty")
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
        print("=== 客户前台可信度与角色隔离 Chromium 端到端验收 ===")
        print("- 通过：未登录访问会进入登录页")
        print("- 通过：客户账户进入研究前台")
        print("- 通过：客户前台按行业筛选并显示两个已验证非银行行业标的")
        print("- 通过：普通版显示可信度卡片，专业版显示运行指纹")
        print("- 通过：分析结束后加载区域隐藏，手机宽度无横向溢出")
        print("- 通过：两份报告比较显示‘为什么不同’")
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
