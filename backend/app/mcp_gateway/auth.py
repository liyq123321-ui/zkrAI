"""Bearer-token resolution: raw credential -> agent identity.

Two credential shapes are accepted:

1. **Static tokens** declared in ``config/gateway.yaml`` (or
   ``MCP_GATEWAY_STATIC_TOKENS=token:agentId,...``) — convenient for CI.
2. **Signed tokens** verified with the gateway secret:
   - firstFlight access tokens (``<body>.<sig>``, see ``app.security``)
   - standard HS256 JWTs (``<header>.<payload>.<sig>``)

The *policy* (what an agent may call) always comes from the config registry,
never from the token itself. An unknown ``agent_id`` is rejected: the gateway
is deny-all by default.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.mcp_gateway.config import GatewayConfig, GatewaySettings
from app.mcp_gateway.errors import McpError, UNAUTHENTICATED, UNAUTHORIZED

DecodeFn = Callable[[str, str], dict[str, Any]]


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _b64url_json(segment: str) -> dict[str, Any]:
    payload = json.loads(_b64url_decode(segment).decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _verify_hs256_jwt(secret: str, token: str) -> dict[str, Any]:
    """Verify a standard HS256 JWT (3 segments) without third-party deps."""

    header_b64, payload_b64, signature_b64 = token.split(".", 2)
    expected = base64.urlsafe_b64encode(
        hmac.new(
            secret.encode("utf-8"),
            f"{header_b64}.{payload_b64}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(expected, signature_b64):
        raise ValueError("signature mismatch")
    claims = _b64url_json(payload_b64)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        raise ValueError("token expired")
    return claims


def _verify_signed_token(secret: str, token: str) -> dict[str, Any]:
    """Accept firstFlight 2-segment tokens and standard HS256 JWTs."""

    parts = token.split(".")
    if len(parts) == 2:
        from app.security import InvalidToken, decode_access_token

        try:
            return decode_access_token(secret, token)
        except InvalidToken as exc:  # pragma: no cover - message passthrough
            raise ValueError(str(exc)) from exc
    if len(parts) == 3:
        return _verify_hs256_jwt(secret, token)
    raise ValueError("malformed token")


@dataclass(frozen=True)
class AgentIdentity:
    """Resolved caller. ``roles`` here are informational; policy wins."""

    agent_id: str
    roles: tuple[str, ...] = ()
    auth_mode: str = "static"


class Authenticator:
    """Resolve an Authorization header into an :class:`AgentIdentity`."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        jwt_secret: str = "",
        decode_fn: DecodeFn | None = None,
    ) -> None:
        self._config = config
        self._settings: GatewaySettings = config.settings
        self._jwt_secret = jwt_secret or self._settings.jwt_secret
        self._decode_fn = decode_fn or _verify_signed_token

    @property
    def static_token_count(self) -> int:
        return len(self._settings.static_tokens)

    @property
    def jwt_configured(self) -> bool:
        return bool(self._jwt_secret)

    def _resolve_static(self, token: str) -> str | None:
        for entry in self._settings.static_tokens:
            if hmac.compare_digest(entry.token, token):
                return entry.agent_id
        return None

    def _resolve_signed(self, token: str) -> tuple[str, tuple[str, ...]] | None:
        if not self._jwt_secret:
            return None
        try:
            claims = self._decode_fn(self._jwt_secret, token)
        except Exception:  # noqa: BLE001 - any crypto/format failure => bad cred
            return None
        subject = claims.get("sub") or claims.get("agent_id") or claims.get("client_id")
        if not isinstance(subject, str) or not subject:
            return None
        roles = claims.get("roles")
        if not isinstance(roles, (list, tuple)):
            single = claims.get("role")
            roles = [single] if isinstance(single, str) else []
        return subject, tuple(str(role) for role in roles)

    def policy_for(self, agent_id: str):
        return self._config.agents.get(agent_id)

    def resolve(self, authorization: str | None) -> AgentIdentity:
        """Resolve and authorize a caller, raising :class:`McpError` on failure."""

        token = ""
        if authorization:
            parts = authorization.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1].strip()
            elif len(parts) == 1:
                token = parts[0].strip()
        if not token:
            raise McpError(
                UNAUTHENTICATED,
                "缺少 Bearer 凭证：请在 Authorization 头中提供网关 token。",
            )

        mode = (self._settings.auth_mode or "both").lower()
        agent_id: str | None = None
        auth_mode = "static"
        roles: tuple[str, ...] = ()

        if mode in {"static", "both"}:
            agent_id = self._resolve_static(token)
        if agent_id is None and mode in {"jwt", "both"}:
            signed = self._resolve_signed(token)
            if signed is not None:
                agent_id, roles = signed
                auth_mode = "jwt"
        if agent_id is None:
            raise McpError(UNAUTHENTICATED, "网关 token 无效或已过期。")

        policy = self.policy_for(agent_id)
        if policy is None or not policy.enabled:
            raise McpError(
                UNAUTHORIZED,
                f"agent '{agent_id}' 未在网关注册或已被禁用。",
                data={"agent_id": agent_id},
            )
        return AgentIdentity(
            agent_id=agent_id,
            roles=roles or policy.roles,
            auth_mode=auth_mode,
        )


def mint_agent_token(
    secret: str,
    agent_id: str,
    roles: tuple[str, ...] | list[str] = (),
    ttl_seconds: int = 43_200,
) -> str:
    """Mint a gateway token for ``agent_id`` using the firstFlight HMAC scheme.

    The payload carries ``roles`` so any firstFlight-issued token also works
    (its single ``role`` claim is normalized by :meth:`Authenticator.resolve`).
    """

    now = int(time.time())
    payload = {
        "sub": agent_id,
        "roles": list(roles),
        "typ": "mcp",
        "iat": now,
        "exp": now + int(ttl_seconds),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    signature = hmac.new(
        secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).digest()
    return f"{body}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"


def bearer_token(headers: Mapping[str, str]) -> str | None:
    for key in ("authorization", "Authorization"):
        value = headers.get(key)
        if value:
            return value
    return None
