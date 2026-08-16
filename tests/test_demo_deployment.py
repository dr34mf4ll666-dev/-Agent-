import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeploymentDemoTests(unittest.TestCase):
    def test_demo_prints_ready_status_and_safety_boundary(self):
        completed = subprocess.run(
            [sys.executable, "Scripts/demo_deployment_readiness.py"],
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("P8 正式部署、安全和质量门禁验收", completed.stdout)
        self.assertIn("部署状态: ready", completed.stdout)
        self.assertIn("通过: 客户不能访问管理员功能", completed.stdout)
        self.assertIn("通过: 镜像使用非root账户", completed.stdout)
        self.assertIn("通过: Linux和Windows均运行测试", completed.stdout)
        self.assertIn("P8 验收通过", completed.stdout)


if __name__ == "__main__":
    unittest.main()
