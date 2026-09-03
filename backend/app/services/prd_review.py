"""Bridge immutable project Specs to their authoritative Gitea PRD review."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import asc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import (
    CommentIndex,
    PrdVersion,
    Project,
    ReviewTask,
    SpecVersion,
    WorkItem,
)
from app.domain.types import SpecStatus, WorkItemKind
from app.schemas.prd_review import (
    CommentCreateRequest,
    CommentReplyRequest,
    CommentResolveRequest,
    PrdCommentRead,
    PrdCommentableLineRead,
    PrdCommentableLinesRead,
    PrdCommentReplyRead,
    PrdDocumentRead,
    PrdDiffRead,
)
from app.services.gitea import GiteaClient, GiteaError, GiteaThread


class PrdServiceError(RuntimeError):
    """A stable, public-safe PRD review service failure."""

    code = "PRD_ERROR"


class PrdNotFound(PrdServiceError):
    code = "PRD_NOT_FOUND"


class PrdForbidden(PrdServiceError):
    code = "PRD_FORBIDDEN"


class PrdContentConflict(PrdServiceError):
    code = "PRD_CONTENT_CONFLICT"


class PrdReviewClosed(PrdServiceError):
    code = "PRD_REVIEW_CLOSED"


def _normalized_markdown(markdown: str) -> str:
    """Canonicalize Unicode and line endings without changing Markdown meaning."""

    return unicodedata.normalize("NFC", markdown).replace("\r\n", "\n").replace("\r", "\n")


def _markdown_hash(markdown: str) -> str:
    return hashlib.sha256(_normalized_markdown(markdown).encode("utf-8")).hexdigest()


def _structured_hash(content: object) -> str:
    canonical = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _valid_spec_content_hash(spec: SpecVersion) -> bool:
    """Accept current structured hashes and legacy Markdown-hash fixtures."""

    return spec.content_hash in {
        _structured_hash(spec.content),
        _markdown_hash(spec.markdown),
    }


@dataclass(frozen=True, slots=True)
class _WriteGuard:
    project_id: str
    state_version: int
    current_spec_version_id: str
    spec_status: str
    final_approver: str
    project_manager_ids: tuple[str, ...]
    root_owner_ids: tuple[str, ...]


class PrdReviewService:
    """Bind root WorkItems to one long-lived Gitea PR and immutable PRD files."""

    def __init__(self, session_factory: Callable[[], Session], gitea: GiteaClient) -> None:
        self._session_factory = session_factory
        self._gitea = gitea

    @staticmethod
    def assert_reviewer(project: Project, actor_id: str) -> None:
        reviewers = {
            project.final_approver,
            *(project.project_manager_ids or []),
            *(project.root_owner_ids or []),
        }
        if actor_id not in reviewers:
            raise PrdForbidden("actor is not authorized for PRD review")

    async def latest(self, wi: str) -> PrdDocumentRead:
        binding = await self._ensure_current_binding(wi)
        return await self._preflight_binding(binding)

    async def version(self, wi: str, number: int) -> PrdDocumentRead:
        await self._ensure_current_binding(wi)
        with self._session_factory() as db:
            binding = db.get(PrdVersion, (wi, number))
            if binding is None:
                raise PrdNotFound("PRD version not found")
        return await self._preflight_binding(binding)

    async def versions(self, wi: str) -> list[PrdDocumentRead]:
        await self._ensure_current_binding(wi)
        with self._session_factory() as db:
            bindings = (
                db.query(PrdVersion)
                .filter_by(wi=wi)
                .order_by(asc(PrdVersion.version))
                .all()
            )
        return [await self._preflight_binding(binding) for binding in bindings]

    async def comments(self, wi: str) -> list[PrdCommentRead]:
        binding = await self._ensure_current_binding(wi)
        await self._preflight_binding(binding)
        threads = await self._gitea.list_comment_threads(binding.pr_number)
        agent_reply_ids = self._verified_agent_reply_ids(wi, threads)
        with self._session_factory() as db:
            allowed_paths = self._allowed_paths(db, wi, binding.pr_number)
            accepted = [
                thread for thread in threads if thread.comment.path in allowed_paths
            ]
            result = [
                self._comment_read(thread, agent_reply_ids) for thread in accepted
            ]
            for thread in accepted:
                comment = thread.comment
                indexed = db.get(CommentIndex, comment.id)
                if indexed is not None and (
                    indexed.wi != wi or indexed.pr_number != binding.pr_number
                ):
                    raise PrdContentConflict(
                        "Gitea comment ID conflicts with another PRD review"
                    )
                if indexed is None:
                    indexed = CommentIndex(
                        id=comment.id,
                        wi=wi,
                        pr_number=binding.pr_number,
                        path=comment.path,
                        line=comment.line,
                        author_type="human",
                        body=comment.body,
                        resolved=comment.resolved,
                        created_at=self._timestamp(comment.created_at),
                    )
                    db.add(indexed)
                else:
                    indexed.path = comment.path
                    indexed.line = comment.line
                    indexed.author_type = "human"
                    indexed.body = comment.body
                    indexed.resolved = comment.resolved
            db.commit()
        return result

    async def commentable_lines(self, wi: str) -> PrdCommentableLinesRead:
        binding = await self._ensure_current_binding(wi)
        await self._preflight_binding(binding)
        lines = await self._gitea.commentable_lines(
            binding.pr_number, binding.filename
        )
        return PrdCommentableLinesRead(
            wi=wi,
            version=binding.version,
            filename=binding.filename,
            commit_sha=self._required_commit(binding),
            lines=[
                PrdCommentableLineRead(line=line, kind=kind, text=text)
                for line, kind, text in lines
            ],
        )

    async def diff(self, wi: str) -> PrdDiffRead:
        binding = await self._ensure_current_binding(wi)
        await self._preflight_binding(binding)
        refs = await self._gitea.read_pr_refs(binding.pr_number)
        current_file = await self._gitea.get_file(binding.filename, refs[1])
        if current_file.path != binding.filename or _markdown_hash(current_file.content) != binding.content_hash:
            raise PrdContentConflict("PR head content differs from the bound PRD")
        patch = await self._gitea.file_diff(binding.pr_number, binding.filename)
        if await self._gitea.read_pr_refs(binding.pr_number) != refs:
            raise PrdContentConflict("PR base or head changed while reading its diff")
        return PrdDiffRead(
            wi=wi, version=binding.version, filename=binding.filename,
            commit_sha=self._required_commit(binding), patch=patch,
        )

    async def create_comment(self, wi: str, request: CommentCreateRequest) -> None:
        binding, guard = await self._write_preflight(wi, request.actor_id)
        self._assert_write_guard(wi, request.actor_id, guard)
        await self._gitea.create_comment(
            binding.pr_number,
            binding.filename,
            request.line,
            request.text,
            self._required_commit(binding),
        )

    async def reply(
        self, wi: str, comment_id: int, request: CommentReplyRequest
    ) -> None:
        binding, guard = await self._write_preflight(wi, request.actor_id)
        await self._authoritative_thread(wi, binding, comment_id)
        self._assert_write_guard(wi, request.actor_id, guard)
        await self._gitea.reply_comment(binding.pr_number, comment_id, request.text)

    async def resolve(
        self, wi: str, comment_id: int, request: CommentResolveRequest
    ) -> None:
        binding, guard = await self._write_preflight(wi, request.actor_id)
        await self._authoritative_thread(wi, binding, comment_id)
        self._assert_write_guard(wi, request.actor_id, guard)
        await self._gitea.set_comment_resolved(comment_id, request.resolved)

    async def ensure_current_binding(self, wi: str) -> PrdVersion:
        binding = await self._ensure_current_binding(wi)
        await self._preflight_binding(binding)
        return binding

    async def publication_threads(
        self,
        wi: str,
        actor_id: str,
        binding: PrdVersion | None = None,
        recovery_task_id: str | None = None,
    ) -> tuple[PrdVersion, list[GiteaThread]]:
        """Read authoritative publication evidence before task mutation begins.

        ``binding`` is accepted for recovery of a partially published task,
        whose immutable base is no longer the Project's current Spec.  The
        current Project is still checked for reviewer authority and an open
        review state in either case.
        """

        with self._session_factory() as db:
            _, project, spec = self._root_context(db, wi)
            self.assert_reviewer(project, actor_id)
            if spec.status == SpecStatus.NEED_CLARIFICATION.value:
                task = (
                    db.get(ReviewTask, recovery_task_id)
                    if recovery_task_id is not None
                    else None
                )
                stored_binding = (
                    db.get(PrdVersion, (wi, task.base_version))
                    if task is not None
                    else None
                )
                if (
                    binding is None
                    or task is None
                    or task.status != "error"
                    or task.wi != wi
                    or task.new_spec_version_id != spec.id
                    or task.new_version is None
                    or task.new_version != spec.revision
                    or task.base_version != binding.version
                    or task.base_commit_sha != binding.commit_sha
                    or stored_binding is None
                    or stored_binding.spec_version_id != spec.parent_version_id
                    or stored_binding.version != binding.version
                    or stored_binding.commit_sha != binding.commit_sha
                    or stored_binding.content_hash != binding.content_hash
                ):
                    raise PrdReviewClosed("PRD review is closed")
            else:
                self._assert_open(spec)
        await self._check_capabilities()
        selected = binding or await self._ensure_current_binding(wi)
        if selected.wi != wi:
            raise PrdContentConflict("PRD binding belongs to another WorkItem")
        await self._preflight_binding(selected)
        threads = await self._gitea.list_comment_threads(selected.pr_number)
        with self._session_factory() as db:
            allowed_paths = self._allowed_paths(db, wi, selected.pr_number)
        return selected, [
            thread for thread in threads if thread.comment.path in allowed_paths
        ]

    async def _ensure_current_binding(self, wi: str) -> PrdVersion:
        existing_binding: PrdVersion | None = None
        with self._session_factory() as db:
            _, project, spec = self._root_context(db, wi)
            binding = db.query(PrdVersion).filter_by(spec_version_id=spec.id).one_or_none()
            if binding is not None:
                self._validate_binding(binding, wi, spec)
                existing_binding = binding
            else:
                if spec.status not in {SpecStatus.HUMAN_REVIEW.value, SpecStatus.REWORK.value}:
                    raise PrdReviewClosed("PRD review is closed")
                markdown = _normalized_markdown(spec.markdown)
                markdown_hash = _markdown_hash(markdown)
                if not _valid_spec_content_hash(spec):
                    raise PrdContentConflict("Spec content hash does not match its version")
                spec_content_hash = spec.content_hash
                spec_id = spec.id
                version_number = spec.revision
                change_summary = spec.change_summary
                first_bind_guard = self._write_guard(project, spec)

        await self._check_capabilities()
        if existing_binding is not None:
            return existing_binding

        branch = self._review_branch(wi)
        filename = f"docs/prd/{wi}/v{version_number}.md"
        branch_head = await self._gitea.ensure_branch(branch)
        try:
            existing = await self._gitea.get_file(filename, branch)
        except GiteaError as error:
            if error.code != "GITEA_NOT_FOUND":
                raise
            commit_sha = (
                await self._gitea.put_file(
                    filename,
                    markdown,
                    branch,
                    f"Publish PRD {wi} v{version_number}",
                )
            ).sha
        else:
            if existing.path != filename or _markdown_hash(existing.content) != markdown_hash:
                raise PrdContentConflict("Gitea PRD content conflicts with the Spec version")
            commit_sha = branch_head.sha
        pull_request = await self._gitea.ensure_review_pr(wi, branch)

        with self._session_factory() as db:
            _, current = self._first_bind_context(db, wi, first_bind_guard)
            binding = db.query(PrdVersion).filter_by(spec_version_id=spec_id).one_or_none()
            if binding is not None:
                self._validate_binding(binding, wi, current)
                return binding
            binding = PrdVersion(
                wi=wi,
                version=version_number,
                spec_version_id=spec_id,
                filename=filename,
                pr_number=pull_request.number,
                commit_sha=commit_sha,
                content_hash=markdown_hash,
                spec_content_hash=spec_content_hash,
                change_summary=change_summary,
            )
            db.add(binding)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            else:
                return binding

        # The failed Session may retain identity-map state from before the
        # losing flush.  Recovery deliberately starts from a fresh Session and
        # validates the whole original guard before trusting the winner.
        with self._session_factory() as recovery_db:
            _, current = self._first_bind_context(
                recovery_db, wi, first_bind_guard
            )
            binding = self._binding_race_winner(
                recovery_db, wi, version_number, spec_id
            )
            self._validate_binding(binding, wi, current)
            return binding

    async def _write_preflight(
        self, wi: str, actor_id: str
    ) -> tuple[PrdVersion, _WriteGuard]:
        with self._session_factory() as db:
            _, project, spec = self._root_context(db, wi)
            self.assert_reviewer(project, actor_id)
            self._assert_open(spec)
            guard = self._write_guard(project, spec)
        binding = await self._ensure_current_binding(wi)
        await self._preflight_binding(binding)
        return binding, guard

    def _assert_write_guard(
        self, wi: str, actor_id: str, expected: _WriteGuard
    ) -> None:
        with self._session_factory() as db:
            try:
                _, project, spec = self._root_context(db, wi)
            except PrdNotFound as error:
                raise PrdContentConflict(
                    "PRD review state changed during the external check"
                ) from error
            self.assert_reviewer(project, actor_id)
            self._assert_open(spec)
            if self._write_guard(project, spec) != expected:
                raise PrdContentConflict(
                    "PRD review state changed during the external check"
                )

    async def _preflight_binding(self, binding: PrdVersion) -> PrdDocumentRead:
        with self._session_factory() as db:
            spec = db.get(SpecVersion, binding.spec_version_id)
            item = db.get(WorkItem, binding.wi)
            if (
                spec is None
                or item is None
                or item.kind != WorkItemKind.ROOT.value
                or item.parent_id is not None
                or item.project_id != spec.project_id
            ):
                raise PrdContentConflict("PRD binding has no matching root Spec")
            self._validate_binding(binding, binding.wi, spec)
        external = await self._gitea.get_file(
            binding.filename,
            self._required_commit(binding),
        )
        content = _normalized_markdown(external.content)
        if (
            external.path != binding.filename
            or content != _normalized_markdown(spec.markdown)
            or _markdown_hash(content) != binding.content_hash
        ):
            raise PrdContentConflict("Gitea PRD content conflicts with its binding")
        return PrdDocumentRead(
            wi=binding.wi,
            version=binding.version,
            filename=binding.filename,
            pr_number=binding.pr_number,
            commit_sha=binding.commit_sha,
            content=content,
            change_summary=binding.change_summary,
        )

    async def _authoritative_thread(
        self, wi: str, binding: PrdVersion, comment_id: int
    ) -> GiteaThread:
        threads = await self._gitea.list_comment_threads(binding.pr_number)
        with self._session_factory() as db:
            allowed_paths = self._allowed_paths(db, wi, binding.pr_number)
        for thread in threads:
            if (
                thread.comment.id == comment_id
                and thread.comment.path in allowed_paths
            ):
                return thread
        raise PrdNotFound("PRD comment not found")

    @staticmethod
    def _allowed_paths(db: Session, wi: str, pr_number: int) -> set[str]:
        return {
            path
            for (path,) in db.query(PrdVersion.filename)
            .filter_by(wi=wi, pr_number=pr_number)
            .all()
        }

    @staticmethod
    def _comment_read(
        thread: GiteaThread, agent_reply_ids: set[int] | frozenset[int] = frozenset()
    ) -> PrdCommentRead:
        comment = thread.comment
        return PrdCommentRead(
            id=comment.id,
            path=comment.path,
            line=comment.line,
            author_type="human",
            body=comment.body,
            resolved=comment.resolved,
            replies=[
                PrdCommentReplyRead(
                    id=reply.id,
                    author_type="agent" if reply.id in agent_reply_ids else "human",
                    body=reply.body,
                )
                for reply in thread.replies
            ],
        )

    @staticmethod
    def _timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _required_commit(binding: PrdVersion) -> str:
        if not binding.commit_sha:
            raise PrdContentConflict("PRD binding has no commit SHA")
        return binding.commit_sha

    @staticmethod
    def _binding_race_winner(
        db: Session, wi: str, version_number: int, spec_id: str
    ) -> PrdVersion:
        by_spec = db.query(PrdVersion).filter_by(spec_version_id=spec_id).one_or_none()
        by_key = db.get(PrdVersion, (wi, version_number))
        if (
            by_spec is None
            or by_key is None
            or by_spec.spec_version_id != by_key.spec_version_id
        ):
            raise PrdContentConflict("concurrent PRD binding conflicts with this Spec")
        return by_spec

    @staticmethod
    def _write_guard(project: Project, spec: SpecVersion) -> _WriteGuard:
        return _WriteGuard(
            project_id=project.id,
            state_version=project.state_version,
            current_spec_version_id=str(project.current_spec_version_id),
            spec_status=spec.status,
            final_approver=project.final_approver,
            project_manager_ids=tuple(project.project_manager_ids or []),
            root_owner_ids=tuple(project.root_owner_ids or []),
        )

    def _first_bind_context(
        self, db: Session, wi: str, expected: _WriteGuard
    ) -> tuple[Project, SpecVersion]:
        try:
            _, project, spec = self._root_context(db, wi)
        except PrdNotFound as error:
            raise PrdContentConflict(
                "current Spec changed while binding the PRD"
            ) from error
        self._assert_open(spec)
        if self._write_guard(project, spec) != expected:
            raise PrdContentConflict("current Spec changed while binding the PRD")
        return project, spec

    @staticmethod
    def _assert_open(spec: SpecVersion) -> None:
        if spec.status not in {
            SpecStatus.HUMAN_REVIEW.value,
            SpecStatus.REWORK.value,
        }:
            raise PrdReviewClosed("PRD review is closed")

    @staticmethod
    def _validate_binding(binding: PrdVersion, wi: str, spec: SpecVersion) -> None:
        expected_filename = f"docs/prd/{wi}/v{spec.revision}.md"
        if (
            not _valid_spec_content_hash(spec)
            or binding.wi != wi
            or binding.version != spec.revision
            or binding.filename != expected_filename
            or binding.content_hash != _markdown_hash(spec.markdown)
            or binding.spec_content_hash != spec.content_hash
            or not binding.commit_sha
            or not isinstance(binding.pr_number, int)
            or binding.pr_number <= 0
        ):
            raise PrdContentConflict("PRD binding conflicts with its Spec version")

    async def _check_capabilities(self) -> None:
        checker = getattr(self._gitea, "check_capabilities", None)
        if checker is not None:
            await checker()

    def _review_branch(self, wi: str) -> str:
        branch_builder = getattr(self._gitea, "review_branch", None)
        if branch_builder is not None:
            return str(branch_builder(wi))
        return f"prd-review/{wi}"

    def _verified_agent_reply_ids(
        self, wi: str, threads: list[GiteaThread]
    ) -> set[int]:
        identity = getattr(self._gitea, "service_identity", None)
        if identity is None:
            return set()
        replies = {
            reply.id: reply
            for thread in threads
            for reply in thread.replies
        }
        verified: set[int] = set()
        with self._session_factory() as db:
            tasks = db.query(ReviewTask).filter_by(wi=wi).all()
            for task in tasks:
                receipts = task.reply_receipts or {}
                if not isinstance(receipts, dict):
                    continue
                for raw_comment_id, receipt in receipts.items():
                    if not isinstance(receipt, dict):
                        continue
                    reply_id = receipt.get("reply_id")
                    user_id = receipt.get("user_id")
                    body = receipt.get("body")
                    reply = replies.get(reply_id)
                    try:
                        comment_id = int(raw_comment_id)
                    except (TypeError, ValueError):
                        continue
                    if (
                        reply is not None
                        and reply.user_id == identity.id == user_id
                        and reply.body == body
                        and isinstance(body, str)
                        and self._valid_receipt_body(body, task.id, comment_id)
                    ):
                        verified.add(reply.id)
        return verified

    def _valid_receipt_body(self, body: str, task_id: str, comment_id: int) -> bool:
        pattern = re.compile(
            rf"\n<!-- firstflight-receipt:v1:{re.escape(task_id)}:{comment_id}:([0-9a-f]{{64}}) -->$"
        )
        match = pattern.search(body)
        verifier = getattr(self._gitea, "verify_receipt", None)
        if match is None or verifier is None:
            return False
        visible = body[: match.start()]
        return bool(
            verifier(f"{task_id}\n{comment_id}\n{visible}", match.group(1))
        )

    @staticmethod
    def _root_context(
        db: Session, wi: str
    ) -> tuple[WorkItem, Project, SpecVersion]:
        item = db.get(WorkItem, wi)
        if (
            item is None
            or item.kind != WorkItemKind.ROOT.value
            or item.parent_id is not None
        ):
            raise PrdNotFound("root WorkItem not found")
        project = db.get(Project, item.project_id)
        if project is None or not project.current_spec_version_id:
            raise PrdNotFound("project Spec not found")
        version = db.get(SpecVersion, project.current_spec_version_id)
        if version is None or version.project_id != project.id:
            raise PrdNotFound("project Spec not found")
        return item, project, version


__all__ = [
    "PrdContentConflict",
    "PrdForbidden",
    "PrdNotFound",
    "PrdReviewClosed",
    "PrdReviewService",
    "PrdServiceError",
]
