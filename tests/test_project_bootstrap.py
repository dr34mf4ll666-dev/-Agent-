import importlib
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
            "dev-map.md",
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

    def test_management_catalogs_reference_importable_modules_and_real_evidence(self):
        catalog_paths = [
            "Skill/catalog.json",
            "MCP/catalog.json",
            "SubAgents/catalog.json",
        ]

        for relative_path in catalog_paths:
            with self.subTest(catalog=relative_path):
                path = PROJECT_ROOT / relative_path
                catalog = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(catalog["version"], 1)
                entries = catalog["entries"]
                self.assertTrue(entries)
                ids = [entry["id"] for entry in entries]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(any(entry["status"] == "active" for entry in entries))

                for entry in entries:
                    self.assertIn(entry["status"], {"active", "pending"})
                    if entry["status"] != "active":
                        continue
                    implementation = PROJECT_ROOT / entry["implementation"]
                    self.assertTrue(implementation.is_file(), implementation)
                    module_name, symbol_name = entry["import_path"].split(":", 1)
                    module = importlib.import_module(module_name)
                    self.assertTrue(hasattr(module, symbol_name))
                    evidence = entry["evidence"]
                    self.assertTrue(evidence)
                    for evidence_path in evidence:
                        self.assertTrue(
                            (PROJECT_ROOT / evidence_path).is_file(),
                            evidence_path,
                        )

    def test_dev_map_covers_all_nine_harness_components(self):
        dev_map = (PROJECT_ROOT / "dev-map.md").read_text(encoding="utf-8")
        required_components = [
            "SPEC",
            "Rule",
            "Skill",
            "Workflow",
            "Scripts",
            "MCP",
            "SubAgent",
            "dev-map",
            "任务看板",
        ]

        for component in required_components:
            with self.subTest(component=component):
                self.assertIn(component, dev_map)

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

        acceptance_ids = {item["id"] for item in checklist["final_acceptance"]}
        self.assertEqual(
            acceptance_ids,
            {"PLATFORM", "FINANCE_APPLICATION", "ENGINEERING"},
        )

    def test_roadmap_preserves_task_brief_acceptance_requirements(self):
        roadmap = (PROJECT_ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        required_terms = [
            "非金融 Demo",
            "Model Gateway",
            "Hook 事件触发循环",
            "边数据 Schema",
            "MACD",
            "2–3 轮结构化辩论",
            "单笔亏损不超过 2%",
            "不少于 20 只股票",
            "夏普比率大于 0.5",
            "连续运行 1–2 周",
            "幻觉率",
            "token 消耗",
        ]

        for required_term in required_terms:
            with self.subTest(required_term=required_term):
                self.assertIn(required_term, roadmap)

    def test_live_trading_is_disabled_in_example_config(self):
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("ALLOW_LIVE_TRADING=false", env_example)


if __name__ == "__main__":
    unittest.main()
