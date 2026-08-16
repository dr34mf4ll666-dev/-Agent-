import http.cookiejar
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.dashboard import create_server  # noqa: E402
from agent_platform.security import SecurityRuntime  # noqa: E402


class _RuntimeStub:
    def __init__(self, project_root):
        self.project_root = project_root

    def close(self):
        return None


class DashboardSecurityHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.environment = {
            "AGENT_PLATFORM_CLIENT_USERNAME": "reader",
            "AGENT_PLATFORM_CLIENT_PASSWORD": "reader-password-123",
            "AGENT_PLATFORM_ADMIN_USERNAME": "operator",
            "AGENT_PLATFORM_ADMIN_PASSWORD": "operator-password-123",
        }
        cls.security = SecurityRuntime(
            PROJECT_ROOT,
            environment=cls.environment,
            audit_path=Path(cls.temp_dir.name) / "audit.jsonl",
        )
        cls.server = create_server(
            port=0,
            runtime=_RuntimeStub(PROJECT_ROOT),
            security=cls.security,
        )
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp_dir.cleanup()

    def _login(self, username, password):
        jar = http.cookiejar.CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        request = Request(
            f"{self.base_url}/api/auth/login",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": username, "password": password}).encode(),
        )
        with opener.open(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return opener, payload

    def test_unauthenticated_browser_is_redirected_to_login(self):
        with urlopen(f"{self.base_url}/", timeout=3) as response:
            html = response.read().decode("utf-8")

        self.assertEqual(response.geturl(), f"{self.base_url}/login")
        self.assertIn("登录你的工作区", html)

    def test_client_can_use_frontend_but_cannot_open_admin(self):
        opener, payload = self._login("reader", "reader-password-123")

        with opener.open(f"{self.base_url}/", timeout=3) as response:
            client_html = response.read().decode("utf-8")
        with self.assertRaises(HTTPError) as context:
            opener.open(f"{self.base_url}/admin", timeout=3)

        self.assertEqual(payload["role"], "client")
        self.assertIn("看懂一只股票", client_html)
        self.assertEqual(context.exception.code, 403)

    def test_admin_can_open_admin_and_read_audit_summary(self):
        opener, payload = self._login("operator", "operator-password-123")

        with opener.open(f"{self.base_url}/admin", timeout=3) as response:
            admin_html = response.read().decode("utf-8")
        with opener.open(f"{self.base_url}/api/admin/security", timeout=3) as response:
            security = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["role"], "admin")
        self.assertIn("把 Agent 的能力", admin_html)
        self.assertEqual(security["account"]["role"], "admin")
        self.assertFalse(security["audit"]["secrets_recorded"])

    def test_csrf_protects_session_model_key_and_secret_never_returns(self):
        opener, payload = self._login("reader", "reader-password-123")
        body = json.dumps({"api_key": "sk-session-secret-1234567890"}).encode()
        missing = Request(
            f"{self.base_url}/api/auth/model-key",
            method="POST",
            headers={"Content-Type": "application/json"},
            data=body,
        )
        with self.assertRaises(HTTPError) as context:
            opener.open(missing, timeout=3)

        configured = Request(
            f"{self.base_url}/api/auth/model-key",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": payload["csrf_token"],
            },
            data=body,
        )
        with opener.open(configured, timeout=3) as response:
            status = json.loads(response.read().decode("utf-8"))

        self.assertEqual(context.exception.code, 403)
        self.assertTrue(status["configured"])
        self.assertNotIn("sk-session-secret", repr(status))
        self.assertNotIn(
            "sk-session-secret",
            self.security.audit_path.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
