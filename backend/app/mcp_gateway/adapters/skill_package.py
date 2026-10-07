"""Read integrity-checked, locally installed Agent Skill packages as MCP resources."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.mcp_gateway.adapters.base import BaseAdapter, resource_text_content
from app.mcp_gateway.config import BACKEND_ROOT, SkillSpec
from app.mcp_gateway.errors import McpError, SKILL_NOT_FOUND

PACKAGE_ROOT = BACKEND_ROOT / "skills" / "gateway_packages"
PACKAGE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _digest(root: Path) -> str:
    entries: list[str] = []
    for item in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if item.is_symlink():
            raise ValueError("symlink in Skill package")
        if item.is_file() and item.name != "SOURCE.json":
            content = item.read_bytes()
            if item.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".sh"}:
                try:
                    content = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
                except UnicodeDecodeError:
                    pass
            entries.append(f"{hashlib.sha256(content).hexdigest()}  ./{item.relative_to(root).as_posix()}\n")
    return hashlib.sha256("".join(entries).encode("utf-8")).hexdigest()


def verified_package(name: str, source: dict[str, str]) -> Path | None:
    package = PACKAGE_ROOT / name
    if not PACKAGE_ID.fullmatch(name) or not package.is_dir():
        return None
    try:
        metadata = json.loads((package / "SOURCE.json").read_text(encoding="utf-8"))
        if (
            metadata.get("name") != name
            or metadata.get("commit") != source["commit"]
            or metadata.get("source") != source["url"]
            or metadata.get("tree_sha256") != _digest(package)
            or not (package / "SKILL.md").is_file()
        ):
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return package


class SkillPackageAdapter(BaseAdapter):
    name = "skill-package"

    def _packages(self) -> list[dict[str, Any]]:
        config = self.ctx.config
        return [
            package
            for package in config.skill_packages
            if isinstance(package.get("id"), str)
            and isinstance(package.get("source"), str)
            and package["source"] in config.skill_sources
        ]

    async def list_resources(self, skill: SkillSpec, *, agent: Any = None) -> list[dict[str, Any]]:
        result = []
        for package in self._packages():
            name = package["id"]
            if verified_package(name, self.ctx.config.skill_sources[package["source"]]):
                result.append({"uri": f"skill://{name}", "name": name, "mimeType": "text/markdown"})
        return result

    async def read_resource(self, skill: SkillSpec, uri: str, *, agent: Any = None) -> list[dict[str, Any]]:
        name = uri.removeprefix("skill://") if uri.startswith("skill://") else ""
        package = next((p for p in self._packages() if p["id"] == name), None)
        root = (
            verified_package(name, self.ctx.config.skill_sources[package["source"]])
            if package is not None
            else None
        )
        if root is None:
            raise McpError(SKILL_NOT_FOUND, f"Agent Skill '{name}' 未安装或完整性校验失败。")
        text = (root / "SKILL.md").read_text(encoding="utf-8")
        if len(text) > 128_000:
            raise McpError(SKILL_NOT_FOUND, f"Agent Skill '{name}' 超过读取上限。")
        return [resource_text_content(uri, text, "text/markdown")]
