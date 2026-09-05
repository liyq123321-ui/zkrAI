from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    codex_binary: str
    codex_home: Path
    codex_cwd: Path
    codex_model: str | None = None
    codex_timeout_seconds: int = 2000
    codex_inactivity_timeout_seconds: float = 2000
    codex_skip_git_repo_check: bool = False
    codex_ignore_user_config: bool = False
    gitea_url: str | None = None
    gitea_token: str | None = None
    gitea_owner: str | None = None
    gitea_repo: str = "docs"
    gitea_base_branch: str = "main"
    gitea_review_branch_prefix: str = "prd-review/"
    gitea_timeout_seconds: int = 20
    identity_mode: str = "legacy"
    identity_actor_id: str | None = None
    identity_actor_header: str = "X-FirstFlight-Actor"
    cors_allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(__file__).resolve().parents[1]
        # Local development is documented around backend/.env. Loading it here
        # keeps every application entry point consistent, while real process
        # environment variables still take precedence (override=False).
        load_dotenv(root / ".env", override=False)
        cors_allowed_origins = tuple(
            origin.strip()
            for origin in os.getenv("FIRSTFLIGHT_CORS_ORIGINS", "").split(",")
            if origin.strip()
        )
        codex_timeout_seconds = int(os.getenv("CODEX_TIMEOUT_SECONDS", "2000"))
        return cls(
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{root / 'data' / 'gateway.db'}"),
            codex_binary=os.getenv("CODEX_BINARY", "codex"),
            codex_home=Path(os.getenv("CODEX_RUNTIME_HOME", str(Path.home() / ".codex"))).resolve(),
            codex_cwd=Path(os.getenv("CODEX_WORKING_DIRECTORY", str(root))).resolve(),
            codex_model=os.getenv("CODEX_MODEL") or None,
            codex_timeout_seconds=codex_timeout_seconds,
            codex_inactivity_timeout_seconds=float(
                os.getenv(
                    "CODEX_INACTIVITY_TIMEOUT_SECONDS",
                    str(codex_timeout_seconds),
                )
            ),
            codex_skip_git_repo_check=os.getenv(
                "CODEX_SKIP_GIT_REPO_CHECK", "false"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            codex_ignore_user_config=os.getenv(
                "CODEX_IGNORE_USER_CONFIG", "false"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            gitea_url=os.getenv("GITEA_URL"),
            gitea_token=os.getenv("GITEA_TOKEN"),
            gitea_owner=os.getenv("GITEA_OWNER"),
            gitea_repo=os.getenv("GITEA_REPO", "docs"),
            gitea_base_branch=os.getenv("GITEA_BASE_BRANCH", "main"),
            gitea_review_branch_prefix=os.getenv(
                "GITEA_REVIEW_BRANCH_PREFIX", "prd-review/"
            ),
            gitea_timeout_seconds=int(os.getenv("GITEA_TIMEOUT_SECONDS", "20")),
            identity_mode=os.getenv("FIRSTFLIGHT_IDENTITY_MODE", "legacy").strip().lower(),
            identity_actor_id=os.getenv("FIRSTFLIGHT_ACTOR_ID") or None,
            identity_actor_header=os.getenv(
                "FIRSTFLIGHT_TRUSTED_ACTOR_HEADER",
                os.getenv("FIRSTFLIGHT_ACTOR_HEADER", "X-FirstFlight-Actor"),
            ).strip(),
            cors_allowed_origins=cors_allowed_origins,
        )
