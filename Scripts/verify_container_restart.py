"""Verify a submitted analysis remains available after a real container restart."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _free_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def _wait_health(base_url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/healthz", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.5)
    raise RuntimeError("container health endpoint did not become ready")


def _login(base_url: str):
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    request = Request(
        f"{base_url}/api/auth/login",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(
            {"username": "client", "password": "container-client-password"}
        ).encode(),
    )
    with opener.open(request, timeout=5) as response:
        session = json.loads(response.read().decode("utf-8"))
    return opener, session


def _submit(opener, base_url: str, csrf_token: str) -> str:
    request = Request(
        f"{base_url}/api/client/jobs",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf_token,
        },
        data=json.dumps({"symbol": "sz000001", "mode": "offline"}).encode(),
    )
    with opener.open(request, timeout=8) as response:
        job = json.loads(response.read().decode("utf-8"))
    return str(job["job_id"])


def _wait_job(opener, base_url: str, job_id: str, timeout: float = 180.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with opener.open(f"{base_url}/api/client/jobs/{job_id}", timeout=8) as response:
            job = json.loads(response.read().decode("utf-8"))
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.5)
    raise RuntimeError("recovered job did not reach a terminal state")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验收容器重启后的任务持久化")
    parser.add_argument("--image", default="agent-platform-finance:p8")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    suffix = uuid4().hex[:10]
    container = f"agent-platform-p8-{suffix}"
    volume = f"agent-platform-p8-{suffix}"
    if not container.startswith("agent-platform-p8-") or not volume.startswith(
        "agent-platform-p8-"
    ):
        raise RuntimeError("unsafe Docker cleanup target")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    try:
        _run("volume", "create", volume)
        _run(
            "run",
            "-d",
            "--name",
            container,
            "-p",
            f"127.0.0.1:{port}:8765",
            "-e",
            "AGENT_PLATFORM_CLIENT_USERNAME=client",
            "-e",
            "AGENT_PLATFORM_CLIENT_PASSWORD=container-client-password",
            "-e",
            "AGENT_PLATFORM_ADMIN_USERNAME=admin",
            "-e",
            "AGENT_PLATFORM_ADMIN_PASSWORD=container-admin-password",
            "-v",
            f"{volume}:/app/.runtime",
            args.image,
        )
        _wait_health(base_url)
        opener, session = _login(base_url)
        job_id = _submit(opener, base_url, session["csrf_token"])

        _run("stop", "--time", "3", container)
        _run("start", container)
        _wait_health(base_url)
        recovered_opener, _ = _login(base_url)
        job = _wait_job(recovered_opener, base_url, job_id)
        if job["status"] != "succeeded":
            raise RuntimeError(f"recovered job ended with {job['status']}")
        with recovered_opener.open(
            f"{base_url}/api/client/jobs/{job_id}/result", timeout=8
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
        safety = result.get("safety", {})
        if safety.get("real_trading_allowed") is not False:
            raise RuntimeError("trading safety changed after restart")
        print("=== P8 容器重启恢复验收 ===")
        print(f"- 原任务编号: {job_id}")
        print("- 通过：容器停止并启动后任务仍可查询")
        print(f"- 通过：恢复后任务状态={job['status']}")
        print("- 通过：结果仍保持真实交易关闭")
        return 0
    except (subprocess.CalledProcessError, HTTPError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or error.stdout or str(error)).strip()
        else:
            detail = error.read().decode("utf-8", errors="replace")
        print(f"容器重启验收失败: {detail}")
        return 1
    finally:
        _run("rm", "-f", container, check=False)
        _run("volume", "rm", "-f", volume, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
