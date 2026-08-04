import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))


class ProjectBootstrapTests(unittest.TestCase):
    def test_package_is_importable(self):
        import agent_platform

        self.assertEqual(agent_platform.__version__, "0.1.0")

    def test_required_project_files_exist(self):
        required_files = [
            "README.md",
            "ROADMAP.md",
            "SPEC.md",
            "AGENTS.md",
            "checklist.json",
            "progress.txt",
            "pyproject.toml",
            ".env.example",
        ]

        for relative_path in required_files:
            with self.subTest(relative_path=relative_path):
                self.assertTrue((PROJECT_ROOT / relative_path).is_file())

    def test_required_directories_have_role_documents(self):
        role_directories = [
            "Rule",
            "Skill",
            "Workflow",
            "Scripts",
            "MCP",
            "SubAgents",
            "docs",
        ]

        for directory in role_directories:
            with self.subTest(directory=directory):
                directory_path = PROJECT_ROOT / directory
                self.assertTrue(directory_path.is_dir())
                self.assertTrue((directory_path / "README.md").is_file())

    def test_checklist_is_valid_and_ids_are_unique(self):
        checklist_path = PROJECT_ROOT / "checklist.json"
        checklist = json.loads(checklist_path.read_text(encoding="utf-8"))
        items = checklist["items"]
        ids = [item["id"] for item in items]
        allowed_statuses = {"pending", "in_progress", "done"}
        official_task_ids = {
            "T1.1",
            "T1.2",
            "T1.3",
            "T1.4",
            "T2.1",
            "T2.2",
            "T3.1",
            "T3.2",
            "T3.3",
            "T4.1",
            "T4.2",
            "T4.3",
        }

        self.assertGreater(len(items), 0)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), official_task_ids)
        self.assertTrue(all(item["status"] in allowed_statuses for item in items))
        self.assertTrue(
            all(not item["remaining"] for item in items if item["status"] == "done")
        )

    def test_live_trading_is_disabled_in_example_config(self):
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("ALLOW_LIVE_TRADING=false", env_example)


if __name__ == "__main__":
    unittest.main()
