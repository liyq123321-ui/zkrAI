"""Gitea-backed PRD review binding and comment projection behavior."""

import hashlib
import unicodedata

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import CommentIndex, PrdVersion, Project, SpecVersion, WorkItem
from app.domain.types import ProjectPhase, SpecStatus, WorkItemKind
from pydantic import ValidationError

from app.schemas.prd_review import (
    CommentCreateRequest,
    CommentReplyRequest,
    CommentResolveRequest,
    PrdCommentRead,
    PrdDocumentRead,
)
from app.services.gitea import (
    GiteaComment,
    GiteaCommit,
    GiteaError,
    GiteaFile,
    GiteaIdentity,
    GiteaPullRequest,
    GiteaReply,
    GiteaThread,
)
from app.services.prd_review import (
    PrdContentConflict,
    PrdForbidden,
    PrdNotFound,
    PrdReviewClosed,
    PrdReviewService,
)


class FakeGitea:
    """In-memory external boundary; service behavior remains real."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], GiteaFile] = {}
        self.threads: dict[int, list[GiteaThread]] = {}
        self.calls: list[tuple[object, ...]] = []
        self.on_get_file = None
        self.on_ensure_review_pr = None
        self.on_list_comment_threads = None
        self.service_identity = GiteaIdentity(99, "firstflight")

    async def check_capabilities(self):
        return self.service_identity

    @staticmethod
    def review_branch(wi: str) -> str:
        return f"prd-review/{wi}"

    @staticmethod
    def sign_receipt(payload: str) -> str:
        return hashlib.sha256(f"test-secret:{payload}".encode()).hexdigest()

    @classmethod
    def verify_receipt(cls, payload: str, signature: str) -> bool:
        return cls.sign_receipt(payload) == signature

    async def ensure_branch(self, branch: str) -> GiteaCommit:
        self.calls.append(("ensure_branch", branch))
        return GiteaCommit("base-commit")

    async def get_file(self, path: str, ref: str) -> GiteaFile:
        self.calls.append(("get_file", path, ref))
        callback, self.on_get_file = self.on_get_file, None
        if callback is not None:
            callback()
        try:
            return self.files[(path, ref)]
        except KeyError as error:
            raise GiteaError("GITEA_NOT_FOUND", "not found", False) from error

    async def put_file(
        self,
        path: str,
        content: str,
        branch: str,
        message: str,
        sha: str | None = None,
    ) -> GiteaCommit:
        self.calls.append(("put_file", path, content, branch, message, sha))
        assert sha is None
        commit = GiteaCommit("created-commit")
        self.files[(path, branch)] = GiteaFile(path, content, "created-blob")
        self.files[(path, commit.sha)] = GiteaFile(path, content, "created-blob")
        return commit

    async def ensure_review_pr(self, wi: str, head: str) -> GiteaPullRequest:
        self.calls.append(("ensure_review_pr", wi, head))
        callback, self.on_ensure_review_pr = self.on_ensure_review_pr, None
        if callback is not None:
            callback()
        return GiteaPullRequest(number=17, head=head, base="main")

    async def list_comment_threads(self, pr_number: int) -> list[GiteaThread]:
        self.calls.append(("list_comment_threads", pr_number))
        callback, self.on_list_comment_threads = self.on_list_comment_threads, None
        if callback is not None:
            callback()
        return list(self.threads.get(pr_number, []))

    async def create_comment(
        self, pr_number: int, path: str, line: int, body: str, commit_sha: str
    ) -> None:
        self.calls.append(
            ("create_comment", pr_number, path, line, body, commit_sha)
        )

    async def reply_comment(self, pr_number: int, comment_id: int, body: str) -> GiteaReply:
        self.calls.append(("reply_comment", pr_number, comment_id, body))
        return GiteaReply(
            id=9000 + len(self.calls), body=body, user="firstflight",
            created_at="2026-09-01T09:00:00Z", user_id=99,
        )

    async def set_comment_resolved(self, comment_id: int, resolved: bool) -> None:
        self.calls.append(("set_comment_resolved", comment_id, resolved))


def _markdown_hash(markdown: str) -> str:
    normalized = unicodedata.normalize("NFC", markdown)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _review_project(db, *, status: SpecStatus = SpecStatus.HUMAN_REVIEW):
    markdown = "# PRD\r\n\r\nA first version.\r\n"
    project = Project(
        id="project-prd",
        session_id="session-prd",
        creation_request_id="request-prd",
        brief={"title": "PRD"},
        final_approver="approver-1",
        project_manager_ids=["pm-1"],
        root_owner_ids=["owner-1"],
        phase=ProjectPhase.REVIEW.value,
        state_version=2,
        current_spec_version_id="spec-prd-1",
    )
    spec = SpecVersion(
        id="spec-prd-1",
        project_id=project.id,
        revision=1,
        content={"background_and_goals": ["A first version."]},
        markdown=markdown,
        generation_source="PM_AGENT",
        input_refs=[],
        generator_agent_session_id="pm-session",
        generator_call_id="pm-call",
        parent_version_id=None,
        change_summary="Initial specification",
        content_hash=_markdown_hash(markdown),
        status=status.value,
    )
    root = WorkItem(
        id="root-prd",
        project_id=project.id,
        local_key="root",
        kind=WorkItemKind.ROOT.value,
        parent_id=None,
        executable=False,
    )
    child = WorkItem(
        id="child-prd",
        project_id=project.id,
        local_key="child",
        kind=WorkItemKind.TASK.value,
        parent_id=root.id,
        executable=True,
    )
    db.add_all([project, spec, root, child])
    db.commit()
    return project, spec, root, child


@pytest.mark.asyncio
async def test_first_binding_publishes_normalized_current_root_spec_once(
    session_factory, db_session
):
    """Omitting any branch/file/PR binding step would leave review state unauditable."""
    _, spec, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)

    binding = await service.ensure_current_binding(root.id)

    assert binding.wi == root.id
    assert binding.version == 1
    assert binding.spec_version_id == spec.id
    assert binding.filename == "docs/prd/root-prd/v1.md"
    assert binding.pr_number == 17
    assert binding.commit_sha == "created-commit"
    assert binding.content_hash == hashlib.sha256(
        b"# PRD\n\nA first version.\n"
    ).hexdigest()
    assert gitea.calls == [
        ("ensure_branch", "prd-review/root-prd"),
        (
            "get_file",
            "docs/prd/root-prd/v1.md",
            "prd-review/root-prd",
        ),
        (
            "put_file",
            "docs/prd/root-prd/v1.md",
            "# PRD\n\nA first version.\n",
            "prd-review/root-prd",
            "Publish PRD root-prd v1",
            None,
        ),
        ("ensure_review_pr", "root-prd", "prd-review/root-prd"),
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
    ]
    with session_factory() as db:
        assert db.query(PrdVersion).count() == 1
        assert db.get(PrdVersion, (root.id, 1)).content_hash == spec.content_hash
        assert db.get(PrdVersion, (root.id, 1)).spec_content_hash == spec.content_hash

    same = await service.ensure_current_binding(root.id)
    assert same.spec_version_id == spec.id
    assert gitea.calls[-1] == (
        "get_file",
        "docs/prd/root-prd/v1.md",
        "created-commit",
    )


@pytest.mark.asyncio
async def test_binding_uses_configured_review_branch_builder(
    session_factory, db_session
):
    """The configured prefix is part of the repository contract, not a label."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    gitea.review_branch = lambda wi: f"custom-prd/{wi}"

    await PrdReviewService(session_factory, gitea).ensure_current_binding(root.id)

    assert ("ensure_branch", "custom-prd/root-prd") in gitea.calls
    assert ("ensure_review_pr", "root-prd", "custom-prd/root-prd") in gitea.calls
    with session_factory() as db:
        assert db.query(PrdVersion).count() == 1


@pytest.mark.asyncio
async def test_only_root_work_item_can_bind(session_factory, db_session):
    """Accepting a child ID would create a second review authority for one project."""
    _, _, _, child = _review_project(db_session)

    with pytest.raises(PrdNotFound, match="root WorkItem"):
        await PrdReviewService(session_factory, FakeGitea()).ensure_current_binding(
            child.id
        )


@pytest.mark.asyncio
async def test_child_write_is_classified_not_found_before_actor_authorization(
    session_factory, db_session
):
    """A child ID must not become an authorization oracle for project membership."""
    _, _, _, child = _review_project(db_session)

    with pytest.raises(PrdNotFound, match="root WorkItem"):
        await PrdReviewService(session_factory, FakeGitea()).create_comment(
            child.id,
            CommentCreateRequest(actor_id="outsider", line=1, text="Probe"),
        )


def test_reviewer_authority_is_limited_to_project_responsibility_set(
    session_factory, db_session
):
    """An arbitrary authenticated actor must not mutate the PRD review."""
    project, _, _, _ = _review_project(db_session)
    service = PrdReviewService(session_factory, FakeGitea())

    for actor in ("approver-1", "pm-1", "owner-1"):
        service.assert_reviewer(project, actor)
    with pytest.raises(PrdForbidden):
        service.assert_reviewer(project, "outsider")


@pytest.mark.asyncio
async def test_binding_rejects_spec_or_existing_gitea_content_hash_conflict(
    session_factory, db_session
):
    """A mismatched immutable source must never be selected or overwritten."""
    _, spec, root, _ = _review_project(db_session)
    spec.content_hash = "0" * 64
    db_session.commit()
    gitea = FakeGitea()

    with pytest.raises(PrdContentConflict) as local_error:
        await PrdReviewService(session_factory, gitea).ensure_current_binding(root.id)

    assert local_error.value.code == "PRD_CONTENT_CONFLICT"
    assert gitea.calls == []

    spec.content_hash = _markdown_hash(spec.markdown)
    db_session.commit()
    path = "docs/prd/root-prd/v1.md"
    gitea.files[(path, "prd-review/root-prd")] = GiteaFile(
        path, "# A conflicting PRD\n", "existing-blob"
    )

    with pytest.raises(PrdContentConflict):
        await PrdReviewService(session_factory, gitea).ensure_current_binding(root.id)

    assert not any(call[0] == "put_file" for call in gitea.calls)
    with session_factory() as db:
        assert db.query(PrdVersion).count() == 0


@pytest.mark.asyncio
async def test_binding_rejects_wrong_gitea_path_and_corrupt_local_projection(
    session_factory, db_session
):
    """Equal bytes must not bless a file or projection bound to the wrong identity."""
    _, spec, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    expected_path = "docs/prd/root-prd/v1.md"
    gitea.files[(expected_path, "prd-review/root-prd")] = GiteaFile(
        "docs/prd/other-root/v1.md",
        "# PRD\n\nA first version.\n",
        "existing-blob",
    )
    service = PrdReviewService(session_factory, gitea)

    with pytest.raises(PrdContentConflict):
        await service.ensure_current_binding(root.id)
    assert not any(call[0] == "put_file" for call in gitea.calls)

    gitea.files[(expected_path, "prd-review/root-prd")] = GiteaFile(
        expected_path, "# PRD\n\nA first version.\n", "existing-blob"
    )
    gitea.files[(expected_path, "base-commit")] = GiteaFile(
        expected_path, "# PRD\n\nA first version.\n", "existing-blob"
    )
    binding = await service.ensure_current_binding(root.id)
    assert binding.commit_sha == "base-commit"
    assert not any(call[0] == "put_file" for call in gitea.calls)

    with session_factory() as db:
        db.get(PrdVersion, (root.id, 1)).content_hash = "f" * 64
        db.commit()
    with pytest.raises(PrdContentConflict):
        await service.ensure_current_binding(root.id)
    assert spec.content_hash != "f" * 64


@pytest.mark.asyncio
async def test_existing_file_is_bound_to_refreshed_branch_head(
    session_factory, db_session
):
    """A concurrent publisher must not leave a new file bound to a stale head."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    path = "docs/prd/root-prd/v1.md"
    content = "# PRD\n\nA first version.\n"
    gitea.files[(path, "prd-review/root-prd")] = GiteaFile(
        path, content, "existing-blob"
    )
    gitea.files[(path, "current-commit")] = GiteaFile(
        path, content, "existing-blob"
    )
    heads = iter(("stale-commit", "current-commit"))

    async def moving_head(branch: str) -> GiteaCommit:
        gitea.calls.append(("ensure_branch", branch))
        return GiteaCommit(next(heads))

    gitea.ensure_branch = moving_head

    binding = await PrdReviewService(session_factory, gitea).ensure_current_binding(
        root.id
    )

    assert binding.commit_sha == "current-commit"
    assert ("get_file", path, "current-commit") in gitea.calls


@pytest.mark.asyncio
async def test_binding_normalizes_canonically_equivalent_unicode_before_hashing(
    session_factory, db_session
):
    """Decomposed Unicode must not create a byte-distinct PRD projection."""
    _, spec, root, _ = _review_project(db_session)
    spec.markdown = "# Cafe\u0301\r\n"
    spec.content_hash = hashlib.sha256("# Caf\u00e9\n".encode("utf-8")).hexdigest()
    db_session.commit()
    gitea = FakeGitea()

    binding = await PrdReviewService(session_factory, gitea).ensure_current_binding(
        root.id
    )

    put = next(call for call in gitea.calls if call[0] == "put_file")
    assert put[2] == "# Caf\u00e9\n"
    assert binding.content_hash == hashlib.sha256(
        "# Caf\u00e9\n".encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_existing_binding_preflight_rejects_authoritative_gitea_drift(
    session_factory, db_session
):
    """Returning a locally valid binding must still prove its recorded Gitea commit."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    binding = await service.ensure_current_binding(root.id)
    gitea.files[(binding.filename, binding.commit_sha)] = GiteaFile(
        binding.filename, "# Drifted\n", "drifted-blob"
    )
    gitea.calls.clear()

    with pytest.raises(PrdContentConflict):
        await service.ensure_current_binding(root.id)

    assert gitea.calls == [
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit")
    ]


@pytest.mark.asyncio
async def test_first_binding_revalidates_current_state_before_projection_insert(
    session_factory, db_session
):
    """External evidence may remain, but a newly closed review must get no binding."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()

    def close_review():
        with session_factory() as db:
            project = db.get(Project, "project-prd")
            db.get(SpecVersion, project.current_spec_version_id).status = (
                SpecStatus.APPROVED.value
            )
            project.state_version += 1
            db.commit()

    gitea.on_ensure_review_pr = close_review

    with pytest.raises(PrdReviewClosed):
        await PrdReviewService(session_factory, gitea).ensure_current_binding(root.id)

    with session_factory() as db:
        assert db.query(PrdVersion).count() == 0
    assert any(call[0] == "put_file" for call in gitea.calls)
    assert (
        "docs/prd/root-prd/v1.md",
        "created-commit",
    ) in gitea.files


@pytest.mark.asyncio
async def test_binding_unique_race_converges_on_fully_validated_winner(
    session_factory, db_session, monkeypatch
):
    """A concurrent projection winner must be returned instead of leaking IntegrityError."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    original_commit = Session.commit
    raced = False

    def race_commit(db):
        nonlocal raced
        pending = next(
            (item for item in db.new if isinstance(item, PrdVersion)), None
        )
        if pending is None or raced:
            return original_commit(db)
        raced = True
        values = {
            column: getattr(pending, column)
            for column in (
                "wi",
                "version",
                "spec_version_id",
                "filename",
                "pr_number",
                "commit_sha",
                "content_hash",
                "spec_content_hash",
                "change_summary",
            )
        }
        db.expunge(pending)
        db.add(PrdVersion(**values))
        original_commit(db)
        raise IntegrityError("concurrent binding", {}, RuntimeError("unique"))

    monkeypatch.setattr(Session, "commit", race_commit)

    binding = await PrdReviewService(session_factory, gitea).ensure_current_binding(
        root.id
    )

    assert raced is True
    assert binding.commit_sha == "created-commit"
    with session_factory() as db:
        assert db.query(PrdVersion).count() == 1
    assert gitea.calls[-1] == (
        "get_file",
        "docs/prd/root-prd/v1.md",
        "created-commit",
    )


@pytest.mark.asyncio
async def test_binding_unique_race_rejects_winner_after_project_guard_drifts(
    session_factory, db_session, monkeypatch
):
    """A committed race winner must not bless a now-stale first-bind attempt."""
    _, _, root, _ = _review_project(db_session)
    next_markdown = "# PRD v2\n"
    db_session.add(
        SpecVersion(
            id="spec-prd-2",
            project_id="project-prd",
            revision=2,
            content={"background_and_goals": ["Second version"]},
            markdown=next_markdown,
            generation_source="PM_AGENT",
            input_refs=[],
            generator_agent_session_id="pm-session",
            generator_call_id="pm-call-2",
            parent_version_id="spec-prd-1",
            change_summary="Second specification",
            content_hash=_markdown_hash(next_markdown),
            status=SpecStatus.HUMAN_REVIEW.value,
        )
    )
    db_session.commit()
    gitea = FakeGitea()
    original_commit = Session.commit
    raced = False

    def race_and_drift_commit(db):
        nonlocal raced
        pending = next(
            (item for item in db.new if isinstance(item, PrdVersion)), None
        )
        if pending is None or raced:
            return original_commit(db)
        raced = True
        values = {
            column: getattr(pending, column)
            for column in (
                "wi",
                "version",
                "spec_version_id",
                "filename",
                "pr_number",
                "commit_sha",
                "content_hash",
                "spec_content_hash",
                "change_summary",
            )
        }
        db.expunge(pending)
        db.add(PrdVersion(**values))
        project = db.get(Project, "project-prd")
        project.current_spec_version_id = "spec-prd-2"
        project.state_version += 1
        db.get(SpecVersion, "spec-prd-1").status = SpecStatus.APPROVED.value
        original_commit(db)
        raise IntegrityError("concurrent binding", {}, RuntimeError("unique"))

    monkeypatch.setattr(Session, "commit", race_and_drift_commit)

    with pytest.raises(PrdContentConflict):
        await PrdReviewService(session_factory, gitea).ensure_current_binding(
            root.id
        )

    assert raced is True
    with session_factory() as db:
        project = db.get(Project, "project-prd")
        assert project.state_version == 3
        assert project.current_spec_version_id == "spec-prd-2"
        assert db.get(SpecVersion, "spec-prd-1").status == SpecStatus.APPROVED.value
        assert db.query(PrdVersion).count() == 1
    assert not any(
        call == ("get_file", "docs/prd/root-prd/v1.md", "created-commit")
        for call in gitea.calls
    )


@pytest.mark.asyncio
async def test_document_queries_read_authoritative_gitea_content(
    session_factory, db_session
):
    """Returning cached Spec Markdown would hide deletion or drift in Gitea."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    await service.ensure_current_binding(root.id)
    gitea.calls.clear()

    latest = await service.latest(root.id)
    specific = await service.version(root.id, 1)
    listed = await service.versions(root.id)

    assert isinstance(latest, PrdDocumentRead)
    assert latest.content == "# PRD\n\nA first version.\n"
    assert latest.version == 1
    assert specific == latest
    assert listed == [latest]
    assert gitea.calls == [
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
    ]

    with pytest.raises(PrdNotFound):
        await service.version(root.id, 2)


def _thread(
    identifier: int,
    path: str,
    *,
    body: str = "Clarify the owner.",
    resolved: bool = False,
) -> GiteaThread:
    return GiteaThread(
        comment=GiteaComment(
            id=identifier,
            path=path,
            line=3,
            body=body,
            user="reviewer",
            created_at="2026-09-01T08:00:00Z",
            resolved=resolved,
        ),
        replies=(
            GiteaReply(
                id=identifier + 1000,
                body="The owner is the platform team.",
                user="reviewer-2",
                created_at="2026-09-01T08:05:00Z",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_comments_refresh_top_level_index_without_deleting_history(
    session_factory, db_session
):
    """Treating SQLite as comment authority would lose Gitea replies or old evidence."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    await service.ensure_current_binding(root.id)
    db_session.add_all(
        [
            CommentIndex(
                id=101,
                wi=root.id,
                pr_number=17,
                path="stale.md",
                line=9,
                author_type="human",
                body="stale body",
                resolved=True,
            ),
            CommentIndex(
                id=88,
                wi=root.id,
                pr_number=17,
                path="docs/prd/root-prd/v1.md",
                line=2,
                author_type="human",
                body="historical",
                resolved=True,
            ),
        ]
    )
    db_session.commit()
    gitea.threads[17] = [
        _thread(101, "docs/prd/root-prd/v1.md"),
        _thread(202, "docs/prd/another-root/v1.md"),
    ]

    result = await service.comments(root.id)

    assert gitea.calls[-2:] == [
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
        ("list_comment_threads", 17),
    ]
    assert result == [
        PrdCommentRead(
            id=101,
            path="docs/prd/root-prd/v1.md",
            line=3,
            author_type="human",
            body="Clarify the owner.",
            resolved=False,
            replies=[
                {
                    "id": 1101,
                    "author_type": "human",
                    "body": "The owner is the platform team.",
                }
            ],
        )
    ]
    with session_factory() as db:
        refreshed = db.get(CommentIndex, 101)
        assert refreshed.path == "docs/prd/root-prd/v1.md"
        assert refreshed.line == 3
        assert refreshed.body == "Clarify the owner."
        assert refreshed.resolved is False
        assert db.get(CommentIndex, 88).body == "historical"
        assert db.get(CommentIndex, 202) is None


@pytest.mark.asyncio
async def test_comments_preflight_rejects_gitea_drift_before_listing_threads(
    session_factory, db_session
):
    """Comment projection must not run against an unverified PRD binding."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    binding = await service.ensure_current_binding(root.id)
    gitea.files[(binding.filename, binding.commit_sha)] = GiteaFile(
        binding.filename, "# Drifted\n", "drifted-blob"
    )
    gitea.calls.clear()

    with pytest.raises(PrdContentConflict):
        await service.comments(root.id)

    assert gitea.calls == [
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit")
    ]


@pytest.mark.asyncio
async def test_comment_writes_use_current_binding_and_require_open_authorized_review(
    session_factory, db_session
):
    """A write to another version, by another actor, or after approval is invalid."""
    project, spec, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    await service.ensure_current_binding(root.id)
    gitea.calls.clear()
    request = CommentCreateRequest(actor_id="pm-1", line=3, text="Add an owner.")

    await service.create_comment(root.id, request)

    assert gitea.calls == [
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
        (
            "create_comment",
            17,
            "docs/prd/root-prd/v1.md",
            3,
            "Add an owner.",
            "created-commit",
        )
    ]

    before_unauthorized = list(gitea.calls)
    with pytest.raises(PrdForbidden):
        await service.create_comment(
            root.id,
            CommentCreateRequest(actor_id="outsider", line=3, text="No access"),
        )
    assert gitea.calls == before_unauthorized

    spec.status = SpecStatus.APPROVED.value
    project.phase = ProjectPhase.AGENT_SPECS_READY.value
    db_session.commit()
    assert (await service.latest(root.id)).version == 1
    with pytest.raises(PrdReviewClosed) as closed:
        await service.create_comment(root.id, request)
    assert closed.value.code == "PRD_REVIEW_CLOSED"
    assert len([call for call in gitea.calls if call[0] == "create_comment"]) == 1


@pytest.mark.asyncio
async def test_comment_write_rejects_gitea_file_drift_before_mutation(
    session_factory, db_session
):
    """A stale or replaced PRD must not receive a comment through a trusted binding."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    binding = await service.ensure_current_binding(root.id)
    gitea.files[(binding.filename, binding.commit_sha)] = GiteaFile(
        binding.filename, "# Replaced content\n", "replaced-blob"
    )
    gitea.calls.clear()

    with pytest.raises(PrdContentConflict):
        await service.create_comment(
            root.id,
            CommentCreateRequest(actor_id="pm-1", line=1, text="Unsafe target"),
        )

    assert gitea.calls == [
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit")
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("role", PrdForbidden),
        ("status", PrdReviewClosed),
        ("current_spec", PrdContentConflict),
    ],
)
@pytest.mark.asyncio
async def test_create_comment_guard_rejects_state_changed_during_preflight(
    session_factory, db_session, mutation, expected_error
):
    """Authority and review-base changes during Gitea I/O must stop the mutation."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    await service.ensure_current_binding(root.id)

    if mutation == "current_spec":
        markdown = "# PRD v2\n"
        db_session.add(
            SpecVersion(
                id="spec-prd-2",
                project_id="project-prd",
                revision=2,
                content={"background_and_goals": ["Second version"]},
                markdown=markdown,
                generation_source="PM_AGENT",
                input_refs=[],
                generator_agent_session_id="pm-session",
                generator_call_id="pm-call-2",
                parent_version_id="spec-prd-1",
                change_summary="Second specification",
                content_hash=_markdown_hash(markdown),
                status=SpecStatus.HUMAN_REVIEW.value,
            )
        )
        db_session.commit()

    def mutate_guard():
        with session_factory() as db:
            project = db.get(Project, "project-prd")
            if mutation == "role":
                project.project_manager_ids = []
            elif mutation == "status":
                db.get(SpecVersion, "spec-prd-1").status = SpecStatus.APPROVED.value
            else:
                project.current_spec_version_id = "spec-prd-2"
            project.state_version += 1
            db.commit()

    gitea.on_get_file = mutate_guard
    gitea.calls.clear()

    with pytest.raises(expected_error):
        await service.create_comment(
            root.id,
            CommentCreateRequest(actor_id="pm-1", line=1, text="Unsafe race"),
        )

    assert not any(call[0] == "create_comment" for call in gitea.calls)


@pytest.mark.parametrize("operation", ["reply", "resolve"])
@pytest.mark.asyncio
async def test_thread_write_guard_rejects_state_version_changed_during_lookup(
    session_factory, db_session, operation
):
    """A thread found under a stale project guard must never be mutated."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    await service.ensure_current_binding(root.id)
    gitea.threads[17] = [_thread(101, "docs/prd/root-prd/v1.md")]

    def advance_state():
        with session_factory() as db:
            db.get(Project, "project-prd").state_version += 1
            db.commit()

    gitea.on_list_comment_threads = advance_state
    gitea.calls.clear()

    with pytest.raises(PrdContentConflict):
        if operation == "reply":
            await service.reply(
                root.id,
                101,
                CommentReplyRequest(
                    actor_id="owner-1", text="Unsafe race", author_type="human"
                ),
            )
        else:
            await service.resolve(
                root.id,
                101,
                CommentResolveRequest(actor_id="owner-1", resolved=True),
            )

    assert not any(
        call[0] in {"reply_comment", "set_comment_resolved"}
        for call in gitea.calls
    )


@pytest.mark.asyncio
async def test_reply_and_resolve_require_authoritative_comment_on_same_wi_pr(
    session_factory, db_session
):
    """A caller must not use this WorkItem route to mutate another PRD's thread."""
    _, _, root, _ = _review_project(db_session)
    gitea = FakeGitea()
    service = PrdReviewService(session_factory, gitea)
    await service.ensure_current_binding(root.id)
    gitea.threads[17] = [
        _thread(101, "docs/prd/root-prd/v1.md"),
        _thread(202, "docs/prd/another-root/v1.md"),
    ]
    gitea.calls.clear()

    await service.reply(
        root.id,
        101,
        CommentReplyRequest(
            actor_id="owner-1", text="Use the platform team.", author_type="human"
        ),
    )
    await service.resolve(
        root.id,
        101,
        CommentResolveRequest(actor_id="approver-1", resolved=True),
    )

    assert gitea.calls == [
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
        ("list_comment_threads", 17),
        ("reply_comment", 17, 101, "Use the platform team."),
        ("get_file", "docs/prd/root-prd/v1.md", "created-commit"),
        ("list_comment_threads", 17),
        ("set_comment_resolved", 101, True),
    ]

    before = list(gitea.calls)
    with pytest.raises(PrdNotFound):
        await service.reply(
            root.id,
            202,
            CommentReplyRequest(
                actor_id="owner-1", text="Cross WI", author_type="human"
            ),
        )
    with pytest.raises(PrdNotFound):
        await service.resolve(
            root.id,
            999,
            CommentResolveRequest(actor_id="owner-1", resolved=True),
        )
    assert not any(
        call[0] in {"reply_comment", "set_comment_resolved"}
        for call in gitea.calls[len(before) :]
    )


def test_public_reply_contract_still_rejects_agent_authorship():
    """The public service boundary must not admit a forged PM-agent reply."""
    with pytest.raises(ValidationError):
        CommentReplyRequest(
            actor_id="owner-1", text="Forged agent reply", author_type="agent"
        )
