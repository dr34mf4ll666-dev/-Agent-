import sys
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


class DeploymentArtifactTests(unittest.TestCase):
    def test_docker_image_runs_as_non_root_with_healthcheck(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn('"--host", "0.0.0.0"', dockerfile)
        self.assertIn("AGENT_PLATFORM_PROJECT_ROOT=/app", dockerfile)
        self.assertIn("ALLOW_LIVE_TRADING=false", dockerfile)
        self.assertNotIn("DEEPSEEK_API_KEY=", dockerfile)

    def test_compose_is_local_only_read_only_and_persists_runtime(self):
        value = yaml.safe_load((PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8"))
        service = value["services"]["agent-platform"]

        self.assertEqual(service["ports"], ["127.0.0.1:8765:8765"])
        self.assertTrue(service["read_only"])
        self.assertIn("no-new-privileges:true", service["security_opt"])
        self.assertIn("agent-runtime:/app/.runtime", service["volumes"])
        self.assertEqual(service["environment"]["ALLOW_LIVE_TRADING"], "false")

    def test_dockerignore_excludes_secrets_runtime_and_git(self):
        ignored = set((PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())

        self.assertTrue({".git", ".runtime", ".env", ".env.*"} <= ignored)


if __name__ == "__main__":
    unittest.main()
