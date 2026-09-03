"""Small, safe HTTP boundary for the Gitea repository API.

The client deliberately owns no local Git state.  File updates use Gitea's
Contents API so a successful response is the server-side commit receipt.
"""

from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import quote

import httpx

from app.config import Settings


class GiteaError(RuntimeError):
    """A stable, public-safe Gitea failure.

    ``public_message`` is intentionally the only value used in ``str(error)``;
    response bodies, request URLs, and credentials stay out of exception text.
    """

    def __init__(self, code: str, public_message: str, retryable: bool) -> None:
        self.code = code
        self.public_message = public_message
        self.retryable = retryable
        super().__init__(public_message)


class CommentLineNotInDiff(ValueError):
    """The requested target-file line has no unambiguous diff position."""


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def diff_position_for_new_line(patch: str, line: int) -> int:
    """Validate and return one represented target-file source line.

    Gitea's ``new_position`` contract is the new-file line number, not a hunk
    body offset.  The patch is still authoritative for whether that line may
    be reviewed; omitted and deleted lines are rejected.
    """
    if line <= 0:
        raise CommentLineNotInDiff()

    new_line: int | None = None
    for diff_line in patch.splitlines():
        header = _HUNK_HEADER.match(diff_line)
        if header is not None:
            new_line = int(header.group(1))
            continue
        if new_line is None or diff_line == r"\ No newline at end of file":
            continue
        if diff_line.startswith("-"):
            continue
        if diff_line.startswith((" ", "+")):
            if new_line == line:
                return line
            new_line += 1

    raise CommentLineNotInDiff()


def commentable_lines_from_patch(patch: str) -> list[tuple[int, str, str]]:
    """Project a unified diff into reviewable new-file source lines."""

    result: list[tuple[int, str, str]] = []
    new_line: int | None = None
    for diff_line in patch.splitlines():
        header = _HUNK_HEADER.match(diff_line)
        if header is not None:
            new_line = int(header.group(1))
            continue
        if new_line is None or diff_line == r"\ No newline at end of file":
            continue
        if diff_line.startswith("-"):
            continue
        if diff_line.startswith(" "):
            result.append((new_line, "context", diff_line[1:]))
            new_line += 1
        elif diff_line.startswith("+"):
            result.append((new_line, "addition", diff_line[1:]))
            new_line += 1
    return result


@dataclass(frozen=True)
class GiteaFile:
    path: str
    content: str
    sha: str


@dataclass(frozen=True)
class GiteaCommit:
    sha: str


@dataclass(frozen=True)
class GiteaPullRequest:
    number: int
    head: str
    base: str


@dataclass(frozen=True)
class GiteaIdentity:
    id: int
    login: str


@dataclass(frozen=True)
class GiteaComment:
    id: int
    path: str
    line: int
    body: str
    user: str
    created_at: str
    resolved: bool
    user_id: int | None = None


@dataclass(frozen=True)
class GiteaReply:
    id: int
    body: str
    user: str
    created_at: str
    user_id: int | None = None


@dataclass(frozen=True)
class GiteaThread:
    comment: GiteaComment
    replies: tuple[GiteaReply, ...]


class GiteaClient:
    """Async adapter for the subset of Gitea used by PRD review.

    Constructing this client is side-effect free: capability checking remains
    explicit so application startup cannot contact Gitea.
    """

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        configured_url = (settings.gitea_url or "http://gitea.invalid").rstrip("/")
        base_url = configured_url if configured_url.endswith("/api/v1") else f"{configured_url}/api/v1"
        timeout = httpx.Timeout(
            settings.gitea_timeout_seconds,
            connect=settings.gitea_timeout_seconds,
            read=settings.gitea_timeout_seconds,
            write=settings.gitea_timeout_seconds,
            pool=settings.gitea_timeout_seconds,
        )
        headers = {"Accept": "application/json"}
        if settings.gitea_token:
            headers["Authorization"] = f"token {settings.gitea_token}"
        self._client = httpx.AsyncClient(
            base_url=f"{base_url}/", headers=headers, timeout=timeout, transport=transport
        )
        self._capability_lock = asyncio.Lock()
        self._identity: GiteaIdentity | None = None

    async def __aenter__(self) -> "GiteaClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def check_capabilities(self) -> GiteaIdentity:
        """Lazily validate the minimum non-mutating Gitea contract once."""

        self._ensure_configured()
        if self._identity is not None:
            return self._identity
        async with self._capability_lock:
            if self._identity is not None:
                return self._identity
            response = await self._request("GET", "version")
            payload = self._json_object(response)
            version = payload.get("version")
            if not isinstance(version, str) or not self._supported_version(version):
                raise self._incompatible()

            identity_payload = self._json_object(await self._request("GET", "user"))
            identifier = identity_payload.get("id")
            login = identity_payload.get("login")
            if (
                not isinstance(identifier, int)
                or isinstance(identifier, bool)
                or identifier <= 0
                or not isinstance(login, str)
                or not login
            ):
                raise self._incompatible()

            repo = self._json_object(await self._request("GET", self._repo_path("").rstrip("/")))
            permissions = repo.get("permissions")
            if (
                not isinstance(permissions, dict)
                or permissions.get("pull") is not True
                or permissions.get("push") is not True
            ):
                raise GiteaError("GITEA_FORBIDDEN", "Gitea access is forbidden.", False)

            branch_path = self._repo_path(
                f"branches/{self._encode_segment(self._settings.gitea_base_branch)}"
            )
            self._branch_sha(self._json_object(await self._request("GET", branch_path)))
            self._identity = GiteaIdentity(identifier, login)
            return self._identity

    @property
    def service_identity(self) -> GiteaIdentity | None:
        return self._identity

    def review_branch(self, wi: str) -> str:
        return f"{self._settings.gitea_review_branch_prefix}{wi}"

    def sign_receipt(self, payload: str) -> str:
        self._ensure_configured()
        return hmac.new(
            (self._settings.gitea_token or "").encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_receipt(self, payload: str, signature: str) -> bool:
        return hmac.compare_digest(self.sign_receipt(payload), signature)

    async def get_file(self, path: str, ref: str) -> GiteaFile:
        self._ensure_configured()
        response = await self._request(
            "GET",
            self._repo_path(f"contents/{self._encode_file_path(path)}"),
            params={"ref": ref},
            resource_not_found=True,
        )
        payload = self._json_object(response)
        encoded_content = payload.get("content")
        sha = payload.get("sha")
        returned_path = payload.get("path", path)
        if not all(isinstance(value, str) and value for value in (encoded_content, sha, returned_path)):
            raise self._incompatible()
        try:
            # Gitea wraps Contents API base64 at line boundaries.  Remove only
            # that documented wrapping, then reject every other invalid byte.
            wrapped_content = encoded_content.replace("\r", "").replace("\n", "")
            content = base64.b64decode(wrapped_content, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise self._incompatible() from error
        return GiteaFile(path=returned_path, content=content, sha=sha)

    async def ensure_branch(self, branch: str) -> GiteaCommit:
        self._ensure_configured()
        branch_path = self._repo_path(f"branches/{self._encode_segment(branch)}")
        existing = await self._request("GET", branch_path, allow_statuses=(404,))
        if existing.status_code != 404:
            return GiteaCommit(self._branch_sha(self._json_object(existing)))

        created = await self._request(
            "POST",
            self._repo_path("branches"),
            json={
                "new_branch_name": branch,
                "old_ref_name": self._settings.gitea_base_branch,
            },
            allow_statuses=(409,),
        )
        if created.status_code != 409:
            return GiteaCommit(self._branch_sha(self._json_object(created)))

        # A concurrent caller won the race.  Re-read and use its branch rather
        # than failing or creating another one.
        raced = await self._request("GET", branch_path)
        return GiteaCommit(self._branch_sha(self._json_object(raced)))

    async def put_file(
        self, path: str, content: str, branch: str, message: str, sha: str | None = None
    ) -> GiteaCommit:
        self._ensure_configured()
        payload: dict[str, str] = {
            "branch": branch,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "message": message,
        }
        if sha is not None:
            payload["sha"] = sha
        response = await self._request(
            "PUT", self._repo_path(f"contents/{self._encode_file_path(path)}"), json=payload
        )
        result = self._json_object(response)
        commit = result.get("commit")
        if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str) or not commit["sha"]:
            raise self._incompatible()
        return GiteaCommit(commit["sha"])

    async def ensure_review_pr(self, wi: str, head: str) -> GiteaPullRequest:
        self._ensure_configured()
        base = self._settings.gitea_base_branch
        payload = await self._paginated_array(
            self._repo_path("pulls"),
            params={"state": "open", "base_branch": base},
        )
        for candidate in payload:
            parsed = self._pull_request(candidate)
            if parsed.head == head and parsed.base == base:
                return parsed

        created = await self._request(
            "POST",
            self._repo_path("pulls"),
            json={"title": f"PRD review: {wi}", "head": head, "base": base},
            allow_statuses=(409,),
        )
        if created.status_code == 409:
            # Creating a PR races just like branch creation.  Search once more
            # and only surface a safe failure if no matching open PR exists.
            retry = await self._paginated_array(
                self._repo_path("pulls"),
                params={"state": "open", "base_branch": base},
            )
            for candidate in retry:
                parsed = self._pull_request(candidate)
                if parsed.head == head and parsed.base == base:
                    return parsed
            raise GiteaError("GITEA_UNAVAILABLE", "Gitea is temporarily unavailable.", True)
        parsed = self._pull_request(self._json_object(created))
        return parsed

    async def list_comment_threads(self, pr_number: int) -> list[GiteaThread]:
        self._ensure_configured()
        reviews = await self._paginated_array(
            self._repo_path(f"pulls/{pr_number}/reviews")
        )
        threads: list[GiteaThread] = []
        seen_review_ids: set[int] = set()
        seen_comment_ids: set[int] = set()
        for review in reviews:
            review_id, comments_count = self._review_metadata(review)
            if review_id in seen_review_ids:
                raise self._incompatible()
            seen_review_ids.add(review_id)
            if comments_count == 0:
                continue
            response = await self._request(
                "GET", self._repo_path(f"pulls/{pr_number}/reviews/{review_id}/comments")
            )
            comments = [self._review_comment(value, review_id) for value in self._json_array(response)]
            if not comments:
                raise self._incompatible()
            groups: dict[tuple[str, int], list[GiteaComment]] = {}
            for comment in comments:
                if comment.id in seen_comment_ids:
                    raise self._incompatible()
                seen_comment_ids.add(comment.id)
                groups.setdefault((comment.path, comment.line), []).append(comment)
            for group in groups.values():
                group.sort(key=lambda comment: (self._comment_timestamp(comment.created_at), comment.id))
                root, *replies = group
                threads.append(
                    GiteaThread(
                        comment=root,
                        replies=tuple(
                            GiteaReply(
                                id=reply.id,
                                body=reply.body,
                                user=reply.user,
                                created_at=reply.created_at,
                                user_id=reply.user_id,
                            )
                            for reply in replies
                        ),
                    )
                )
        threads.sort(
            key=lambda thread: (
                self._comment_timestamp(thread.comment.created_at),
                thread.comment.id,
            )
        )
        return threads

    async def create_comment(
        self, pr_number: int, path: str, line: int, body: str, commit_sha: str
    ) -> None:
        self._ensure_configured()
        patch = await self._load_file_patch(pr_number, path)
        position = diff_position_for_new_line(patch, line)
        await self._request(
            "POST",
            self._repo_path(f"pulls/{pr_number}/reviews"),
            json={
                "body": body,
                "commit_id": commit_sha,
                "event": "COMMENT",
                "comments": [{"body": body, "path": path, "new_position": position}],
            },
        )

    async def commentable_lines(
        self, pr_number: int, path: str
    ) -> list[tuple[int, str, str]]:
        """Return the exact target-file lines accepted by review comments."""

        self._ensure_configured()
        patch = await self._load_file_patch(pr_number, path)
        return commentable_lines_from_patch(patch)

    async def read_pr_refs(self, pr_number: int) -> tuple[str, str]:
        """Read the base/head commits that define the current PR diff."""
        self._ensure_configured()
        response = await self._request("GET", self._repo_path(f"pulls/{pr_number}"))
        payload = self._json_object(response)
        refs = []
        for name in ("base", "head"):
            ref = payload.get(name)
            sha = ref.get("sha") if isinstance(ref, dict) else None
            if not isinstance(sha, str) or not sha:
                raise self._incompatible()
            refs.append(sha)
        return refs[0], refs[1]

    async def file_diff(self, pr_number: int, path: str) -> str:
        self._ensure_configured()
        return await self._load_file_patch(pr_number, path)

    async def reply_comment(self, pr_number: int, comment_id: int, body: str) -> GiteaReply:
        self._ensure_configured()
        response = await self._request(
            "POST", self._repo_path(f"pulls/{pr_number}/comments/{comment_id}/replies"), json={"body": body}
        )
        return self._reply(self._json_object(response))

    async def set_comment_resolved(self, comment_id: int, resolved: bool) -> None:
        self._ensure_configured()
        action = "resolve" if resolved else "unresolve"
        await self._request("POST", self._repo_path(f"pulls/comments/{comment_id}/{action}"))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        allow_statuses: Iterable[int] = (),
        resource_not_found: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            raise GiteaError("GITEA_TIMEOUT", "Gitea is temporarily unavailable.", True) from error
        except httpx.RequestError as error:
            raise GiteaError("GITEA_UNAVAILABLE", "Gitea is temporarily unavailable.", True) from error
        if response.status_code >= 400 and response.status_code not in set(allow_statuses):
            raise self._classified_error(response.status_code, resource_not_found=resource_not_found)
        return response

    def _ensure_configured(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self._settings.gitea_url, self._settings.gitea_token, self._settings.gitea_owner)
        ):
            raise GiteaError("GITEA_NOT_CONFIGURED", "Gitea is not configured.", False)

    @staticmethod
    def _classified_error(status_code: int, *, resource_not_found: bool = False) -> GiteaError:
        if status_code in (401, 403):
            return GiteaError("GITEA_FORBIDDEN", "Gitea access is forbidden.", False)
        if status_code == 429:
            return GiteaError("GITEA_RATE_LIMITED", "Gitea is rate limited.", True)
        if status_code == 404:
            if resource_not_found:
                return GiteaError("GITEA_NOT_FOUND", "Gitea resource was not found.", False)
            return GiteaError("GITEA_INCOMPATIBLE", "Gitea is incompatible.", False)
        if status_code >= 500:
            return GiteaError("GITEA_UNAVAILABLE", "Gitea is temporarily unavailable.", True)
        return GiteaError("GITEA_UNAVAILABLE", "Gitea is temporarily unavailable.", False)

    @staticmethod
    def _incompatible() -> GiteaError:
        return GiteaError("GITEA_INCOMPATIBLE", "Gitea is incompatible.", False)

    def _repo_path(self, suffix: str) -> str:
        owner = self._encode_segment(self._settings.gitea_owner or "")
        repo = self._encode_segment(self._settings.gitea_repo)
        return f"repos/{owner}/{repo}/{suffix}"

    @staticmethod
    def _encode_segment(value: str) -> str:
        return quote(value, safe="")

    @classmethod
    def _encode_file_path(cls, path: str) -> str:
        return "/".join(cls._encode_segment(part) for part in path.split("/"))

    @classmethod
    def _json_object(cls, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as error:
            raise cls._incompatible() from error
        if not isinstance(payload, dict):
            raise cls._incompatible()
        return payload

    @classmethod
    def _json_array(cls, response: httpx.Response) -> list[Any]:
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as error:
            raise cls._incompatible() from error
        if not isinstance(payload, list):
            raise cls._incompatible()
        return payload

    @classmethod
    def _branch_sha(cls, payload: dict[str, Any]) -> str:
        commit = payload.get("commit")
        if not isinstance(commit, dict) or not isinstance(commit.get("id"), str) or not commit["id"]:
            raise cls._incompatible()
        return commit["id"]

    @classmethod
    def _pull_request(cls, value: Any) -> GiteaPullRequest:
        if not isinstance(value, dict):
            raise cls._incompatible()
        number = value.get("number")
        head = value.get("head")
        base = value.get("base")
        if (
            not isinstance(number, int)
            or not isinstance(head, dict)
            or not isinstance(base, dict)
            or not isinstance(head.get("ref"), str)
            or not isinstance(base.get("ref"), str)
        ):
            raise cls._incompatible()
        return GiteaPullRequest(number=number, head=head["ref"], base=base["ref"])

    @classmethod
    def _review_metadata(cls, value: Any) -> tuple[int, int]:
        if not isinstance(value, dict):
            raise cls._incompatible()
        identifier = value.get("id")
        comments_count = value.get("comments_count")
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier <= 0
            or not isinstance(comments_count, int)
            or isinstance(comments_count, bool)
            or comments_count < 0
        ):
            raise cls._incompatible()
        return identifier, comments_count

    @classmethod
    def _review_comment(cls, value: Any, review_id: int) -> GiteaComment:
        if not isinstance(value, dict):
            raise cls._incompatible()
        identifier = value.get("id")
        path = value.get("path")
        line = value.get("position")
        body = value.get("body")
        user = value.get("user")
        resolver = value.get("resolver")
        returned_review_id = value.get("pull_request_review_id")
        created_at = value.get("created_at")
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier <= 0
            or not isinstance(path, str)
            or not path
            or not isinstance(line, int)
            or isinstance(line, bool)
            or line <= 0
            or not isinstance(body, str)
            or not body
            or not isinstance(user, dict)
            or not isinstance(user.get("login"), str)
            or not user["login"]
            or (resolver is not None and not isinstance(resolver, dict))
            or not isinstance(returned_review_id, int)
            or isinstance(returned_review_id, bool)
            or returned_review_id != review_id
            or not isinstance(created_at, str)
            or not created_at
        ):
            raise cls._incompatible()
        cls._comment_timestamp(created_at)
        return GiteaComment(
            id=identifier,
            path=path,
            line=line,
            body=body,
            user=user["login"],
            created_at=created_at,
            resolved=resolver is not None,
            user_id=cls._optional_user_id(user),
        )

    @classmethod
    def _reply(cls, value: Any) -> GiteaReply:
        if not isinstance(value, dict):
            raise cls._incompatible()
        identifier = value.get("id")
        body = value.get("body")
        user = value.get("user")
        created_at = value.get("created_at")
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier <= 0
            or not isinstance(body, str)
            or not body
            or not isinstance(user, dict)
            or not isinstance(user.get("login"), str)
            or not user["login"]
            or not isinstance(created_at, str)
            or not created_at
        ):
            raise cls._incompatible()
        cls._comment_timestamp(created_at)
        return GiteaReply(
            id=identifier,
            body=body,
            user=user["login"],
            created_at=created_at,
            user_id=cls._optional_user_id(user),
        )

    @classmethod
    def _optional_user_id(cls, user: dict[str, Any]) -> int | None:
        value = user.get("id")
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise cls._incompatible()
        return value

    @staticmethod
    def _supported_version(version: str) -> bool:
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version.strip())
        return match is not None and tuple(int(part) for part in match.groups()) >= (1, 27, 0)

    async def _paginated_array(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> list[Any]:
        values: list[Any] = []
        page = 1
        limit = 50
        while True:
            page_params = dict(params or {})
            page_params.update({"page": str(page), "limit": str(limit)})
            response = await self._request("GET", path, params=page_params)
            current = self._json_array(response)
            values.extend(current)
            has_more_header = response.headers.get("X-HasMore")
            if has_more_header is not None:
                has_more = has_more_header.strip().lower() == "true"
            else:
                has_more = len(current) >= limit
            if not has_more:
                return values
            page += 1
            if page > 10_000:
                raise self._incompatible()

    @classmethod
    def _comment_timestamp(cls, created_at: str) -> datetime:
        try:
            timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise cls._incompatible() from error
        if timestamp.tzinfo is None:
            raise cls._incompatible()
        return timestamp

    async def _load_file_patch(self, pr_number: int, path: str) -> str:
        changed_files = await self._paginated_array(
            self._repo_path(f"pulls/{pr_number}/files")
        )
        patch = self._patch_for_file(changed_files, path)
        if patch is not None:
            return patch
        # Gitea's files API contains statistics, not GitHub's inline patch.
        response = await self._request(
            "GET", self._repo_path(f"pulls/{pr_number}.diff"),
            headers={"Accept": "text/plain"},
        )
        matches = []
        for section in re.split(r"(?m)^diff --git ", response.text)[1:]:
            lines = section.splitlines()
            hunk_start = next((i for i, line in enumerate(lines) if line.startswith("@@ ")), None)
            if hunk_start is None:
                continue
            target_headers = [line[4:] for line in lines[:hunk_start] if line.startswith("+++ ")]
            expected = f"b/{path}"
            if target_headers in ([expected], [json.dumps(expected, ensure_ascii=False)]):
                matches.append("\n".join(lines[hunk_start:]))
        if len(matches) != 1:
            raise CommentLineNotInDiff()
        return matches[0]

    @classmethod
    def _patch_for_file(cls, changed_files: list[Any], path: str) -> str | None:
        matches = []
        for changed_file in changed_files:
            if not isinstance(changed_file, dict):
                raise cls._incompatible()
            filename = changed_file.get("filename")
            if not isinstance(filename, str) or not filename:
                raise cls._incompatible()
            if filename == path:
                matches.append(changed_file)
        if len(matches) != 1:
            raise CommentLineNotInDiff()
        patch = matches[0].get("patch")
        if patch is not None and not isinstance(patch, str):
            raise cls._incompatible()
        return patch
