"""JSON-RPC dispatch core implementing MCP Streamable HTTP semantics.

Every listing and invocation is filtered through the caller's exposure policy.
Transport concerns (headers, SSE, status codes) live in ``router.py``; this
module only turns a JSON-RPC message into a JSON-RPC response.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.mcp_gateway.adapters.base import AdapterContext
from app.mcp_gateway.adapters.registry import AdapterRegistry
from app.mcp_gateway.audit import Auditor
from app.mcp_gateway.auth import AgentIdentity, Authenticator, bearer_token
from app.mcp_gateway.config import (
    SUPPORTED_PROTOCOL_VERSIONS,
    AgentPolicy,
    GatewayConfig,
    SkillSpec,
    load_gateway_config,
)
from app.mcp_gateway.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    RATE_LIMITED,
    SKILL_NOT_FOUND,
    UNAUTHORIZED,
    UPSTREAM_UNAVAILABLE,
    McpError,
    error_response,
    ok_response,
)
from app.mcp_gateway.exposure import ExposureEngine
from app.mcp_gateway.ratelimit import SlidingWindowLimiter

LOGGER = logging.getLogger(__name__)

_IF_BLOCK = re.compile(r"\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*endif\s*%\}", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_TEMPLATE_VAR = re.compile(r"\{(\w+)\}")

_TYPE_CHECKS: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def validate_instance(instance: Any, schema: Any, path: str = "") -> list[str]:
    """Minimal JSON-Schema validation (type/required/enum/nested objects)."""

    errors: list[str] = []
    if not isinstance(schema, Mapping):
        return errors
    expected = schema.get("type")
    if isinstance(expected, str):
        checker = _TYPE_CHECKS.get(expected)
        if checker is not None:
            if expected == "integer":
                valid = isinstance(instance, int) and not isinstance(instance, bool)
            elif expected == "boolean":
                valid = isinstance(instance, bool)
            else:
                valid = isinstance(instance, checker)
            if not valid:
                errors.append(f"{path or '参数'}: 期望类型 {expected}，实际 {type(instance).__name__}")
                return errors
    enum = schema.get("enum")
    if isinstance(enum, (list, tuple)) and instance not in enum:
        errors.append(f"{path or '参数'}: 取值必须在 {list(enum)} 内")
    if isinstance(instance, Mapping):
        properties = schema.get("properties") or {}
        for key in schema.get("required") or ():
            if key not in instance:
                errors.append(f"缺少必填参数: {key}")
        for key, value in instance.items():
            sub_schema = properties.get(key) if isinstance(properties, Mapping) else None
            if isinstance(sub_schema, Mapping):
                errors.extend(validate_instance(value, sub_schema, str(key)))
    return errors


def render_template(template: str, args: Mapping[str, Any]) -> str:
    """Render ``{{var}}`` placeholders plus ``{% if var %}…{% endif %}`` blocks."""

    def _replace_block(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        if args.get(name):
            return _PLACEHOLDER.sub(
                lambda m: str(args.get(m.group(1), "")), body
            )
        return ""

    text = _IF_BLOCK.sub(_replace_block, template)
    return _PLACEHOLDER.sub(lambda m: str(args.get(m.group(1), "")), text)


def uri_pattern(uri_template: str) -> str:
    return _TEMPLATE_VAR.sub("*", uri_template)


@dataclass
class ToolBinding:
    name: str
    skill: SkillSpec
    upstream_name: str | None = None
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayResponse:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    sse: bool = False


class McpGateway:
    """Stateless-per-request MCP server core (auth on every request)."""

    def __init__(
        self,
        config: GatewayConfig,
        authenticator: Authenticator,
        limiter: SlidingWindowLimiter,
        auditor: Auditor,
        registry: AdapterRegistry,
        exposure: ExposureEngine,
    ) -> None:
        self.config = config
        self.authenticator = authenticator
        self.limiter = limiter
        self.auditor = auditor
        self.registry = registry
        self.exposure = exposure
        self._sessions: dict[str, float] = {}

    # --- construction ---------------------------------------------------------
    @classmethod
    def build(
        cls,
        config: GatewayConfig | None = None,
        *,
        jwt_secret: str = "",
        config_dir: str | None = None,
    ) -> "McpGateway":
        config = config or load_gateway_config(config_dir)
        settings = config.settings
        authenticator = Authenticator(config, jwt_secret=jwt_secret)
        limiter = SlidingWindowLimiter(
            settings.per_agent_per_minute, settings.ratelimit_enabled
        )
        auditor = Auditor(
            settings.audit_enabled,
            settings.audit_dir,
            settings.audit_memory,
            settings.audit_arg_preview_chars,
        )
        exposure = ExposureEngine(config, None)  # type: ignore[arg-type]
        ctx = AdapterContext(
            config=config,
            exposure=exposure,
            limiter=limiter,
            auditor=auditor,
            mock=settings.mock_upstream,
        )
        registry = AdapterRegistry(ctx)
        exposure = ExposureEngine(config, registry)
        ctx.exposure = exposure
        return cls(config, authenticator, limiter, auditor, registry, exposure)

    def reload(self, config_dir: str | None = None) -> GatewayConfig:
        """Hot-reload both YAML files and swap the runtime in place."""

        fresh = load_gateway_config(config_dir)
        rebuilt = McpGateway.build(fresh, jwt_secret=self.authenticator._jwt_secret)
        self.config = rebuilt.config
        self.authenticator = rebuilt.authenticator
        self.limiter = rebuilt.limiter
        self.auditor = rebuilt.auditor
        self.registry = rebuilt.registry
        self.exposure = rebuilt.exposure
        return self.config

    # --- transport glue -------------------------------------------------------
    def _session_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        if not self.config.settings.sessions_enabled:
            return {}
        existing = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
        if existing and existing in self._sessions:
            return {"Mcp-Session-Id": existing}
        return {}

    def new_session(self) -> str:
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = time.time()
        return session_id

    def drop_session(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    # --- dispatch -------------------------------------------------------------
    async def handle(
        self, headers: Mapping[str, str], payload: Any
    ) -> GatewayResponse:
        if isinstance(payload, list):
            responses: list[dict[str, Any]] = []
            for message in payload:
                response = await self._handle_message(headers, message)
                if response is not None:
                    responses.append(response)
            if not responses:
                return GatewayResponse(202, None, self._session_headers(headers))
            body: Any = responses[0] if len(responses) == 1 else responses
            return GatewayResponse(200, body, self._session_headers(headers))
        response = await self._handle_message(headers, payload)
        if response is None:
            return GatewayResponse(202, None, self._session_headers(headers))
        return GatewayResponse(200, response, self._session_headers(headers))

    async def _handle_message(
        self, headers: Mapping[str, str], message: Any
    ) -> dict[str, Any] | None:
        if not isinstance(message, Mapping) or message.get("jsonrpc") != "2.0":
            return error_response(None, INVALID_REQUEST, "请求不是合法的 JSON-RPC 2.0 报文。")
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params")
        if not isinstance(params, Mapping):
            params = {}
        if not isinstance(method, str):
            return error_response(request_id, INVALID_REQUEST, "缺少 method 字段。")
        if method.startswith("notifications/") or (
            method.endswith("/initialized")
        ):
            return None

        try:
            if method == "initialize":
                result = await self._initialize(params)
                return ok_response(request_id, result)
            if method == "ping":
                return ok_response(request_id, {})
            if method == "logging/setLevel":
                return ok_response(request_id, {})

            identity = self.authenticator.resolve(bearer_token(headers))
            policy = self._policy(identity)
            decision = self.limiter.check(
                identity.agent_id, policy.rate_limit_per_minute
            )
            if not decision.allowed:
                raise McpError(
                    RATE_LIMITED,
                    f"超出配额：{decision.limit} 次/分钟。",
                    data={
                        "limit": decision.limit,
                        "retry_after": round(decision.retry_after, 2),
                    },
                )
            handler = self._handlers().get(method)
            if handler is None:
                return error_response(
                    request_id, METHOD_NOT_FOUND, f"不支持的方法: {method}"
                )
            result = await handler(identity, params)
            return ok_response(request_id, result)
        except McpError as exc:
            return exc.to_response(request_id)
        except Exception as exc:  # noqa: BLE001 - never leak a stack to clients
            LOGGER.exception("mcp-gateway: 处理 %s 时发生内部错误", method)
            return error_response(
                request_id, INTERNAL_ERROR, f"网关内部错误：{type(exc).__name__}"
            )

    def _handlers(self) -> dict[str, Callable[..., Any]]:
        return {
            "tools/list": self._tools_list,
            "tools/call": self._tools_call,
            "resources/list": self._resources_list,
            "resources/read": self._resources_read,
            "prompts/list": self._prompts_list,
            "prompts/get": self._prompts_get,
        }

    # --- MCP methods ----------------------------------------------------------
    async def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        settings = self.config.settings
        requested = str(params.get("protocolVersion") or "")
        version = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else settings.protocol_version
        )
        return {
            "protocolVersion": version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
                "logging": {},
            },
            "serverInfo": {"name": settings.server_name, "version": settings.server_version},
            "instructions": settings.instructions,
        }

    async def catalogue(self, policy: AgentPolicy) -> dict[str, ToolBinding]:
        """Authorized tool catalogue for one agent (deny-all filtered)."""

        bindings: dict[str, ToolBinding] = {}
        for skill in self.config.tools:
            adapter = self.registry.get(skill)
            if self.registry.is_dynamic(skill):
                for entry in await adapter.dynamic_tools(skill):
                    name = str(entry.get("name") or "")
                    if not name or not self.exposure.allows_tool(policy, name):
                        continue
                    bindings[name] = ToolBinding(
                        name=name,
                        skill=skill,
                        upstream_name=adapter.upstream_tool_name(skill, name),
                        description=str(entry.get("description") or ""),
                        input_schema=dict(entry.get("inputSchema") or {"type": "object", "properties": {}}),
                    )
                continue
            name = skill.tool_name
            if self.exposure.allows_tool(policy, name):
                bindings[name] = ToolBinding(
                    name=name,
                    skill=skill,
                    description=skill.description,
                    input_schema=skill.input_schema,
                )
        return bindings

    async def _tools_list(
        self, identity: AgentIdentity, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        policy = self._policy(identity)
        bindings = await self.catalogue(policy)
        return {
            "tools": [
                {
                    "name": binding.name,
                    "description": binding.description,
                    "inputSchema": binding.input_schema,
                }
                for binding in bindings.values()
            ]
        }

    async def _tools_call(
        self, identity: AgentIdentity, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        policy = self._policy(identity)
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            raise McpError(INVALID_PARAMS, "tools/call 缺少工具名。")
        if not isinstance(args, Mapping):
            raise McpError(INVALID_PARAMS, "tools/call 的 arguments 必须是对象。")

        bindings = await self.catalogue(policy)
        binding = bindings.get(name)
        if binding is None:
            known = await self._tool_exists(name)
            raise McpError(
                UNAUTHORIZED if known else SKILL_NOT_FOUND,
                (
                    f"agent '{identity.agent_id}' 无权调用工具 '{name}'。"
                    if known
                    else f"工具 '{name}' 不存在。"
                ),
                data={"agent_id": identity.agent_id, "tool": name},
            )

        errors = validate_instance(dict(args), binding.input_schema)
        if errors:
            raise McpError(
                INVALID_PARAMS, "参数校验失败。", data={"errors": errors}
            )

        adapter = self.registry.get(binding.skill)
        call_args = dict(args)
        if binding.upstream_name:
            call_args["__upstream_tool__"] = binding.upstream_name

        started = time.perf_counter()
        status = "ok"
        error_text: str | None = None
        try:
            result = await adapter.call_tool(binding.skill, call_args, agent=identity)
        except McpError as exc:
            status = "error"
            error_text = exc.message
            raise
        except Exception as exc:  # noqa: BLE001
            status = "error"
            error_text = f"{type(exc).__name__}: {exc}"
            raise McpError(
                UPSTREAM_UNAVAILABLE,
                f"调用工具 '{name}' 失败：{error_text}",
                data={"tool": name},
            ) from exc
        finally:
            latency = (time.perf_counter() - started) * 1000
            self.auditor.record(
                agent_id=identity.agent_id,
                method="tools/call",
                name=name,
                args=args,
                status=status,
                latency_ms=latency,
                error=error_text,
            )
        size = len(json.dumps(result.content, ensure_ascii=False, default=str))
        self.auditor.record(
            agent_id=identity.agent_id,
            method="tools/call.result",
            name=name,
            args={},
            result_size=size,
            latency_ms=0,
        )
        return {"content": result.content, "isError": result.is_error}

    async def _resources_list(
        self, identity: AgentIdentity, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        policy = self._policy(identity)
        resources: list[dict[str, Any]] = []
        templates: list[dict[str, Any]] = []
        for skill in self.config.resources:
            if not self._resource_visible(policy, skill):
                continue
            if skill.uri_template:
                templates.append(
                    {
                        "uriTemplate": skill.uri_template,
                        "name": skill.name,
                        "description": skill.description,
                        "mimeType": skill.mime_type,
                    }
                )
            adapter = self.registry.get(skill)
            try:
                items = await adapter.list_resources(skill, agent=identity)
            except McpError:
                continue
            for item in items:
                uri = str(item.get("uri") or "")
                if uri and self.exposure.allows_resource(policy, uri, skill):
                    resources.append(item)
        return {"resources": resources, "resourceTemplates": templates}

    async def _resources_read(
        self, identity: AgentIdentity, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        policy = self._policy(identity)
        uri = str(params.get("uri") or "")
        if not uri:
            raise McpError(INVALID_PARAMS, "resources/read 缺少 uri。")
        skill = self._resource_skill(uri)
        if skill is None or not self._resource_visible(policy, skill):
            raise McpError(
                UNAUTHORIZED,
                f"agent '{identity.agent_id}' 无权读取资源 '{uri}'。",
                data={"agent_id": identity.agent_id, "uri": uri},
            )
        if not self.exposure.allows_resource(policy, uri, skill):
            raise McpError(UNAUTHORIZED, f"资源 '{uri}' 不在授权范围内。")
        adapter = self.registry.get(skill)
        started = time.perf_counter()
        try:
            contents = await adapter.read_resource(skill, uri, agent=identity)
        finally:
            self.auditor.record(
                agent_id=identity.agent_id,
                method="resources/read",
                name=uri,
                args={"uri": uri},
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        return {"contents": contents}

    async def _prompts_list(
        self, identity: AgentIdentity, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        policy = self._policy(identity)
        return {
            "prompts": [
                {
                    "name": skill.prompt_name,
                    "description": skill.description,
                    "arguments": [
                        dict(arg) for arg in skill.arguments
                    ],
                }
                for skill in self.config.prompts
                if self.exposure.allows_prompt(policy, skill.prompt_name)
            ]
        }

    async def _prompts_get(
        self, identity: AgentIdentity, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        policy = self._policy(identity)
        name = str(params.get("name") or "")
        raw_args = params.get("arguments") or {}
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        skill = next(
            (s for s in self.config.prompts if s.prompt_name == name), None
        )
        if skill is None or not self.exposure.allows_prompt(policy, name):
            raise McpError(
                UNAUTHORIZED,
                f"agent '{identity.agent_id}' 无权使用提示词 '{name}'。",
                data={"agent_id": identity.agent_id, "prompt": name},
            )
        for arg in skill.arguments:
            if arg.get("required") and arg.get("name") not in args:
                raise McpError(
                    INVALID_PARAMS,
                    f"提示词 '{name}' 缺少必填参数: {arg.get('name')}",
                    data={"errors": [f"缺少必填参数: {arg.get('name')}"]},
                )
        self.auditor.record(
            agent_id=identity.agent_id,
            method="prompts/get",
            name=name,
            args=args,
        )
        text = render_template(skill.template, args)
        return {
            "description": skill.description,
            "messages": [
                {"role": "user", "content": {"type": "text", "text": text}}
            ],
        }

    # --- helpers --------------------------------------------------------------
    def _policy(self, identity: AgentIdentity) -> AgentPolicy:
        policy = self.config.agents.get(identity.agent_id)
        if policy is None or not policy.enabled:  # pragma: no cover - auth guards
            raise McpError(
                UNAUTHORIZED, f"agent '{identity.agent_id}' 未注册或已禁用。"
            )
        return policy

    def _resource_visible(self, policy: AgentPolicy, skill: SkillSpec) -> bool:
        return self.exposure.resource_visible(policy, skill)

    def _resource_skill(self, uri: str) -> SkillSpec | None:
        import fnmatch

        for skill in self.config.resources:
            if not skill.uri_template:
                continue
            if fnmatch.fnmatchcase(uri, uri_pattern(skill.uri_template)):
                return skill
        return None

    async def _tool_exists(self, name: str) -> bool:
        for skill in self.config.tools:
            if not self.registry.is_dynamic(skill):
                if skill.tool_name == name:
                    return True
            elif name.startswith(f"{skill.id}."):
                return True
        return False
