import json
import unittest
from importlib.resources import files
from pathlib import Path

from agent_platform.core import DEFAULT_AGENT_TOOL_POLICIES
from agent_platform.d2_engineering import D2EngineeringRuntime


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class D2EngineeringTests(unittest.TestCase):
    def test_default_resources_complete_all_acceptance_checks(self):
        report = D2EngineeringRuntime.from_files().run()

        self.assertTrue(report.passed)
        self.assertTrue(all(report.acceptance.values()))
        self.assertEqual(report.circuit_breaker["state"], "open")
        self.assertEqual(report.tool_permissions["operation_calls"], 0)
        self.assertEqual(
            report.comparison["with_harness"]["summary"][
                "end_to_end_success_rate_percent"
            ],
            100.0,
        )

    def test_packaged_config_matches_code_level_agent_policies(self):
        resource = files("agent_platform.resources").joinpath(
            "d2_harness_config.json"
        )
        config = json.loads(resource.read_text(encoding="utf-8"))
        configured = {
            item["agent"]: tuple(item["allowed_tools"])
            for item in config["agent_tool_policies"]
        }
        implemented = {
            policy.agent: policy.allowed_tools for policy in DEFAULT_AGENT_TOOL_POLICIES
        }

        self.assertEqual(configured, implemented)

        catalog = json.loads(
            (PROJECT_ROOT / "SubAgents/catalog.json").read_text(encoding="utf-8")
        )
        documented = {
            entry["runtime_name"]: tuple(entry["tools"])
            for entry in catalog["entries"]
            if entry["status"] == "active"
        }
        self.assertEqual(documented, implemented)


if __name__ == "__main__":
    unittest.main()
