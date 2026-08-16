"""One visible acceptance seam for deployment, identity, containers, and CI."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .deployment import DeploymentRuntime
from .security import SecurityConfig, SecurityError, SecurityRuntime


@dataclass(frozen=True)
class P8AcceptanceReport:
    readiness: dict[str, Any]
    identity_and_access: dict[str, bool]
    container: dict[str, bool]
    quality_gates: dict[str, bool]

    @property
    def passed(self) -> bool:
        return bool(self.readiness["ready"]) and all(
            all(group.values())
            for group in (
                self.identity_and_access,
                self.container,
                self.quality_gates,
            )
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": "p8_acceptance_passed" if self.passed else "failed",
            "readiness": dict(self.readiness),
            "identity_and_access": dict(self.identity_and_access),
            "container": dict(self.container),
            "quality_gates": dict(self.quality_gates),
            "passed": self.passed,
        }


class P8AcceptanceRuntime:
    """Hide P8's security behavior and deployment-artifact checks behind one call."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    @classmethod
    def from_project(
        cls, project_root: str | Path | None = None
    ) -> P8AcceptanceRuntime:
        root = Path(project_root or Path(__file__).resolve().parents[2])
        return cls(root)

    def run(self) -> P8AcceptanceReport:
        return P8AcceptanceReport(
            readiness=DeploymentRuntime.from_environment(
                self.project_root
            ).readiness(),
            identity_and_access=self._verify_identity_and_access(),
            container=self._verify_container_contract(),
            quality_gates=self._verify_quality_gates(),
        )

    def _verify_identity_and_access(self) -> dict[str, bool]:
        model_key_value = "sk-" + "p8-acceptance-value-1234567890"
        with tempfile.TemporaryDirectory() as directory:
            runtime = SecurityRuntime(
                self.project_root,
                config=SecurityConfig(model_limit=1),
                environment={
                    "AGENT_PLATFORM_CLIENT_USERNAME": "acceptance-client",
                    "AGENT_PLATFORM_CLIENT_PASSWORD": "client-password-123",
                    "AGENT_PLATFORM_ADMIN_USERNAME": "acceptance-admin",
                    "AGENT_PLATFORM_ADMIN_PASSWORD": "admin-password-123",
                },
                audit_path=Path(directory) / "audit.jsonl",
            )
            client = runtime.login(
                "acceptance-client", "client-password-123", remote_address="127.0.0.1"
            )
            admin = runtime.login(
                "acceptance-admin", "admin-password-123", remote_address="127.0.0.1"
            )
            client_access = runtime.require(
                client.session_token,
                role="client",
                method="GET",
                path="/api/client/overview",
            )
            admin_access = runtime.require(
                admin.session_token,
                role="admin",
                method="GET",
                path="/api/admin/security",
            )
            role_denied = self._security_error_code(
                lambda: runtime.require(
                    client.session_token,
                    role="admin",
                    method="GET",
                    path="/api/admin/security",
                )
            ) == "forbidden"
            csrf_denied = self._security_error_code(
                lambda: runtime.require(
                    client.session_token,
                    role="client",
                    method="POST",
                    path="/api/client/jobs",
                )
            ) == "csrf_failed"
            runtime.require(
                client.session_token,
                role="client",
                method="POST",
                path="/api/client/explain",
                csrf_token=client.principal.csrf_token,
                model_operation=True,
            )
            rate_limited = self._security_error_code(
                lambda: runtime.require(
                    client.session_token,
                    role="client",
                    method="POST",
                    path="/api/client/explain",
                    csrf_token=client.principal.csrf_token,
                    model_operation=True,
                )
            ) == "rate_limited"
            model_status = runtime.set_model_key(client.principal, model_key_value)
            audit_text = runtime.audit_path.read_text(encoding="utf-8")
            return {
                "客户登录成功": client_access.role == "client",
                "管理员登录成功": admin_access.role == "admin",
                "客户不能访问管理员功能": role_denied,
                "写操作需要CSRF校验": csrf_denied,
                "模型调用受到独立限流": rate_limited,
                "DeepSeek密钥只保存于当前会话": (
                    model_status["storage"] == "session_memory_only"
                    and model_status["persists_after_restart"] is False
                ),
                "审计日志不记录密钥": model_key_value not in audit_text,
            }

    @staticmethod
    def _security_error_code(operation: Any) -> str:
        try:
            operation()
        except SecurityError as error:
            return error.code
        return ""

    def _verify_container_contract(self) -> dict[str, bool]:
        dockerfile = (self.project_root / "Dockerfile").read_text(encoding="utf-8")
        compose = yaml.safe_load(
            (self.project_root / "compose.yaml").read_text(encoding="utf-8")
        )
        service = compose["services"]["agent-platform"]
        restart_script = (
            self.project_root / "Scripts" / "verify_container_restart.py"
        ).read_text(encoding="utf-8")
        return {
            "镜像使用非root账户": "USER 10001:10001" in dockerfile,
            "镜像带健康检查": "HEALTHCHECK" in dockerfile,
            "容器端口只暴露给本机": service.get("ports") == ["127.0.0.1:8765:8765"],
            "容器根文件系统只读": service.get("read_only") is True,
            "运行数据使用持久卷": "agent-runtime:/app/.runtime" in service.get("volumes", []),
            "容器禁止新增权限": "no-new-privileges:true"
            in service.get("security_opt", []),
            "容器重启恢复端到端脚本存在": all(
                token in restart_script
                for token in ("docker", '"stop"', '"start"', "/api/client/jobs")
            ),
        }

    def _verify_quality_gates(self) -> dict[str, bool]:
        workflow = (self.project_root / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        runtime_lock = (self.project_root / "requirements.lock").read_text(
            encoding="utf-8"
        )
        return {
            "Linux和Windows均运行测试": all(
                token in workflow for token in ("ubuntu-latest", "windows-latest")
            ),
            "代码规范门禁已配置": "ruff check" in workflow,
            "类型门禁已配置": "mypy " in workflow,
            "覆盖率门禁已配置": "coverage report --fail-under=60" in workflow,
            "浏览器端到端门禁已配置": "Scripts/e2e_dashboard.py" in workflow,
            "镜像构建和重启门禁已配置": all(
                token in workflow
                for token in ("docker build", "Scripts/verify_container_restart.py")
            ),
            "密钥模式扫描已配置": "Scripts/check_secrets.py" in workflow,
            "依赖漏洞扫描已配置": "pip-audit --requirement requirements.lock" in workflow,
            "已锁定无已知告警的setuptools版本": "setuptools==83.0.0" in runtime_lock,
        }


def print_p8_acceptance(report: P8AcceptanceReport) -> None:
    value = report.to_mapping()
    print("=== P8 正式部署、安全和质量门禁验收 ===")
    print(f"部署状态: {value['readiness']['status']}")
    for name, check in value["readiness"]["checks"].items():
        print(f"- {'通过' if check['status'] == 'passed' else '失败'}: {name} · {check['detail']}")
    sections = (
        ("身份、权限与密钥", value["identity_and_access"]),
        ("Docker 容器契约", value["container"]),
        ("自动质量门禁", value["quality_gates"]),
    )
    for title, checks in sections:
        print(f"\n【{title}】")
        for name, passed in checks.items():
            print(f"- {'通过' if passed else '失败'}: {name}")
    print("\n【最终结论】")
    print("P8 验收通过。" if report.passed else "P8 验收失败，请查看失败项。")
    print("边界: 真实交易保持关闭；会话密钥不会持久化。")


__all__ = ["P8AcceptanceReport", "P8AcceptanceRuntime", "print_p8_acceptance"]
