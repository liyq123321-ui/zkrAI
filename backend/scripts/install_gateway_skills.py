"""Install pinned, verified Agent Skill packages declared in MCP skills.yaml.

The default action is an offline catalogue check. Pass --install explicitly to
fetch packages. Downloaded Skill instructions and scripts are never executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / "app" / "mcp_gateway" / "config" / "skills.yaml"
DESTINATION = BACKEND / "skills" / "gateway_packages"
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe catalogue path: {value!r}")
    return path


def load_catalogue() -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    sources = data.get("skill_sources")
    packages = data.get("skill_packages")
    if not isinstance(sources, dict) or not isinstance(packages, list):
        raise ValueError("skill_sources and skill_packages must be present")
    for name, source in sources.items():
        if not SLUG.fullmatch(name) or not isinstance(source, dict):
            raise ValueError(f"Invalid source: {name!r}")
        url = source.get("url", "")
        if not isinstance(url, str) or not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+\.git", url):
            raise ValueError(f"Invalid GitHub URL for {name}")
        if not COMMIT.fullmatch(str(source.get("commit", ""))):
            raise ValueError(f"Invalid commit for {name}")
        if source.get("license_path") != "per_skill":
            _relative_path(str(source.get("license_path", "")))
    seen: set[str] = set()
    covered: set[int] = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("Each package must be a mapping")
        name = package.get("id", "")
        if not isinstance(name, str) or not SLUG.fullmatch(name) or name in seen:
            raise ValueError(f"Invalid or duplicate package id: {name!r}")
        seen.add(name)
        if package.get("source") not in sources:
            raise ValueError(f"Unknown source for {name}")
        _relative_path(str(package.get("path", "")))
        scenarios = package.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios or any(type(n) is not int or n not in range(1, 23) for n in scenarios):
            raise ValueError(f"Invalid scenarios for {name}")
        covered.update(scenarios)
    if covered != set(range(1, 23)):
        raise ValueError(f"Scenario coverage incomplete: {sorted(set(range(1, 23)) - covered)}")
    return sources, packages


def _git(*args: str, cwd: Path | None = None) -> str:
    command = ["git"]
    if os.name == "nt":
        command += ["-c", "http.sslBackend=openssl", "-c", "core.longpaths=true"]
    command += list(args)
    return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess.PIPE).strip()


def _digest(root: Path) -> str:
    entries: list[str] = []
    for item in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if item.is_symlink():
            raise ValueError(f"Symlink rejected: {item}")
        if item.is_file() and item.name != "SOURCE.json":
            content = item.read_bytes()
            if item.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".sh"}:
                try:
                    content = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
                except UnicodeDecodeError:
                    pass
            entries.append(f"{hashlib.sha256(content).hexdigest()}  ./{item.relative_to(root).as_posix()}\n")
    return hashlib.sha256("".join(entries).encode("utf-8")).hexdigest()


def _fetch(source: dict[str, str], paths: list[str], root: Path) -> None:
    _git("init", "-q", str(root))
    _git("remote", "add", "origin", source["url"], cwd=root)
    _git("sparse-checkout", "init", "--cone", cwd=root)
    _git("sparse-checkout", "set", *paths, cwd=root)
    _git("fetch", "--depth", "1", "--filter=blob:none", "origin", source["commit"], cwd=root)
    _git("checkout", "--detach", "FETCH_HEAD", cwd=root)
    if _git("rev-parse", "HEAD", cwd=root) != source["commit"]:
        raise ValueError("Fetched commit does not match the pinned commit")


def _source_license(root: Path, source: dict[str, str], package_dir: Path) -> Path:
    if source["license_path"] == "per_skill":
        candidates = [package_dir / "LICENSE.txt", package_dir / "LICENSE", package_dir / "LICENSE.md"]
        license_file = next((path for path in candidates if path.is_file()), None)
        if license_file is None:
            raise ValueError(f"No per-skill license: {package_dir}")
        license_text = license_file.read_text(encoding="utf-8", errors="replace")
        if "Apache License" not in license_text[:400] and "MIT License" not in license_text[:400]:
            raise ValueError(f"License does not permit automatic local copying: {package_dir}")
        return license_file
    license_file = root.joinpath(*_relative_path(source["license_path"]).parts)
    if not license_file.is_file():
        raise ValueError(f"Missing source license: {license_file}")
    license_text = license_file.read_text(encoding="utf-8", errors="replace")
    if "MIT License" not in license_text[:400] and "Apache License" not in license_text[:400]:
        raise ValueError(f"License does not permit automatic local copying: {license_file}")
    return license_file


def _install_one(root: Path, source: dict[str, str], package: dict[str, Any]) -> str:
    name = package["id"]
    origin = root.joinpath(*_relative_path(package["path"]).parts)
    if not (origin / "SKILL.md").is_file():
        raise ValueError(f"Missing SKILL.md for {name}")
    for item in origin.rglob("*"):
        if item.is_symlink():
            raise ValueError(f"Symlink rejected in {name}: {item}")
    license_file = _source_license(root, source, origin)
    target = DESTINATION / name
    if target.exists():
        source_file = target / "SOURCE.json"
        metadata = json.loads(source_file.read_text(encoding="utf-8")) if source_file.is_file() else {}
        if metadata.get("commit") == source["commit"] and metadata.get("tree_sha256") == _digest(target):
            return "already verified"
        raise ValueError(f"Existing package differs; resolve manually: {target}")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".skill-stage-", dir=DESTINATION) as staging:
        staged = Path(staging) / name
        shutil.copytree(origin, staged, symlinks=False)
        if license_file.parent != origin:
            shutil.copy2(license_file, staged / "UPSTREAM_LICENSE")
        digest = _digest(staged)
        tree = _git("rev-parse", f"HEAD:{package['path']}", cwd=root)
        metadata = {
            "name": name,
            "source": source["url"],
            "commit": source["commit"],
            "path": package["path"],
            "git_tree": tree,
            "tree_sha256": digest,
            "scenarios": package["scenarios"],
        }
        (staged / "SOURCE.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged.replace(target)
    return "installed"


def install(sources: dict[str, dict[str, str]], packages: list[dict[str, Any]]) -> None:
    with tempfile.TemporaryDirectory(prefix="firstflight-skill-download-") as staging:
        for source_name, source in sources.items():
            selected = [package for package in packages if package["source"] == source_name]
            if not selected:
                continue
            checkout = Path(staging) / source_name
            # Cone mode includes root files such as LICENSE automatically.
            paths = sorted({package["path"] for package in selected})
            _fetch(source, paths, checkout)
            for package in selected:
                print(f"{package['id']}: {_install_one(checkout, source, package)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Fetch and install the pinned Skill packages")
    args = parser.parse_args()
    sources, packages = load_catalogue()
    print(f"Catalogue valid: {len(packages)} packages, {len(sources)} pinned sources, 22 scenarios")
    if args.install:
        install(sources, packages)
    else:
        print("No download performed. Pass --install to fetch the pinned packages.")


if __name__ == "__main__":
    main()
