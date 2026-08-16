"""Print the complete P8 deployment and security acceptance."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.deployment import DeploymentConfigurationError  # noqa: E402
from agent_platform.p8_acceptance import (  # noqa: E402
    P8AcceptanceRuntime,
    print_p8_acceptance,
)


def main() -> int:
    try:
        report = P8AcceptanceRuntime.from_project(PROJECT_ROOT).run()
    except DeploymentConfigurationError as error:
        print(f"部署配置检查失败: {error}")
        return 2
    print_p8_acceptance(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
