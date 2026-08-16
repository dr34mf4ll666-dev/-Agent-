import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.dashboard_startup import configure_deepseek_for_dashboard  # noqa: E402
from agent_platform.cli import build_parser as build_cli_parser  # noqa: E402


class DashboardDeepSeekStartupTests(unittest.TestCase):
    def test_hidden_prompt_stores_key_only_in_supplied_process_environment(self):
        environment = {}
        prompts = []

        result = configure_deepseek_for_dashboard(
            env=environment,
            secret_reader=lambda prompt: prompts.append(prompt) or "  secret-value  ",
            interactive=True,
        )

        self.assertTrue(result.enabled)
        self.assertEqual(result.source, "prompt")
        self.assertEqual(environment["DEEPSEEK_API_KEY"], "secret-value")
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("secret-value", result.message)

    def test_blank_input_keeps_local_fixed_fallback(self):
        environment = {}

        result = configure_deepseek_for_dashboard(
            env=environment,
            secret_reader=lambda _prompt: "",
            interactive=True,
        )

        self.assertFalse(result.enabled)
        self.assertEqual(result.source, "local_fallback")
        self.assertNotIn("DEEPSEEK_API_KEY", environment)
        self.assertIn("固定", result.message)

    def test_existing_environment_key_skips_prompt(self):
        environment = {"DEEPSEEK_API_KEY": "already-configured"}

        result = configure_deepseek_for_dashboard(
            env=environment,
            secret_reader=lambda _prompt: self.fail("should not prompt"),
            interactive=True,
        )

        self.assertTrue(result.enabled)
        self.assertEqual(result.source, "environment")
        self.assertNotIn("already-configured", result.message)

    def test_disabled_or_noninteractive_prompt_never_reads_secret(self):
        for prompt_enabled, interactive in ((False, True), (True, False)):
            with self.subTest(prompt_enabled=prompt_enabled, interactive=interactive):
                result = configure_deepseek_for_dashboard(
                    env={},
                    secret_reader=lambda _prompt: self.fail("should not prompt"),
                    prompt_enabled=prompt_enabled,
                    interactive=interactive,
                )
                self.assertFalse(result.enabled)
                self.assertEqual(result.source, "local_fallback")

    def test_installed_dashboard_command_supports_no_key_prompt(self):
        arguments = build_cli_parser().parse_args(["dashboard", "--no-key-prompt"])

        self.assertTrue(arguments.no_key_prompt)

    def test_cli_exposes_deployment_check(self):
        arguments = build_cli_parser().parse_args(["deployment-check"])

        self.assertEqual(arguments.command, "deployment-check")


if __name__ == "__main__":
    unittest.main()
