"""Resolve the trusted actor at the HTTP boundary."""

from fastapi import HTTPException, Request, status

from app.auth import auth_secret
from app.config import Settings
from app.security import InvalidToken, decode_access_token


class ActorResolver:
    """Keep browser-supplied identity outside the application trust boundary."""

    def __init__(self, settings: Settings):
        self._mode = settings.identity_mode
        self._local_actor_id = settings.identity_actor_id
        self._header = settings.identity_actor_header

        if self._mode not in {"legacy", "local", "proxy", "auth"}:
            raise ValueError(f"unsupported identity mode: {self._mode}")
        if not self._header:
            raise ValueError("FIRSTFLIGHT_ACTOR_HEADER must not be empty")
        if self._mode == "auth" and not auth_secret():
            raise ValueError(
                "FIRSTFLIGHT_AUTH_SECRET must be set when identity mode is 'auth'"
            )

    def resolve(self, request: Request, supplied_actor_id: str | None) -> str:
        if self._mode == "auth":
            token = self._bearer_token(request)
            if not token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "IDENTITY_REQUIRED",
                        "message": "需要登录后才能访问。",
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
            try:
                claims = decode_access_token(auth_secret(), token)
            except InvalidToken:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "INVALID_TOKEN",
                        "message": "登录凭证无效或已过期。",
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return claims["sub"]

        if self._mode == "local":
            if self._local_actor_id:
                return self._local_actor_id
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "IDENTITY_NOT_CONFIGURED",
                    "message": "The local trusted actor is not configured.",
                },
            )

        if self._mode == "proxy":
            actor_id = request.headers.get(self._header, "").strip()
            if actor_id:
                return actor_id
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "IDENTITY_REQUIRED",
                    "message": "A trusted actor identity is required.",
                },
            )

        if supplied_actor_id:
            return supplied_actor_id
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "IDENTITY_REQUIRED",
                "message": "An actor identity is required.",
            },
        )

    @staticmethod
    def _bearer_token(request: Request) -> str | None:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return None
