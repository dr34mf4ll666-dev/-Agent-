import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.p8_acceptance import P8AcceptanceRuntime  # noqa: E402


class P8AcceptanceTests(unittest.TestCase):
    def test_one_interface_accepts_security_container_and_quality_gates(self):
        report = P8AcceptanceRuntime.from_project(PROJECT_ROOT).run()
        value = report.to_mapping()

        self.assertTrue(report.passed)
        self.assertEqual(value["status"], "p8_acceptance_passed")
        self.assertTrue(value["readiness"]["ready"])
        self.assertTrue(value["identity_and_access"]["客户不能访问管理员功能"])
        self.assertTrue(value["identity_and_access"]["写操作需要CSRF校验"])
        self.assertTrue(value["identity_and_access"]["模型调用受到独立限流"])
        self.assertTrue(value["identity_and_access"]["审计日志不记录密钥"])
        self.assertTrue(value["container"]["镜像使用非root账户"])
        self.assertTrue(value["container"]["容器重启恢复端到端脚本存在"])
        self.assertTrue(value["quality_gates"]["Linux和Windows均运行测试"])
        self.assertTrue(value["quality_gates"]["依赖漏洞扫描已配置"])


if __name__ == "__main__":
    unittest.main()
