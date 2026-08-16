"""Authentication, authorization, throttling, audit, and session secrets."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo


class SecurityError(ValueError):
    """A request cannot cross the security seam."""

    def __init__(self, message: str, *, code: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class SecurityConfig:
    enabled: bool = True
    session_ttl_seconds: int = 28_800
    request_limit: int = 120
    mutation_limit: int = 30
    model_limit: int = 6
    login_limit: int = 5
    rate_window_seconds: int = 60
    login_window_seconds: int = 300
    audit_max_bytes: int = 2_000_000

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> SecurityConfig:
        values = os.environ if environment is None else environment
        return cls(
            enabled=_read_bool(values, "AGENT_PLATFORM_AUTH_ENABLED", True),
            session_ttl_seconds=_read_int(
                values, "AGENT_PLATFORM_SESSION_TTL_SECONDS", 28_800, 300, 86_400
            ),
            request_limit=_read_int(
                values, "AGENT_PLATFORM_REQUESTS_PER_MINUTE", 120, 10, 10_000
            ),
            mutation_limit=_read_int(
                values, "AGENT_PLATFORM_MUTATIONS_PER_MINUTE", 30, 1, 1_000
            ),
            model_limit=_read_int(
                values, "AGENT_PLATFORM_MODEL_CALLS_PER_MINUTE", 6, 1, 100
            ),
            login_limit=_read_int(
                values, "AGENT_PLATFORM_LOGIN_ATTEMPTS", 5, 1, 100
            ),
            rate_window_seconds=_read_int(
                values, "AGENT_PLATFORM_RATE_WINDOW_SECONDS", 60, 1, 3_600
            ),
            login_window_seconds=_read_int(
                values, "AGENT_PLATFORM_LOGIN_WINDOW_SECONDS", 300, 10, 3_600
            ),
            audit_max_bytes=_read_int(
                values, "AGENT_PLATFORM_AUDIT_MAX_BYTES", 2_000_000, 10_000, 20_000_000
            ),
        )


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    session_id: str
    csrf_token: str
    expires_at: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "username": self.username,
            "role": self.role,
            "csrf_token": self.csrf_token,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class LoginResult:
    principal: Principal
    session_token: str
    max_age: int


@dataclass(frozen=True)
class _Account:
    username: str
    role: str
    salt: bytes
    password_hash: bytes


@dataclass
class _Session:
    username: str
    role: str
    session_id: str
    csrf_token: str
    expires_at: datetime


class SecurityRuntime:
    """Deep security module used by the Web adapter and deployment tests.

    Callers authenticate and authorize through a small interface. Password
    hashing, sliding-window throttling, session expiry, secret lifetime, and
    append-only audit implementation remain private to this module.
    """

    _ROLE_LEVEL = {"client": 1, "admin": 2}

    def __init__(
        self,
        project_root: Path,
        *,
        config: SecurityConfig | None = None,
        environment: MutableMapping[str, str] | None = None,
        audit_path: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        source_environment = os.environ if environment is None else environment
        self.config = config or SecurityConfig.from_environment(source_environment)
        self._model_name = source_environment.get(
            "DEEPSEEK_MODEL", "deepseek-v4-flash"
        )
        self.audit_path = (
            Path(audit_path).resolve()
            if audit_path is not None
            else self.project_root / ".runtime" / "security" / "audit.jsonl"
        )
        self._now = now or (lambda: datetime.now(ZoneInfo("Asia/Shanghai")))
        self._lock = RLock()
        self._sessions: dict[str, _Session] = {}
        self._model_keys: dict[str, str] = {}
        self._windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._generated_credentials: dict[str, str] = {}
        self._accounts = self._build_accounts(source_environment)

    @classmethod
    def disabled(cls, project_root: Path) -> SecurityRuntime:
        return cls(project_root, config=SecurityConfig(enabled=False), environment={})

    def bootstrap_credentials(self) -> dict[str, str]:
        """Return only credentials generated for this process, for one-time display."""

        return dict(self._generated_credentials)

    def login(
        self,
        username: str,
        password: str,
        *,
        remote_address: str,
        user_agent: str = "",
    ) -> LoginResult:
        if not self.config.enabled:
            principal = self._disabled_principal()
            return LoginResult(principal, "disabled", self.config.session_ttl_seconds)
        identity = f"login:{remote_address or 'unknown'}"
        self._consume(identity, "login", self.config.login_limit, self.config.login_window_seconds)
        normalized = username.strip().lower() if isinstance(username, str) else ""
        account = self._accounts.get(normalized)
        if account is None or not self._verify_password(account, password):
            self.audit(
                "login",
                "denied",
                username=normalized or "unknown",
                role="anonymous",
                remote_address=remote_address,
                detail="invalid_credentials",
            )
            raise SecurityError("用户名或密码不正确。", code="invalid_credentials", status=401)
        now = self._now()
        token = secrets.token_urlsafe(32)
        session = _Session(
            username=account.username,
            role=account.role,
            session_id=secrets.token_hex(16),
            csrf_token=secrets.token_urlsafe(24),
            expires_at=now + timedelta(seconds=self.config.session_ttl_seconds),
        )
        with self._lock:
            self._purge_expired_locked(now)
            self._sessions[token] = session
        self.audit(
            "login",
            "succeeded",
            username=account.username,
            role=account.role,
            remote_address=remote_address,
            detail=_safe_user_agent(user_agent),
        )
        return LoginResult(
            self._principal(session), token, self.config.session_ttl_seconds
        )

    def require(
        self,
        session_token: str | None,
        *,
        role: str,
        method: str,
        path: str,
        csrf_token: str | None = None,
        remote_address: str = "",
        model_operation: bool = False,
    ) -> Principal:
        if not self.config.enabled:
            return self._disabled_principal()
        principal = self.authenticate(session_token)
        if self._ROLE_LEVEL.get(principal.role, 0) < self._ROLE_LEVEL.get(role, 99):
            self.audit(
                "authorization",
                "denied",
                username=principal.username,
                role=principal.role,
                method=method,
                path=path,
                remote_address=remote_address,
                detail=f"requires_{role}",
            )
            raise SecurityError("当前账户没有访问该功能的权限。", code="forbidden", status=403)
        normalized_method = method.upper()
        if normalized_method in {"POST", "PUT", "PATCH", "DELETE"}:
            if not csrf_token or not hmac.compare_digest(csrf_token, principal.csrf_token):
                self.audit(
                    "csrf",
                    "denied",
                    username=principal.username,
                    role=principal.role,
                    method=method,
                    path=path,
                    remote_address=remote_address,
                    detail="token_mismatch",
                )
                raise SecurityError("安全校验已失效，请刷新页面后重试。", code="csrf_failed", status=403)
            bucket = "model" if model_operation else "mutation"
            limit = self.config.model_limit if model_operation else self.config.mutation_limit
        else:
            bucket = "request"
            limit = self.config.request_limit
        try:
            self._consume(
                f"session:{principal.session_id}",
                bucket,
                limit,
                self.config.rate_window_seconds,
            )
        except SecurityError:
            self.audit(
                "rate_limit",
                "denied",
                username=principal.username,
                role=principal.role,
                method=method,
                path=path,
                remote_address=remote_address,
                detail=bucket,
            )
            raise
        return principal

    def authenticate(self, session_token: str | None) -> Principal:
        if not self.config.enabled:
            return self._disabled_principal()
        if not session_token:
            raise SecurityError("请先登录。", code="authentication_required", status=401)
        now = self._now()
        with self._lock:
            self._purge_expired_locked(now)
            session = self._sessions.get(session_token)
            if session is None:
                raise SecurityError("登录状态已失效，请重新登录。", code="session_expired", status=401)
            return self._principal(session)

    def logout(self, session_token: str | None, *, remote_address: str = "") -> None:
        if not session_token:
            return
        with self._lock:
            session = self._sessions.pop(session_token, None)
            if session is not None:
                self._model_keys.pop(session.session_id, None)
        if session is not None:
            self.audit(
                "logout",
                "succeeded",
                username=session.username,
                role=session.role,
                remote_address=remote_address,
            )

    def set_model_key(self, principal: Principal, api_key: str) -> dict[str, Any]:
        value = api_key.strip() if isinstance(api_key, str) else ""
        if len(value) < 20 or len(value) > 256 or any(character.isspace() for character in value):
            raise SecurityError(
                "DeepSeek API Key 格式无效。",
                code="invalid_model_key",
                status=400,
            )
        with self._lock:
            self._model_keys[principal.session_id] = value
        self.audit(
            "model_key",
            "configured",
            username=principal.username,
            role=principal.role,
            detail="session_memory_only",
        )
        return self.model_key_status(principal)

    def clear_model_key(self, principal: Principal) -> dict[str, Any]:
        with self._lock:
            existed = self._model_keys.pop(principal.session_id, None) is not None
        self.audit(
            "model_key",
            "cleared",
            username=principal.username,
            role=principal.role,
            detail="removed" if existed else "already_empty",
        )
        return self.model_key_status(principal)

    def model_key_status(self, principal: Principal) -> dict[str, Any]:
        with self._lock:
            configured = principal.session_id in self._model_keys
        return {
            "configured": configured,
            "provider": "deepseek" if configured else "local_fallback",
            "storage": "session_memory_only",
            "persists_after_restart": False,
        }

    def model_environment(self, principal: Principal) -> dict[str, str]:
        with self._lock:
            key = self._model_keys.get(principal.session_id)
        if not key:
            return {}
        return {
            "DEEPSEEK_API_KEY": key,
            "DEEPSEEK_MODEL": self._model_name,
        }

    def audit(
        self,
        event: str,
        status: str,
        *,
        username: str = "system",
        role: str = "system",
        method: str = "",
        path: str = "",
        remote_address: str = "",
        detail: str = "",
        request_id: str = "",
    ) -> None:
        record = {
            "timestamp": self._now().isoformat(timespec="milliseconds"),
            "event": str(event)[:80],
            "status": str(status)[:40],
            "username": str(username)[:80],
            "role": str(role)[:24],
            "method": str(method)[:12],
            "path": str(path)[:240],
            "remote_address": str(remote_address)[:80],
            "detail": str(detail)[:240],
            "request_id": str(request_id)[:80],
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate_audit_locked(len(line.encode("utf-8")))
            with self.audit_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(line)

    def audit_summary(self, *, limit: int = 20) -> dict[str, Any]:
        records = self._read_audit_records()
        denied = sum(1 for item in records if item.get("status") == "denied")
        return {
            "event_count": len(records),
            "denied_count": denied,
            "active_sessions": self._active_session_count(),
            "recent": records[-max(1, min(int(limit), 100)):][::-1],
            "audit_file": self.audit_path.name,
            "secrets_recorded": False,
        }

    def _build_accounts(
        self, environment: Mapping[str, str]
    ) -> dict[str, _Account]:
        if not self.config.enabled:
            return {}
        result: dict[str, _Account] = {}
        definitions = (
            (
                "client",
                environment.get("AGENT_PLATFORM_CLIENT_USERNAME", "client"),
                environment.get("AGENT_PLATFORM_CLIENT_PASSWORD", ""),
            ),
            (
                "admin",
                environment.get("AGENT_PLATFORM_ADMIN_USERNAME", "admin"),
                environment.get("AGENT_PLATFORM_ADMIN_PASSWORD", ""),
            ),
        )
        for role, username_value, password_value in definitions:
            username = str(username_value).strip().lower()
            if not username or username in result:
                raise SecurityError(
                    "客户和管理员用户名必须非空且不能重复。",
                    code="invalid_accounts",
                    status=500,
                )
            password = str(password_value)
            if not password:
                password = secrets.token_urlsafe(12)
                self._generated_credentials[username] = password
            if len(password) < 12:
                raise SecurityError(
                    f"{role} 密码至少需要 12 个字符。",
                    code="weak_password",
                    status=500,
                )
            salt = secrets.token_bytes(16)
            result[username] = _Account(
                username=username,
                role=role,
                salt=salt,
                password_hash=_password_hash(password, salt),
            )
        return result

    def _verify_password(self, account: _Account, password: str) -> bool:
        value = password if isinstance(password, str) else ""
        candidate = _password_hash(value, account.salt)
        return hmac.compare_digest(candidate, account.password_hash)

    def _consume(self, identity: str, bucket: str, limit: int, window: int) -> None:
        now = self._now().timestamp()
        key = (identity, bucket)
        with self._lock:
            queue = self._windows[key]
            cutoff = now - window
            while queue and queue[0] <= cutoff:
                queue.popleft()
            if len(queue) >= limit:
                retry_after = max(1, int(window - (now - queue[0])))
                raise SecurityError(
                    f"请求过于频繁，请在 {retry_after} 秒后重试。",
                    code="rate_limited",
                    status=429,
                )
            queue.append(now)

    def _principal(self, session: _Session) -> Principal:
        return Principal(
            username=session.username,
            role=session.role,
            session_id=session.session_id,
            csrf_token=session.csrf_token,
            expires_at=session.expires_at.isoformat(timespec="seconds"),
        )

    def _disabled_principal(self) -> Principal:
        return Principal(
            username="test-system",
            role="admin",
            session_id="security-disabled",
            csrf_token="security-disabled",
            expires_at="never",
        )

    def _purge_expired_locked(self, now: datetime) -> None:
        expired = [token for token, value in self._sessions.items() if value.expires_at <= now]
        for token in expired:
            session = self._sessions.pop(token)
            self._model_keys.pop(session.session_id, None)

    def _active_session_count(self) -> int:
        with self._lock:
            self._purge_expired_locked(self._now())
            return len(self._sessions)

    def _rotate_audit_locked(self, incoming_bytes: int) -> None:
        try:
            current_size = self.audit_path.stat().st_size
        except FileNotFoundError:
            return
        if current_size + incoming_bytes <= self.config.audit_max_bytes:
            return
        rotated = self.audit_path.with_suffix(self.audit_path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        self.audit_path.replace(rotated)

    def _read_audit_records(self) -> list[dict[str, Any]]:
        if not self.audit_path.is_file():
            return []
        records: list[dict[str, Any]] = []
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines[-500:]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
        return records


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)


def _safe_user_agent(value: str) -> str:
    return " ".join(str(value).split())[:160]


def _read_int(
    values: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise SecurityError(f"{name} 必须是整数。", code="invalid_security_config", status=500) from error
    if not minimum <= value <= maximum:
        raise SecurityError(
            f"{name} 必须在 {minimum} 到 {maximum} 之间。",
            code="invalid_security_config",
            status=500,
        )
    return value


def _read_bool(values: Mapping[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SecurityError(
        f"{name} 必须是 true 或 false。",
        code="invalid_security_config",
        status=500,
    )


__all__ = [
    "LoginResult",
    "Principal",
    "SecurityConfig",
    "SecurityError",
    "SecurityRuntime",
]
