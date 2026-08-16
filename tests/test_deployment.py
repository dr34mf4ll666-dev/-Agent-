import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.deployment import (  # noqa: E402
    DeploymentConfigurationError,
    DeploymentRuntime,
)


class DeploymentRuntimeTests(unittest.TestCase):
    def test_default_configuration_is_ready_and_never_allows_trading(self):
        report = DeploymentRuntime.from_environment(PROJECT_ROOT).readiness()

        self.assertTrue(report["ready"])
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["version"], "0.1.0")
        self.assertFalse(report["safety"]["real_trading_allowed"])
        self.assertEqual(report["checks"]["trading_safety"]["status"], "passed")

    def test_packaged_web_assets_do_not_depend_on_source_checkout_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            report = DeploymentRuntime.from_environment(Path(directory)).readiness()

        self.assertEqual(report["checks"]["web_assets"]["status"], "passed")

    def test_remote_binding_is_rejected_before_server_creation(self):
        report = DeploymentRuntime.from_environment(
            PROJECT_ROOT, host="0.0.0.0", port=8765
        ).readiness()

        self.assertFalse(report["ready"])
        self.assertEqual(report["checks"]["host_binding"]["status"], "failed")
        self.assertIn("禁止远程绑定", report["checks"]["host_binding"]["detail"])

    def test_container_binding_requires_container_mode_and_authentication(self):
        allowed = DeploymentRuntime.from_environment(
            PROJECT_ROOT,
            host="0.0.0.0",
            port=8765,
            environment={
                "AGENT_PLATFORM_CONTAINER_MODE": "true",
                "AGENT_PLATFORM_AUTH_ENABLED": "true",
            },
        ).readiness()
        rejected = DeploymentRuntime.from_environment(
            PROJECT_ROOT,
            host="0.0.0.0",
            port=8765,
            environment={
                "AGENT_PLATFORM_CONTAINER_MODE": "true",
                "AGENT_PLATFORM_AUTH_ENABLED": "false",
            },
        ).readiness()

        self.assertTrue(allowed["ready"])
        self.assertFalse(rejected["ready"])
        self.assertEqual(rejected["checks"]["authentication"]["status"], "failed")

    def test_live_trading_flag_fails_readiness(self):
        report = DeploymentRuntime.from_environment(
            PROJECT_ROOT,
            environment={"ALLOW_LIVE_TRADING": "true"},
        ).readiness()

        self.assertFalse(report["ready"])
        self.assertEqual(report["checks"]["trading_safety"]["status"], "failed")

    def test_invalid_environment_value_is_rejected(self):
        with self.assertRaises(DeploymentConfigurationError):
            DeploymentRuntime.from_environment(
                PROJECT_ROOT,
                environment={"AGENT_PLATFORM_PORT": "not-a-port"},
            )

    def test_version_and_health_do_not_expose_environment_secrets(self):
        runtime = DeploymentRuntime.from_environment(
            PROJECT_ROOT,
            environment={
                "DEEPSEEK_API_KEY": "secret-value",
                "AGENT_PLATFORM_ENV": "test",
            },
        )

        serialized = repr({"version": runtime.version(), "health": runtime.health()})
        self.assertNotIn("secret-value", serialized)
        self.assertEqual(runtime.version()["environment"], "test")


if __name__ == "__main__":
    unittest.main()
