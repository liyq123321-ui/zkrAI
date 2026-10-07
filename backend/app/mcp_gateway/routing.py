"""Decide where one capability invocation belongs; never execute the call."""

from __future__ import annotations

from typing import Any, Mapping

from app.mcp_gateway.config import GatewayConfig

REQUIRED_METADATA = (
    "resource_scope",
    "credential_class",
    "compute_class",
    "governance",
    "latency_sensitivity",
    "control_type",
)
ALLOWED_VALUES = {
    "resource_scope": {"local_repo", "local_file", "localhost", "web", "saas", "database", "production"},
    "credential_class": {"none", "user_token", "paid_api", "production_secret"},
    "compute_class": {"light", "medium", "heavy"},
    "governance": {"none", "audit", "quota", "approval"},
    "latency_sensitivity": {"low", "normal", "high"},
    "control_type": {"worker", "human_gate", "dispatcher", "release_gate"},
}
ROUTES = {"LOCAL", "REMOTE", "ORCH"}


def decide_route(config: GatewayConfig, invocation: Mapping[str, Any]) -> dict[str, str]:
    """Apply ordered YAML rules to the current invocation's metadata."""

    capability = str(invocation.get("capability") or "").strip()
    if not capability:
        return {"route": "ORCH", "rule": "missing-capability"}
    for key in REQUIRED_METADATA:
        if str(invocation.get(key) or "") not in ALLOWED_VALUES[key]:
            return {"route": "ORCH", "rule": f"missing-or-invalid-{key}"}

    policy = config.capability_routing
    rules = policy.get("rules") if isinstance(policy, Mapping) else None
    for raw in rules if isinstance(rules, list) else ():
        if not isinstance(raw, Mapping):
            continue
        condition = raw.get("when")
        route = str(raw.get("route") or "").upper()
        if route not in ROUTES or not isinstance(condition, Mapping) or not condition:
            continue
        if all(
            key in REQUIRED_METADATA
            and str(invocation[key]) in (values if isinstance(values, list) else [values])
            for key, values in condition.items()
        ):
            return {"route": route, "rule": str(raw.get("id") or "unnamed")}

    fallback = str(policy.get("default_route") or "ORCH").upper() if isinstance(policy, Mapping) else "ORCH"
    return {"route": fallback if fallback in ROUTES else "ORCH", "rule": "default"}
