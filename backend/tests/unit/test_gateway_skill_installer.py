"""Offline checks for the deferred Agent Skill installer."""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts import install_gateway_skills as installer


def test_local_install_is_pinned_and_detects_tampering(tmp_path, monkeypatch):
    repository = tmp_path / "source"
    skill = repository / "skills" / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: example\n---\n", encoding="utf-8")
    (repository / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"],
        cwd=repository,
        check=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    checkout = tmp_path / "checkout"
    installer._fetch({"url": str(repository), "commit": commit}, ["skills/example"], checkout)
    assert (checkout / "skills" / "example" / "SKILL.md").is_file()
    source = {"url": "https://github.com/example/skills.git", "commit": commit, "license_path": "LICENSE"}
    package = {"id": "example", "path": "skills/example", "scenarios": [1]}
    monkeypatch.setattr(installer, "DESTINATION", tmp_path / "installed")

    assert installer._install_one(repository, source, package) == "installed"
    installed = installer.DESTINATION / "example"
    assert json.loads((installed / "SOURCE.json").read_text(encoding="utf-8"))["commit"] == commit
    assert installer._install_one(repository, source, package) == "already verified"
    (installed / "SKILL.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        installer._install_one(repository, source, package)


def test_restricted_license_is_not_copied(tmp_path):
    package = tmp_path / "skill"
    package.mkdir()
    (package / "LICENSE.txt").write_text("ADDITIONAL RESTRICTIONS: no copies outside Services", encoding="utf-8")
    with pytest.raises(ValueError, match="License does not permit"):
        installer._source_license(tmp_path, {"license_path": "per_skill"}, package)
