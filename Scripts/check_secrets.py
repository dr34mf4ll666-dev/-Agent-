"""Fail CI when likely real credentials are committed to project files."""

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".runtime", "__pycache__", ".mypy_cache", ".ruff_cache"}
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ""}
PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("deepseek_key", re.compile(r"sk-(?!test-|session-|example-)[A-Za-z0-9_-]{20,}")),
)


def main() -> int:
    findings: list[str] = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name, pattern in PATTERNS:
            if pattern.search(content):
                findings.append(f"{path.relative_to(PROJECT_ROOT)}: {name}")
    if findings:
        print("疑似密钥扫描失败：")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("疑似密钥扫描通过：未发现私钥、GitHub Token 或真实 DeepSeek Key。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
