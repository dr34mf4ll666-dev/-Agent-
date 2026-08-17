import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.security_master import (  # noqa: E402
    DEFAULT_SECURITY_MASTER,
    SecurityMasterError,
    SecurityMasterRuntime,
)


class SecurityMasterTests(unittest.TestCase):
    def test_versioned_catalog_has_visible_industries_and_capabilities(self):
        self.assertEqual(DEFAULT_SECURITY_MASTER.catalog_version, "2026-08-17.v1")
        self.assertGreaterEqual(len(DEFAULT_SECURITY_MASTER.customer_records()), 21)
        self.assertEqual(set(DEFAULT_SECURITY_MASTER.industries()), {"银行", "酿酒"})
        record = DEFAULT_SECURITY_MASTER.get("sz000858")
        self.assertTrue(record.verified)
        self.assertTrue(record.customer_visible)
        self.assertEqual(record.analysis_sectors["live"], "酿酒行业")
        self.assertTrue(record.capabilities["full_graph"])

    def test_search_filters_customer_catalog_without_source_code_changes(self):
        result = DEFAULT_SECURITY_MASTER.search(industry="酿酒")
        self.assertEqual([record.symbol for record in result], ["sz000858"])
        result = DEFAULT_SECURITY_MASTER.search(query="000858")
        self.assertEqual([record.name for record in result], ["五粮液"])

    def test_unknown_symbol_is_rejected_by_the_master(self):
        catalog = SecurityMasterRuntime.from_json(
            PROJECT_ROOT / "src" / "agent_platform" / "resources" / "security_master.v1.json"
        )
        with self.assertRaises(SecurityMasterError):
            catalog.get("sz999999")


if __name__ == "__main__":
    unittest.main()
