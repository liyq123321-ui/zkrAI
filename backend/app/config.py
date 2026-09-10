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
    agent_api_timeout_seconds: float = 2000
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    zhipu_api_key: str | None = None
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_model: str = "glm-5.2"
    moonshot_api_key: str | None = None
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    moonshot_model: str = "kimi-k3"
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
    magic_mcp_enabled: bool = False
    magic_mcp_url: str = "https://21st.dev/api/mcp"
    magic_mcp_api_key_env: str = "API_KEY_21ST"
    magic_mcp_tool_timeout_seconds: int = 300

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
        magic_mcp_api_key_env = (
            os.getenv("MAGIC_MCP_API_KEY_ENV", "API_KEY_21ST").strip()
            or "API_KEY_21ST"
        )
        magic_mcp_enabled_raw = os.getenv("MAGIC_MCP_ENABLED", "").strip().lower()
        magic_mcp_enabled = (
            bool(os.getenv(magic_mcp_api_key_env))
            if not magic_mcp_enabled_raw
            else magic_mcp_enabled_raw in {"1", "true", "yes", "on"}
        )
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
            agent_api_timeout_seconds=float(
                os.getenv("AGENT_API_TIMEOUT_SECONDS", str(codex_timeout_seconds))
            ),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ).rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip(),
            zhipu_api_key=os.getenv("ZHIPU_API_KEY") or None,
            zhipu_base_url=os.getenv(
                "ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"
            ).rstrip("/"),
            zhipu_model=os.getenv("ZHIPU_MODEL", "glm-5.2").strip(),
            moonshot_api_key=os.getenv("MOONSHOT_API_KEY") or None,
            moonshot_base_url=os.getenv(
                "MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"
            ).rstrip("/"),
            moonshot_model=os.getenv("MOONSHOT_MODEL", "kimi-k3").strip(),
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
            magic_mcp_enabled=magic_mcp_enabled,
            magic_mcp_url=os.getenv(
                "MAGIC_MCP_URL", "https://21st.dev/api/mcp"
            ).strip(),
            magic_mcp_api_key_env=magic_mcp_api_key_env,
            magic_mcp_tool_timeout_seconds=int(
                os.getenv("MAGIC_MCP_TOOL_TIMEOUT_SECONDS", "300")
            ),
        )
