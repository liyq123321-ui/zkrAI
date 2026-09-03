from pathlib import Path

import app.config as config_module
from app.config import Settings


def test_settings_use_codex_home_without_overriding_process_home(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'gateway.db'}",
        codex_binary="codex",
        codex_home=tmp_path / "codex-home",
        codex_cwd=tmp_path,
        codex_timeout_seconds=90,
    )
    assert settings.codex_home == tmp_path / "codex-home"
    assert settings.codex_cwd == tmp_path


def test_settings_load_backend_dotenv_without_overriding_process_environment(monkeypatch):
    calls: list[tuple[Path, bool]] = []

    def fake_load_dotenv(path: Path, *, override: bool) -> None:
        calls.append((path, override))

    monkeypatch.setattr(config_module, "load_dotenv", fake_load_dotenv)
    monkeypatch.setenv("GITEA_OWNER", "process-owner")

    settings = Settings.from_env()

    assert calls == [(Path(config_module.__file__).resolve().parents[1] / ".env", False)]
    assert settings.gitea_owner == "process-owner"


def test_settings_can_allow_codex_outside_git_repository(monkeypatch):
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("CODEX_SKIP_GIT_REPO_CHECK", "true")
    monkeypatch.setenv("CODEX_IGNORE_USER_CONFIG", "true")

    settings = Settings.from_env()

    assert settings.codex_skip_git_repo_check is True
    assert settings.codex_ignore_user_config is True
    assert settings.codex_model == "gpt-5.6-luna"


def test_settings_load_gitea_review_configuration_from_environment(monkeypatch):
    """A deployment can configure Gitea without changing Codex settings."""

    monkeypatch.setenv("GITEA_URL", "https://gitea.example")
    monkeypatch.setenv("GITEA_TOKEN", "secret")
    monkeypatch.setenv("GITEA_OWNER", "product")
    monkeypatch.setenv("GITEA_REPO", "requirements")
    monkeypatch.setenv("GITEA_BASE_BRANCH", "trunk")
    monkeypatch.setenv("GITEA_REVIEW_BRANCH_PREFIX", "reviews/")
    monkeypatch.setenv("GITEA_TIMEOUT_SECONDS", "17")

    settings = Settings.from_env()

    assert settings.gitea_url == "https://gitea.example"
    assert settings.gitea_token == "secret"
    assert settings.gitea_owner == "product"
    assert settings.gitea_repo == "requirements"
    assert settings.gitea_base_branch == "trunk"
    assert settings.gitea_review_branch_prefix == "reviews/"
    assert settings.gitea_timeout_seconds == 17


def test_settings_load_identity_and_cors_configuration(monkeypatch):
    monkeypatch.setenv("FIRSTFLIGHT_IDENTITY_MODE", "local")
    monkeypatch.setenv("FIRSTFLIGHT_ACTOR_ID", "owner-1")
    monkeypatch.setenv(
        "FIRSTFLIGHT_CORS_ORIGINS",
        "http://127.0.0.1:3000, https://firstflight.example ",
    )

    settings = Settings.from_env()

    assert settings.identity_mode == "local"
    assert settings.identity_actor_id == "owner-1"
    assert settings.identity_actor_header == "X-FirstFlight-Actor"
    assert settings.cors_allowed_origins == (
        "http://127.0.0.1:3000",
        "https://firstflight.example",
    )
