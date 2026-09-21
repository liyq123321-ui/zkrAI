"""Auth runtime context shared between resolver, dependencies, and routers."""

_AUTH_SECRET = ""
_AUTH_TTL_SECONDS = 60 * 60 * 12


def configure_auth(secret: str, ttl_seconds: int = 60 * 60 * 12) -> None:
    """Install the signing secret and token lifetime for this process."""

    global _AUTH_SECRET, _AUTH_TTL_SECONDS
    _AUTH_SECRET = secret
    if ttl_seconds and ttl_seconds > 0:
        _AUTH_TTL_SECONDS = int(ttl_seconds)


def auth_secret() -> str:
    return _AUTH_SECRET


def auth_token_ttl() -> int:
    return _AUTH_TTL_SECONDS
