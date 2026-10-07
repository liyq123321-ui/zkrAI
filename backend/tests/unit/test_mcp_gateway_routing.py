"""Capability-level routing and pinned Skill catalogue checks."""

from __future__ import annotations

from app.mcp_gateway.config import load_gateway_config
from app.mcp_gateway.routing import decide_route
from app.mcp_gateway.adapters.skill_package import _digest, verified_package
import app.mcp_gateway.adapters.skill_package as package_module
import json


def _invocation(**overrides):
    values = {
        "capability": "read-repo",
        "resource_scope": "local_repo",
        "credential_class": "none",
        "compute_class": "light",
        "governance": "audit",
        "latency_sensitivity": "high",
        "control_type": "worker",
    }
    values.update(overrides)
    return values


def test_routing_precedence_and_missing_metadata():
    config = load_gateway_config(env={})
    assert decide_route(config, _invocation()) == {"route": "LOCAL", "rule": "local-resource"}
    assert decide_route(config, _invocation(resource_scope="web"))["route"] == "REMOTE"
    assert decide_route(config, _invocation(credential_class="paid_api"))["route"] == "REMOTE"
    assert decide_route(config, _invocation(compute_class="heavy"))["route"] == "REMOTE"
    assert decide_route(config, _invocation(governance="approval", resource_scope="web"))["route"] == "ORCH"
    assert decide_route(config, _invocation(control_type="release_gate", resource_scope="web"))["route"] == "ORCH"
    assert decide_route(config, _invocation(resource_scope="unknown"))["route"] == "ORCH"


def test_skill_catalogue_covers_all_scenarios_without_enabling_packages():
    config = load_gateway_config(env={})
    assert len(config.skill_packages) == 56
    assert len({package["id"] for package in config.skill_packages}) == 56
    assert {scenario for package in config.skill_packages for scenario in package["scenarios"]} == set(range(1, 23))
    assert config.agents["worker-default"].allowed_tools == ("gateway_ping",)


def test_skill_resource_requires_matching_source_and_checksum(tmp_path, monkeypatch):
    monkeypatch.setattr(package_module, "PACKAGE_ROOT", tmp_path)
    package = tmp_path / "example"
    package.mkdir()
    (package / "SKILL.md").write_text("---\nname: example\n---\n", encoding="utf-8")
    source = {"url": "https://github.com/example/skills.git", "commit": "a" * 40}
    (package / "SOURCE.json").write_text(
        json.dumps({"name": "example", "source": source["url"], "commit": source["commit"], "tree_sha256": _digest(package)}),
        encoding="utf-8",
    )
    assert verified_package("example", source) == package
    (package / "SKILL.md").write_text("tampered", encoding="utf-8")
    assert verified_package("example", source) is None
