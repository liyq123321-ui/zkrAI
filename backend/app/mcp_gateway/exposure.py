"""裁剪: per-agent whitelist filtering (default deny-all).

Every listing and every invocation goes through this engine. Patterns are
matched with :func:`fnmatch.fnmatchcase`, so ``spaces://collab/knowledge/*``
and ``code-search.*`` behave as expected.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

from app.mcp_gateway.config import (
    KIND_PROMPT,
    KIND_RESOURCE,
    KIND_TOOL,
    AgentPolicy,
    GatewayConfig,
    SkillSpec,
)

CASCADE_ADAPTER = "external-mcp"


def matches(patterns: tuple[str, ...], value: str) -> bool:
    """True when ``value`` matches any pattern (case-sensitive fnmatch)."""

    return any(fnmatchcase(value, pattern) for pattern in patterns)


class ExposureEngine:
    """Resolve what a given agent may see and call."""

    def __init__(self, config: GatewayConfig, resolver=None) -> None:
        self._config = config
        self._resolve = resolver

    @staticmethod
    def matches_pattern(patterns: tuple[str, ...], value: str) -> bool:
        return matches(patterns, value)

    # --- tools -----------------------------------------------------------------
    def tools_for(self, agent: AgentPolicy) -> tuple[SkillSpec, ...]:
        allowed: list[SkillSpec] = []
        for skill in self._config.tools:
            if skill.adapter == CASCADE_ADAPTER:
                # 级联工具在 catalogue 阶段展开；这里用前缀通配判断是否放行
                if self.allows_tool(agent, f"{skill.id}.*"):
                    allowed.append(skill)
                continue
            if self.allows_tool(agent, skill.tool_name):
                allowed.append(skill)
        return tuple(allowed)

    def allows_tool(self, agent: AgentPolicy, name: str) -> bool:
        return matches(agent.allowed_tools, name)

    # --- resources -------------------------------------------------------------
    def resources_for(self, agent: AgentPolicy) -> tuple[SkillSpec, ...]:
        return tuple(
            skill
            for skill in self._config.resources
            if self.resource_visible(agent, skill)
        )

    def resource_visible(self, agent: AgentPolicy, skill: SkillSpec) -> bool:
        """Whether the agent may see this resource skill at all.

        Whitelists are written against concrete URIs
        (``spaces://collab/knowledge/*``), so the skill's URI template is
        expanded over its declared scopes before matching.
        """

        return any(
            matches(agent.allowed_resources, candidate)
            for candidate in self.resource_candidates(skill)
        )

    @staticmethod
    def resource_candidates(skill: SkillSpec) -> list[str]:
        candidates = [skill.id]
        template = skill.uri_template
        if not template:
            return candidates
        candidates.append(template)
        for scope in (skill.options or {}).get("scopes") or ():
            expanded = template.replace("{scope}", str(scope))
            while "{" in expanded and "}" in expanded:
                head = expanded.index("{")
                tail = expanded.index("}", head)
                expanded = expanded[:head] + "*" + expanded[tail + 1:]
            candidates.append(expanded)
        return candidates

    def allows_resource(
        self, agent: AgentPolicy, uri: str, skill: SkillSpec | None = None
    ) -> bool:
        """Authorize one concrete URI.

        A URI is readable when it matches an allowed pattern, or when the
        agent whitelisted the whole skill (by id or by URI template).
        Note: being allowed *one* scope of a resource never grants the others.
        """

        if matches(agent.allowed_resources, uri):
            return True
        if skill is None:
            return False
        return matches(agent.allowed_resources, skill.id) or bool(
            skill.uri_template and matches(agent.allowed_resources, skill.uri_template)
        )

    # --- prompts ---------------------------------------------------------------
    def prompts_for(self, agent: AgentPolicy) -> tuple[SkillSpec, ...]:
        return tuple(
            skill
            for skill in self._config.prompts
            if self.allows_prompt(agent, skill.prompt_name)
        )

    def allows_prompt(self, agent: AgentPolicy, name: str) -> bool:
        return matches(agent.allowed_prompts, name)

    def summary(self, agent: AgentPolicy) -> dict[str, list[str]]:
        return {
            "tools": sorted({s.id for s in self.tools_for(agent)}),
            "resources": sorted({s.id for s in self.resources_for(agent)}),
            "prompts": sorted({s.prompt_name for s in self.prompts_for(agent)}),
        }


KINDS = (KIND_TOOL, KIND_RESOURCE, KIND_PROMPT)
