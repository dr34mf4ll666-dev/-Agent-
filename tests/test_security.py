import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.security import SecurityConfig, SecurityError, SecurityRuntime  # noqa: E402


class SecurityRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.environment = {
            "AGENT_PLATFORM_CLIENT_USERNAME": "reader",
            "AGENT_PLATFORM_CLIENT_PASSWORD": "reader-password-123",
            "AGENT_PLATFORM_ADMIN_USERNAME": "operator",
            "AGENT_PLATFORM_ADMIN_PASSWORD": "operator-password-123",
        }
        self.runtime = SecurityRuntime(
            self.root,
            environment=self.environment,
            audit_path=self.root / "audit.jsonl",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _login(self, username="reader", password="reader-password-123"):
        return self.runtime.login(
            username,
            password,
            remote_address="127.0.0.1",
            user_agent="test-browser",
        )

    def test_login_hashes_password_and_returns_role_session(self):
        result = self._login()

        self.assertEqual(result.principal.role, "client")
        self.assertEqual(
            self.runtime.authenticate(result.session_token).username, "reader"
        )
        serialized = repr(self.runtime.__dict__)
        self.assertNotIn("reader-password-123", serialized)

    def test_client_cannot_cross_admin_authorization(self):
        result = self._login()

        with self.assertRaises(SecurityError) as context:
            self.runtime.require(
                result.session_token,
                role="admin",
                method="GET",
                path="/admin",
            )

        self.assertEqual(context.exception.status, 403)
        self.assertEqual(context.exception.code, "forbidden")

    def test_mutation_requires_csrf_token(self):
        result = self._login()

        with self.assertRaises(SecurityError) as context:
            self.runtime.require(
                result.session_token,
                role="client",
                method="POST",
                path="/api/client/jobs",
            )
        self.assertEqual(context.exception.code, "csrf_failed")

        principal = self.runtime.require(
            result.session_token,
            role="client",
            method="POST",
            path="/api/client/jobs",
            csrf_token=result.principal.csrf_token,
        )
        self.assertEqual(principal.username, "reader")

    def test_rate_limit_rejects_excess_mutations(self):
        runtime = SecurityRuntime(
            self.root,
            environment=self.environment,
            config=SecurityConfig(mutation_limit=1),
            audit_path=self.root / "audit.jsonl",
        )
        result = runtime.login(
            "reader", "reader-password-123", remote_address="127.0.0.1"
        )
        runtime.require(
            result.session_token,
            role="client",
            method="POST",
            path="/api/client/jobs",
            csrf_token=result.principal.csrf_token,
        )
        with self.assertRaises(SecurityError) as context:
            runtime.require(
                result.session_token,
                role="client",
                method="POST",
                path="/api/client/jobs",
                csrf_token=result.principal.csrf_token,
            )
        self.assertEqual(context.exception.status, 429)

    def test_session_model_key_is_not_persisted_or_audited(self):
        result = self._login()
        key = "sk-test-secret-key-1234567890"

        status = self.runtime.set_model_key(result.principal, key)
        environment = self.runtime.model_environment(result.principal)

        self.assertTrue(status["configured"])
        self.assertEqual(environment["DEEPSEEK_API_KEY"], key)
        self.assertNotIn(key, self.runtime.audit_path.read_text(encoding="utf-8"))
        self.runtime.logout(result.session_token)
        with self.assertRaises(SecurityError):
            self.runtime.authenticate(result.session_token)

    def test_audit_is_append_only_safe_metadata(self):
        with self.assertRaises(SecurityError):
            self._login(password="wrong-password")
        self._login()

        lines = self.runtime.audit_path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        self.assertEqual([item["status"] for item in records], ["denied", "succeeded"])
        self.assertNotIn("wrong-password", repr(records))
        summary = self.runtime.audit_summary()
        self.assertEqual(summary["event_count"], 2)
        self.assertTrue(summary["secrets_recorded"] is False)

    def test_expired_session_is_removed(self):
        current = datetime(2026, 8, 16, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        runtime = SecurityRuntime(
            self.root,
            environment=self.environment,
            config=SecurityConfig(session_ttl_seconds=300),
            audit_path=self.root / "audit.jsonl",
            now=lambda: current,
        )
        result = runtime.login(
            "reader", "reader-password-123", remote_address="127.0.0.1"
        )
        current += timedelta(seconds=301)

        with self.assertRaises(SecurityError) as context:
            runtime.authenticate(result.session_token)
        self.assertEqual(context.exception.code, "session_expired")

    def test_generated_credentials_are_only_returned_when_passwords_missing(self):
        runtime = SecurityRuntime(self.root, environment={}, audit_path=self.root / "other.jsonl")
        generated = runtime.bootstrap_credentials()

        self.assertEqual(set(generated), {"client", "admin"})
        self.assertGreaterEqual(min(map(len, generated.values())), 12)


if __name__ == "__main__":
    unittest.main()
