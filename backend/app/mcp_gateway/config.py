"""Declarative configuration for the MCP gateway.

Two YAML files are the single source of truth:

``config/gateway.yaml``
    server metadata, auth mode, rate limits, audit, and the agent registry
    (including each agent's exposure whitelist).

``config/skills.yaml``
    the skill registry (tools / resources / prompts) with adapter bindings.

Environment variables override the file values so secrets never need to live
in the repository. See ``docs/mcp-gateway.md`` for the full list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent / "config"
BACKEND_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

KIND_TOOL = "tool"
KIND_RESOURCE = "resource"
KIND_PROMPT = "prompt"


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        return (stripped,) if stripped else ()
    if isinstance(value, Mapping):
        return ()
    try:
        return tuple(str(item).strip() for item in value if str(item).strip())
    except TypeError:
        return ()


@dataclass(frozen=True)
class StaticToken:
    """Pre-shared machine token (dev / CI convenience)."""

    token: str
    agent_id: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillSpec:
    """One registered skill: a tool, a resource family, or a prompt."""

    id: str
    kind: str
    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    arguments: tuple[dict[str, Any], ...] = ()
    template: str = ""
    uri_template: str | None = None
    mime_type: str = "text/plain"
    adapter: str = "builtin"
    upstream: str | None = None
    api_key_env: str | None = None
    timeout_sec: float = 60.0
    options: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @property
    def tool_name(self) -> str:
        return self.name or self.id

    @property
    def prompt_name(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class AgentPolicy:
    """Exposure policy for one agent. Default stance is deny-all."""

    id: str
    roles: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_resources: tuple[str, ...] = ()
    allowed_prompts: tuple[str, ...] = ()
    rate_limit_per_minute: int | None = None
    enabled: bool = True
    description: str = ""


@dataclass(frozen=True)
class GatewaySettings:
    server_name: str = "firstflight-mcp-gateway"
    server_version: str = "0.1.0"
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    instructions: str = ""
    auth_mode: str = "both"  # jwt | static | both
    jwt_secret: str = ""
    jwt_secret_env: str = "GATEWAY_JWT_SECRET"
    static_tokens: tuple[StaticToken, ...] = ()
    ratelimit_enabled: bool = True
    per_agent_per_minute: int = 60
    audit_enabled: bool = True
    audit_dir: Path = BACKEND_ROOT / "data" / "mcp-gateway"
    audit_memory: int = 200
    audit_arg_preview_chars: int = 200
    mock_upstream: bool = False
    admin_token: str = ""
    admin_token_env: str = "MCP_GATEWAY_ADMIN_TOKEN"
    sessions_enabled: bool = True


@dataclass(frozen=True)
class GatewayConfig:
    settings: GatewaySettings
    agents: dict[str, AgentPolicy]
    skills: tuple[SkillSpec, ...]
    capability_routing: Mapping[str, Any] = field(default_factory=dict)
    skill_packages: tuple[Mapping[str, Any], ...] = ()
    skill_sources: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @property
    def tools(self) -> tuple[SkillSpec, ...]:
        return tuple(s for s in self.skills if s.kind == KIND_TOOL and s.enabled)

    @property
    def resources(self) -> tuple[SkillSpec, ...]:
        return tuple(s for s in self.skills if s.kind == KIND_RESOURCE and s.enabled)

    @property
    def prompts(self) -> tuple[SkillSpec, ...]:
        return tuple(s for s in self.skills if s.kind == KIND_PROMPT and s.enabled)


def _parse_skill(raw: Mapping[str, Any]) -> SkillSpec | None:
    if not isinstance(raw, Mapping):
        return None
    skill_id = str(raw.get("id") or "").strip()
    if not skill_id:
        return None
    kind = str(raw.get("kind") or KIND_TOOL).strip().lower()
    if kind not in {KIND_TOOL, KIND_RESOURCE, KIND_PROMPT}:
        kind = KIND_TOOL
    schema = raw.get("input_schema") or {"type": "object", "properties": {}}
    if not isinstance(schema, Mapping):
        schema = {"type": "object", "properties": {}}
    arguments = tuple(
        dict(arg)
        for arg in (raw.get("arguments") or ())
        if isinstance(arg, Mapping)
    )
    return SkillSpec(
        id=skill_id,
        kind=kind,
        name=str(raw.get("name") or skill_id).strip(),
        description=str(raw.get("description") or "").strip(),
        input_schema=dict(schema),
        arguments=arguments,
        template=str(raw.get("template") or ""),
        uri_template=(
            str(raw["uri_template"]).strip() if raw.get("uri_template") else None
        ),
        mime_type=str(raw.get("mime_type") or "text/plain").strip(),
        adapter=str(raw.get("adapter") or "builtin").strip(),
        upstream=(str(raw["upstream"]).strip() if raw.get("upstream") else None),
        api_key_env=(
            str(raw["api_key_env"]).strip() if raw.get("api_key_env") else None
        ),
        timeout_sec=_as_float(raw.get("timeout_sec"), 60.0),
        options=dict(raw.get("options") or {}),
        enabled=_as_bool(raw.get("enabled"), True),
    )


def _parse_agent(raw: Mapping[str, Any]) -> AgentPolicy | None:
    if not isinstance(raw, Mapping):
        return None
    agent_id = str(raw.get("id") or "").strip()
    if not agent_id:
        return None
    limit = raw.get("rate_limit_per_minute")
    return AgentPolicy(
        id=agent_id,
        roles=_as_tuple(raw.get("roles")),
        allowed_tools=_as_tuple(raw.get("allowed_tools")),
        allowed_resources=_as_tuple(raw.get("allowed_resources")),
        allowed_prompts=_as_tuple(raw.get("allowed_prompts")),
        rate_limit_per_minute=(int(limit) if isinstance(limit, (int, float)) else None),
        enabled=_as_bool(raw.get("enabled"), True),
        description=str(raw.get("description") or "").strip(),
    )


def _load_yaml(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, Mapping) else {}


def _parse_settings(raw: Mapping[str, Any], env: Mapping[str, str]) -> GatewaySettings:
    auth = raw.get("auth") if isinstance(raw.get("auth"), Mapping) else {}
    rate = raw.get("ratelimit") if isinstance(raw.get("ratelimit"), Mapping) else {}
    audit = raw.get("audit") if isinstance(raw.get("audit"), Mapping) else {}
    server = raw.get("server") if isinstance(raw.get("server"), Mapping) else {}

    static_tokens: list[StaticToken] = []
    raw_tokens = auth.get("static_tokens") or ()
    if isinstance(raw_tokens, Mapping):  # token -> agent_id shorthand
        raw_tokens = [{"token": k, "agent_id": v} for k, v in raw_tokens.items()]
    for item in raw_tokens:
        if not isinstance(item, Mapping):
            continue
        token = str(item.get("token") or "").strip()
        agent_id = str(item.get("agent_id") or "").strip()
        if token and agent_id:
            static_tokens.append(
                StaticToken(
                    token=token,
                    agent_id=agent_id,
                    roles=_as_tuple(item.get("roles")),
                )
            )
    # Extra machine tokens can be injected without editing the YAML file.
    raw_env_tokens = env.get("MCP_GATEWAY_STATIC_TOKENS", "")
    for pair in raw_env_tokens.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        token, agent_id = pair.split(":", 1)
        token, agent_id = token.strip(), agent_id.strip()
        if token and agent_id:
            static_tokens.append(StaticToken(token=token, agent_id=agent_id))

    secret_env = str(auth.get("jwt_secret_env") or "GATEWAY_JWT_SECRET").strip()
    jwt_secret = (
        env.get("MCP_GATEWAY_JWT_SECRET", "").strip()
        or env.get(secret_env, "").strip()
        or str(auth.get("jwt_secret") or "").strip()
    )
    admin_token_env = str(
        raw.get("admin_token_env") or "MCP_GATEWAY_ADMIN_TOKEN"
    ).strip()
    protocol_version = str(server.get("protocol_version") or DEFAULT_PROTOCOL_VERSION)
    if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        protocol_version = DEFAULT_PROTOCOL_VERSION

    return GatewaySettings(
        server_name=str(server.get("name") or "firstflight-mcp-gateway").strip(),
        server_version=str(server.get("version") or "0.1.0").strip(),
        protocol_version=protocol_version,
        instructions=str(server.get("instructions") or "").strip(),
        auth_mode=str(
            env.get("MCP_GATEWAY_AUTH_MODE") or auth.get("mode") or "both"
        ).strip().lower(),
        jwt_secret=jwt_secret,
        jwt_secret_env=secret_env,
        static_tokens=tuple(static_tokens),
        ratelimit_enabled=_as_bool(rate.get("enabled"), True),
        per_agent_per_minute=_as_int(
            env.get("MCP_GATEWAY_RATE_LIMIT_PER_MINUTE") or rate.get("per_agent_per_minute"),
            60,
        ),
        audit_enabled=_as_bool(audit.get("enabled"), True),
        audit_dir=Path(
            env.get("MCP_GATEWAY_AUDIT_DIR")
            or audit.get("dir")
            or (BACKEND_ROOT / "data" / "mcp-gateway")
        ),
        audit_memory=_as_int(audit.get("memory_size"), 200),
        audit_arg_preview_chars=_as_int(audit.get("arg_preview_chars"), 200),
        mock_upstream=_as_bool(
            env.get("MCP_GATEWAY_MOCK_UPSTREAM") or env.get("MOCK_UPSTREAM"),
            _as_bool(raw.get("mock_upstream"), False),
        ),
        admin_token=env.get(admin_token_env, "").strip(),
        admin_token_env=admin_token_env,
        sessions_enabled=_as_bool(server.get("sessions"), True),
    )


def load_gateway_config(
    config_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> GatewayConfig:
    """Load gateway + skill registries, applying environment overrides."""

    environment = os.environ if env is None else env
    directory = Path(
        config_dir
        or environment.get("MCP_GATEWAY_CONFIG_DIR")
        or DEFAULT_CONFIG_DIR
    ).resolve()

    gateway_raw = _load_yaml(directory / "gateway.yaml")
    skills_raw = _load_yaml(directory / "skills.yaml")

    settings = _parse_settings(gateway_raw, environment)

    agents: dict[str, AgentPolicy] = {}
    for raw_agent in gateway_raw.get("agents") or ():
        policy = _parse_agent(raw_agent)
        if policy is not None:
            agents[policy.id] = policy

    skills: list[SkillSpec] = []
    for raw_skill in skills_raw.get("skills") or ():
        spec = _parse_skill(raw_skill)
        if spec is not None:
            skills.append(spec)

    routing = gateway_raw.get("capability_routing")
    sources = skills_raw.get("skill_sources")
    packages = skills_raw.get("skill_packages")
    return GatewayConfig(
        settings=settings,
        agents=agents,
        skills=tuple(skills),
        capability_routing=dict(routing) if isinstance(routing, Mapping) else {},
        skill_sources={
            str(name): dict(source)
            for name, source in (sources.items() if isinstance(sources, Mapping) else ())
            if isinstance(source, Mapping)
        },
        skill_packages=tuple(
            dict(package)
            for package in (packages if isinstance(packages, list) else ())
            if isinstance(package, Mapping)
        ),
    )


def config_dir_hint() -> str:
    return str(
        Path(os.environ.get("MCP_GATEWAY_CONFIG_DIR") or DEFAULT_CONFIG_DIR).resolve()
    )
