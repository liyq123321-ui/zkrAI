"""Durable publication of one frozen Gitea PRD review snapshot."""

from collections import deque
import hashlib

import pytest
from sqlalchemy import event, update
from sqlalchemy.sql.dml import Update

from app.database.models import (
    AgentCall,
    AuditEvent,
    ClarificationRequest,
    CommandAttempt,
    PrdPrototype,
    PrdVersion,
    ProcessedCommand,
    Project,
    ReviewTask,
    SpecReview,
    SpecVersion,
    WorkItem,
)
from app.domain.types import (
    CommandAction,
    HtmlPrototypePayload,
    PrdRewriteOutput,
    ProjectPhase,
    ReviewKind,
    ReviewVerdict,
    RewriteAction,
    SemanticReview,
    SpecStatus,
    WorkItemKind,
)
from app.services.gitea import (
    GiteaComment,
    GiteaCommit,
    GiteaError,
    GiteaFile,
    GiteaIdentity,
    GiteaReply,
    GiteaThread,
)
from app.services.command_service import _canonical_hash
from app.services.pm_agent import NoUnresolvedComments, ReviewPublishCoordinator
from app.services.prd_review import PrdContentConflict, _markdown_hash
from app.services.spec_service import _render_markdown
from tests.helpers.fake_agent import ScriptedAgentGateway


BASE_MARKDOWN = "# Project Spec\n"
BASE_SHA = "b" * 40
BASE_PATH = "docs/prd/root-1/v1.md"


def _thread(comment_id: int, body: str, *, replies=(), resolved=False) -> GiteaThread:
    return GiteaThread(
        comment=GiteaComment(
            id=comment_id,
            path=BASE_PATH,
            line=comment_id - 90,
            body=body,
            user="reviewer",
            created_at=f"2026-09-01T08:00:{comment_id - 100:02d}Z",
            resolved=resolved,
        ),
        replies=tuple(replies),
    )


class FakeGitea:
    """Stateful external boundary; local service/database behavior stays real."""

    def __init__(
        self,
        threads=(),
        *,
        put_failures=(),
        reply_failures=None,
        list_hook=None,
        branch_results=(),
    ):
        self.threads = list(threads)
        self.files = {BASE_PATH: BASE_MARKDOWN}
        self.branch_sha = BASE_SHA
        self.calls: list[tuple] = []
        self.put_failures = deque(put_failures)
        self.reply_failures = {
            comment_id: deque(failures)
            for comment_id, failures in (reply_failures or {}).items()
        }
        self.list_hook = list_hook
        self.branch_results = deque(branch_results)
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

    async def get_file(self, path: str, ref: str) -> GiteaFile:
        self.calls.append(("get_file", path, ref))
        if path not in self.files:
            raise GiteaError("GITEA_NOT_FOUND", "Gitea resource was not found.", False)
        return GiteaFile(path=path, content=self.files[path], sha=f"blob:{path}")

    async def list_comment_threads(self, pr_number: int) -> list[GiteaThread]:
        self.calls.append(("list_comment_threads", pr_number))
        if self.list_hook is not None:
            hook, self.list_hook = self.list_hook, None
            hook()
        return list(self.threads)

    async def ensure_branch(self, branch: str) -> GiteaCommit:
        self.calls.append(("ensure_branch", branch))
        if self.branch_results:
            self.branch_sha = self.branch_results.popleft()
        return GiteaCommit(self.branch_sha)

    async def put_file(
        self,
        path: str,
        content: str,
        branch: str,
        message: str,
        sha: str | None = None,
    ) -> GiteaCommit:
        self.calls.append(("put_file", path, content, branch, message, sha))
        if self.put_failures:
            error = self.put_failures.popleft()
            if error is not None:
                raise error
        self.files[path] = content
        self.branch_sha = "c" * 40
        return GiteaCommit(self.branch_sha)

    async def reply_comment(self, pr_number: int, comment_id: int, body: str) -> GiteaReply:
        self.calls.append(("reply_comment", pr_number, comment_id, body))
        failures = self.reply_failures.get(comment_id)
        if failures:
            error = failures.popleft()
            if error is not None:
                raise error
        for index, thread in enumerate(self.threads):
            if thread.comment.id == comment_id:
                reply = GiteaReply(
                    id=9000 + len(thread.replies),
                    body=body,
                    user="firstflight",
                    created_at="2026-09-01T09:00:00Z",
                    user_id=99,
                )
                self.threads[index] = GiteaThread(
                    comment=thread.comment,
                    replies=thread.replies + (reply,),
                )
                return reply
        raise AssertionError(f"unknown comment {comment_id}")


def _seed_review(db, complete_brief, valid_spec) -> None:
    content_hash = hashlib.sha256(BASE_MARKDOWN.encode()).hexdigest()
    db.add_all(
        [
            Project(
                id="publish-project",
                session_id="publish-session",
                creation_request_id="publish-request",
                brief=complete_brief.model_dump(mode="json"),
                final_approver="owner",
                project_manager_ids=["pm"],
                root_owner_ids=["root-owner"],
                phase=ProjectPhase.REVIEW.value,
                state_version=4,
                current_spec_version_id="publish-spec-1",
            ),
            SpecVersion(
                id="publish-spec-1",
                project_id="publish-project",
                revision=1,
                content=valid_spec.model_dump(mode="json"),
                markdown=BASE_MARKDOWN,
                generation_source="PM_AGENT",
                input_refs=["artifact:publish-request"],
                generator_agent_session_id="publish-pm-session",
                generator_call_id="publish-call-1",
                parent_version_id=None,
                change_summary="Initial specification",
                content_hash=content_hash,
                status=SpecStatus.HUMAN_REVIEW.value,
            ),
            WorkItem(
                id="root-1",
                project_id="publish-project",
                local_key="ROOT",
                kind=WorkItemKind.ROOT.value,
                parent_id=None,
                executable=False,
            ),
            PrdVersion(
                wi="root-1",
                version=1,
                spec_version_id="publish-spec-1",
                filename=BASE_PATH,
                pr_number=7,
                commit_sha=BASE_SHA,
                content_hash=content_hash,
                spec_content_hash=content_hash,
                change_summary="Initial specification",
            ),
        ]
    )
    db.commit()


def _rewrite(valid_spec, *responses) -> PrdRewriteOutput:
    return PrdRewriteOutput(
        spec=valid_spec.model_copy(
            update={
                "main_flows": [
                    "Submit, review, publish, and recover the project specification"
                ]
            }
        ),
        responses=list(responses),
        change_summary="Applied the frozen Gitea review.",
    )


def _response(comment_id: int, action: RewriteAction, note: str) -> dict[str, object]:
    return {"comment_id": comment_id, "action": action, "note": note}


def _agent(valid_spec, passing_semantic_review, *responses):
    return ScriptedAgentGateway(
        rewrite_results=deque([_rewrite(valid_spec, *responses)]),
        review_results=deque([passing_semantic_review]),
    )


@pytest.mark.asyncio
async def test_create_rejects_an_empty_unresolved_snapshot_without_agent_work(
    session_factory, db_session, complete_brief, valid_spec
):
    """Removing the empty-snapshot gate would schedule ungrounded PM work."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Already handled", resolved=True)])
    agent = ScriptedAgentGateway()
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)

    with pytest.raises(NoUnresolvedComments) as raised:
        await coordinator.create_or_resume("root-1", "owner")

    assert raised.value.code == "NO_UNRESOLVED_COMMENTS"
    assert agent.calls == []
    with session_factory() as db:
        assert db.query(ReviewTask).count() == 0


@pytest.mark.asyncio
async def test_auto_resolve_findings_publishes_without_gitea_comments(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Explicit opt-in turns current findings into frozen rewrite evidence."""
    _seed_review(db_session, complete_brief, valid_spec)
    finding = {
        "code": "SCOPE-001",
        "severity": "MAJOR",
        "spec_path": "/system_boundaries/0",
        "message": "The runtime boundary is ambiguous.",
        "suggested_resolution": "State that the runtime is local-only.",
        "blocks_progress": True,
    }
    db_session.add(
        SpecReview(
            id="review-with-finding",
            project_id="publish-project",
            spec_version_id="publish-spec-1",
            kind=ReviewKind.AGENT.value,
            reviewer_id="reviewer-agent",
            input_spec_hash=hashlib.sha256(BASE_MARKDOWN.encode()).hexdigest(),
            verdict=ReviewVerdict.REJECT.value,
            findings=[finding],
            comments=None,
        )
    )
    db_session.commit()
    gitea = FakeGitea([])
    agent = ScriptedAgentGateway(
        rewrite_results=deque([
            PrdRewriteOutput(
                spec=valid_spec,
                responses=[],
                change_summary="Applied the automatic review recommendation.",
            )
        ]),
        review_results=deque([passing_semantic_review]),
        prototype_results=deque([
            HtmlPrototypePayload(
                title="Revised PRD prototype",
                html="<!doctype html><html><body>Revised flow</body></html>",
                generation_summary="Updated the primary flow.",
            )
        ]),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)

    task = await coordinator.create_or_resume(
        "root-1", "owner", auto_resolve_findings=True
    )
    await coordinator.run(task.id)

    public = coordinator.task(task.id)
    assert public.status == "done"
    assert public.new_version == 2
    assert not any(call[0] == "reply_comment" for call in gitea.calls)
    rewrite_payload = next(payload for operation, payload in agent.calls if operation == "rewrite_prd")
    assert rewrite_payload["comments"] == []
    assert rewrite_payload["auto_resolve_review_findings"] is True
    assert rewrite_payload["review_findings"] == [
        {"label": "non_control_input", **finding}
    ]
    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        assert stored.auto_resolve_findings is True
        assert stored.finding_snapshot == [finding]
        prototype = db.query(PrdPrototype).one()
        assert prototype.spec_version_id == stored.new_spec_version_id
        assert prototype.status == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_changes", [True, False])
async def test_auto_resolve_findings_repairs_or_rejects_an_unchanged_rewrite(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
    repair_changes,
):
    """Automatic review must not silently publish another identical PRD."""

    _seed_review(db_session, complete_brief, valid_spec)
    base_spec = valid_spec.model_copy(
        update={"source_refs": ["artifact:publish-request"]}
    )
    base_content = base_spec.model_dump(mode="json")
    base_markdown = _render_markdown(base_content)
    base_hash = _canonical_hash(base_content)
    finding = {
        "code": "NEEDS_HUMAN_DECISION",
        "severity": "BLOCKER",
        "spec_path": "/open_questions",
        "message": "Choose a deterministic fallback.",
        "suggested_resolution": "Use the conservative fallback and document it.",
        "blocks_progress": True,
    }
    with session_factory() as db:
        parent = db.get(SpecVersion, "publish-spec-1")
        parent.content = base_content
        parent.markdown = base_markdown
        parent.content_hash = base_hash
        binding = db.get(PrdVersion, ("root-1", 1))
        binding.content_hash = _markdown_hash(base_markdown)
        binding.spec_content_hash = base_hash
        db.add(
            SpecReview(
                id="review-requiring-repair",
                project_id="publish-project",
                spec_version_id="publish-spec-1",
                kind=ReviewKind.AGENT.value,
                reviewer_id="reviewer-agent",
                input_spec_hash=base_hash,
                verdict=ReviewVerdict.REJECT.value,
                findings=[finding],
                comments=None,
            )
        )
        db.commit()
    repaired_spec = (
        base_spec.model_copy(
            update={
                "main_flows": [
                    "Submit, review, and use the conservative documented fallback"
                ]
            }
        )
        if repair_changes
        else base_spec
    )
    unchanged = PrdRewriteOutput(
        spec=base_spec,
        responses=[],
        change_summary="No effective change.",
    )
    repaired = PrdRewriteOutput(
        spec=repaired_spec,
        responses=[],
        change_summary="Applied the conservative fallback.",
    )
    gitea = FakeGitea([])
    gitea.files[BASE_PATH] = base_markdown
    agent = ScriptedAgentGateway(
        rewrite_results=deque([unchanged, repaired]),
        review_results=deque([passing_semantic_review]),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)

    task = await coordinator.create_or_resume(
        "root-1", "owner", auto_resolve_findings=True
    )
    await coordinator.run(task.id)

    public = coordinator.task(task.id)
    with session_factory() as db:
        diagnostic_task = db.get(ReviewTask, task.id)
        diagnostic_calls = [
            (call.id, call.operation, call.status)
            for call in db.query(AgentCall).order_by(AgentCall.started_at).all()
        ]
    rewrite_payloads = [
        payload for operation, payload in agent.calls if operation == "rewrite_prd"
    ]
    assert len(rewrite_payloads) == 2
    assert rewrite_payloads[0]["automatic_resolution_policy"]["decision_authority"] == (
        "AUTHORIZED_AGENT_DISCRETION"
    )
    assert rewrite_payloads[1]["repair_attempt"] == 1
    assert rewrite_payloads[1]["repair_feedback"]["required_action"]
    if not repair_changes:
        assert public.status == "error"
        assert public.error.startswith("The Agent returned the same PRD twice")
        assert diagnostic_task.error_code == "AUTO_REVIEW_NO_CHANGE"
        assert diagnostic_task.new_spec_version_id is None
        assert diagnostic_calls and all(
            status == "RESULT_READY" for _, _, status in diagnostic_calls
        )
        return
    assert public.status == "done", (
        diagnostic_task.error_code,
        diagnostic_task.error,
        diagnostic_calls,
    )
    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        next_version = db.get(SpecVersion, stored.new_spec_version_id)
        assert next_version.content != base_content
        assert {
            call.status
            for call in db.query(AgentCall).filter_by(operation="rewrite_prd").all()
        } == {"RESULT_READY", "SUCCEEDED"}


@pytest.mark.asyncio
async def test_create_freezes_the_base_comments_and_reply_history(
    session_factory, db_session, complete_brief, valid_spec
):
    """Reading live Gitea state during execution would let late comments alter the command."""
    _seed_review(db_session, complete_brief, valid_spec)
    reply = GiteaReply(
        id=301,
        body="Please keep the fallback explicit.",
        user="owner",
        created_at="2026-09-01T08:02:00Z",
    )
    gitea = FakeGitea(
        [_thread(101, "Add a publication step", replies=(reply,)), _thread(102, "Name the fallback")]
    )
    coordinator = ReviewPublishCoordinator(
        session_factory, gitea, ScriptedAgentGateway()
    )

    task = await coordinator.create_or_resume("root-1", "owner")
    gitea.threads.append(_thread(103, "Late comment"))

    assert task.base_version == 1
    assert task.base_commit_sha == BASE_SHA
    assert task.comment_ids == [101, 102]
    assert [item["body"] for item in task.comment_snapshot] == [
        "Add a publication step",
        "Name the fallback",
    ]
    assert task.comment_snapshot[0]["replies"] == [
        {
            "id": 301,
            "body": "Please keep the fallback explicit.",
            "user": "owner",
            "created_at": "2026-09-01T08:02:00Z",
            "extra": {},
        }
    ]
    assert 103 not in task.comment_ids


@pytest.mark.asyncio
async def test_same_snapshot_reuses_and_resets_the_same_failed_task(
    session_factory, db_session, complete_brief, valid_spec
):
    """Replacing a failed row would lose the command receipt needed for safe retry."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    coordinator = ReviewPublishCoordinator(
        session_factory, gitea, ScriptedAgentGateway()
    )

    first = await coordinator.create_or_resume("root-1", "owner")
    with session_factory() as db:
        stored = db.get(ReviewTask, first.id)
        stored.status = "error"
        stored.error_code = "GITEA_UNAVAILABLE"
        stored.error = "private upstream diagnostic"
        db.commit()

    list_calls_before = sum(
        call[0] == "list_comment_threads" for call in gitea.calls
    )
    gitea.threads[0] = _thread(101, "A later live edit belongs to the next task")

    resumed = await coordinator.create_or_resume("root-1", "owner")

    assert resumed.id == first.id
    assert resumed.status == "pending"
    assert resumed.error_code is None
    assert resumed.error is None
    assert resumed.comment_snapshot == first.comment_snapshot
    assert sum(call[0] == "list_comment_threads" for call in gitea.calls) == list_calls_before
    with session_factory() as db:
        assert db.query(ReviewTask).count() == 1


@pytest.mark.asyncio
async def test_only_original_initiator_can_resume_and_revocation_blocks_before_effects(
    session_factory, db_session, complete_brief, valid_spec
):
    """A task keeps its exact principal; current role membership is rechecked before effects."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    coordinator = ReviewPublishCoordinator(
        session_factory, gitea, ScriptedAgentGateway()
    )
    task = await coordinator.create_or_resume("root-1", "owner")
    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        stored.status = "error"
        stored.error_code = "GITEA_UNAVAILABLE"
        stored.error = "retry"
        db.commit()

    from app.services.prd_review import PrdForbidden

    with pytest.raises(PrdForbidden):
        await coordinator.create_or_resume("root-1", "pm")

    resumed = await coordinator.create_or_resume("root-1", "owner")
    with session_factory() as db:
        project = db.get(Project, "publish-project")
        project.final_approver = "replacement"
        db.commit()
    calls_before = list(gitea.calls)

    await coordinator.run(resumed.id)

    assert coordinator.task(resumed.id).status == "error"
    assert gitea.calls == calls_before


@pytest.mark.asyncio
async def test_pm_initiator_is_used_for_command_and_audit_attribution(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """The coordinator must not silently substitute the final approver identity."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "pm")

    await coordinator.run(task.id)

    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        attempt = db.query(CommandAttempt).filter_by(
            command_id=f"publish-review:{task.id}"
        ).one()
        events = db.query(AuditEvent).filter(
            AuditEvent.event_type.in_(
                ["COMMAND_APPLIED", "REVIEW_COMMENT_REPLIED", "REVIEW_PUBLICATION_COMPLETED"]
            )
        ).all()
        assert stored.initiator_actor_id == "pm"
        assert attempt.status == "MATERIALIZED"
        assert events and {event.actor_id for event in events} == {"pm"}


@pytest.mark.asyncio
async def test_concurrent_resume_claim_is_not_overwritten_by_error_reset(
    session_factory,
    db_session,
    engine,
    complete_brief,
    valid_spec,
):
    """An unqualified ORM flush would lose a concurrent pending-to-processing claim."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    coordinator = ReviewPublishCoordinator(
        session_factory, gitea, ScriptedAgentGateway()
    )
    first = await coordinator.create_or_resume("root-1", "owner")
    with session_factory() as db:
        stored = db.get(ReviewTask, first.id)
        stored.status = "error"
        stored.error_code = "GITEA_UNAVAILABLE"
        stored.error = "private upstream diagnostic"
        db.commit()

    interleaved = False

    def claim_between_read_and_reset(
        connection, clauseelement, multiparams, params, execution_options
    ):
        nonlocal interleaved
        if (
            interleaved
            or not isinstance(clauseelement, Update)
            or clauseelement.table.name != ReviewTask.__tablename__
        ):
            return
        interleaved = True
        connection.execute(
            update(ReviewTask)
            .where(ReviewTask.id == first.id, ReviewTask.status == "error")
            .values(status="pending", error_code=None, error=None)
        )
        connection.execute(
            update(ReviewTask)
            .where(ReviewTask.id == first.id, ReviewTask.status == "pending")
            .values(status="processing")
        )

    event.listen(engine, "before_execute", claim_between_read_and_reset)
    try:
        resumed = await coordinator.create_or_resume("root-1", "owner")
    finally:
        event.remove(engine, "before_execute", claim_between_read_and_reset)

    assert interleaved is True
    assert resumed.id == first.id
    assert resumed.status == "processing"
    with session_factory() as db:
        stored = db.get(ReviewTask, first.id)
        assert stored.status == "processing"
        assert stored.error_code is None
        assert stored.error is None


@pytest.mark.asyncio
async def test_create_revalidates_a_removed_current_spec_after_external_snapshot(
    session_factory, db_session, complete_brief, valid_spec
):
    """Opening the task transaction without revalidation would persist a stale base."""
    _seed_review(db_session, complete_brief, valid_spec)

    def remove_current_spec():
        with session_factory() as db:
            db.get(Project, "publish-project").current_spec_version_id = None
            db.commit()

    gitea = FakeGitea(
        [_thread(101, "Add a publication step")], list_hook=remove_current_spec
    )
    coordinator = ReviewPublishCoordinator(
        session_factory, gitea, ScriptedAgentGateway()
    )

    with pytest.raises(PrdContentConflict):
        await coordinator.create_or_resume("root-1", "owner")

    with session_factory() as db:
        assert db.query(ReviewTask).count() == 0


@pytest.mark.asyncio
async def test_run_publishes_one_local_revision_file_projection_and_truthful_replies(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Reordering or omitting stages would leave publication incomplete or unauditable."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea(
        [_thread(101, "Add a publication step"), _thread(102, "Name the fallback")]
    )
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
        _response(102, RewriteAction.CLARIFIED, "Named the fallback."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(task.id)

    public = coordinator.task(task.id)
    assert public.status == "done"
    assert public.base_version == 1
    assert public.new_version == 2
    assert public.new_commit_sha == "c" * 40
    assert public.error is None
    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]
    put_calls = [call for call in gitea.calls if call[0] == "put_file"]
    assert len(put_calls) == 1
    assert put_calls[0][1] == "docs/prd/root-1/v2.md"
    reply_calls = [call for call in gitea.calls if call[0] == "reply_comment"]
    assert [call[2] for call in reply_calls] == [102, 101]
    reply_by_comment = {call[2]: call[3] for call in reply_calls}
    assert "<!-- firstflight-receipt:v1:" in reply_by_comment[101]
    assert f"firstflight-receipt:v1:{task.id}:101" in reply_by_comment[101]
    assert "MODIFIED" in reply_by_comment[101]
    assert "Added the publication step." in reply_by_comment[101]
    assert "v2" in reply_by_comment[101]
    assert "c" * 40 in reply_by_comment[101]
    assert "待人工评审" in reply_by_comment[101]
    with session_factory() as db:
        project = db.get(Project, "publish-project")
        revised = db.get(SpecVersion, project.current_spec_version_id)
        projection = db.get(PrdVersion, ("root-1", 2))
        stored = db.get(ReviewTask, task.id)
        assert revised.revision == 2
        assert revised.parent_version_id == "publish-spec-1"
        assert projection.spec_version_id == revised.id
        assert projection.commit_sha == "c" * 40
        assert projection.content_hash == _markdown_hash(revised.markdown)
        assert projection.spec_content_hash == revised.content_hash
        assert stored.status == "done"
        assert db.query(AuditEvent).filter_by(
            event_type="REVIEW_PUBLICATION_COMPLETED"
        ).count() == 1


@pytest.mark.asyncio
async def test_successful_no_content_change_publication_still_creates_immutable_next_revision(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """A successful review publication must never bind its task back to the parent."""
    _seed_review(db_session, complete_brief, valid_spec)
    canonical = valid_spec.model_copy(
        update={"source_refs": ["artifact:publish-request"]}
    )
    canonical_hash = _canonical_hash(canonical.model_dump(mode="json"))
    with session_factory() as db:
        parent = db.get(SpecVersion, "publish-spec-1")
        parent.content = canonical.model_dump(mode="json")
        parent.content_hash = canonical_hash
        db.get(PrdVersion, ("root-1", 1)).spec_content_hash = canonical_hash
        db.commit()
    gitea = FakeGitea([_thread(101, "Keep the current policy")])
    agent = ScriptedAgentGateway(
        rewrite_results=deque(
            [
                PrdRewriteOutput(
                    spec=canonical,
                    responses=[
                        _response(
                            101,
                            RewriteAction.NOT_ACCEPTED,
                            "The current policy is retained.",
                        )
                    ],
                    change_summary="Reviewed without changing policy content.",
                )
            ]
        ),
        review_results=deque([passing_semantic_review]),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(task.id)

    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        next_version = db.get(SpecVersion, stored.new_spec_version_id)
        assert stored.status == "done"
        assert stored.new_version == 2
        assert next_version.parent_version_id == "publish-spec-1"
        assert next_version.content_hash == canonical_hash
        assert db.get(PrdVersion, ("root-1", 2)).spec_version_id == next_version.id


@pytest.mark.asyncio
async def test_stale_base_fails_before_any_agent_call(
    session_factory, db_session, complete_brief, valid_spec
):
    """Checking the base after command preparation would rewrite stale evidence."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    agent = ScriptedAgentGateway()
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")
    with session_factory() as db:
        db.get(PrdVersion, ("root-1", 1)).commit_sha = "d" * 40
        db.commit()

    await coordinator.run(task.id)

    assert agent.calls == []
    public = coordinator.task(task.id)
    assert public.status == "error"
    assert public.error == "The reviewed PRD version changed before publication."
    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        assert stored.error_code == "STALE_REVIEW_BASE"
        assert db.query(CommandAttempt).count() == 0


@pytest.mark.asyncio
async def test_external_branch_drift_fails_before_any_agent_call(
    session_factory, db_session, complete_brief, valid_spec
):
    """Checking only SQLite would miss an externally advanced review branch."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    agent = ScriptedAgentGateway()
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")
    gitea.branch_sha = "d" * 40

    await coordinator.run(task.id)

    assert agent.calls == []
    with session_factory() as db:
        assert db.get(ReviewTask, task.id).error_code == "STALE_REVIEW_BASE"


@pytest.mark.asyncio
async def test_branch_drift_immediately_before_missing_file_write_skips_all_gitea_effects(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Reusing an earlier branch head would write v2 after the frozen base drifted."""
    _seed_review(db_session, complete_brief, valid_spec)
    drifted_sha = "d" * 40
    gitea = FakeGitea(
        [_thread(101, "Add a publication step")],
        branch_results=(BASE_SHA, BASE_SHA, drifted_sha),
    )
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(task.id)

    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]
    assert len([call for call in gitea.calls if call[0] == "ensure_branch"]) == 3
    assert not any(call[0] == "put_file" for call in gitea.calls)
    assert not any(call[0] == "reply_comment" for call in gitea.calls)
    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        assert stored.status == "error"
        assert stored.error_code == "STALE_REVIEW_BASE"
        assert stored.new_version == 2
        assert stored.new_commit_sha is None
        assert db.get(PrdVersion, ("root-1", 2)) is None


@pytest.mark.asyncio
async def test_file_failure_retains_local_revision_and_retry_replays_same_command(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """A retry that reruns the command could generate a divergent second Spec revision."""
    _seed_review(db_session, complete_brief, valid_spec)
    secret = "upstream token=do-not-expose"
    gitea = FakeGitea(
        [_thread(101, "Add a publication step")],
        put_failures=(RuntimeError(secret), None),
    )
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    first = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(first.id)

    failed = coordinator.task(first.id)
    assert failed.status == "error"
    assert failed.new_version == 2
    assert failed.new_commit_sha is None
    assert secret not in failed.error
    with session_factory() as db:
        stored = db.get(ReviewTask, first.id)
        command = db.query(ProcessedCommand).one()
        rewrite_call = db.query(AgentCall).filter_by(operation="rewrite_prd").one()
        assert stored.error == secret
        assert stored.new_spec_version_id is not None
        assert db.query(SpecVersion).filter_by(project_id="publish-project").count() == 2
        assert db.get(PrdVersion, ("root-1", 2)) is None
        original_command_id = command.command_id
        assert rewrite_call.request["command_id"] == original_command_id

    resumed = await coordinator.create_or_resume("root-1", "owner")
    assert resumed.id == first.id
    await coordinator.run(resumed.id)

    assert coordinator.task(first.id).status == "done"
    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]
    with session_factory() as db:
        assert db.query(SpecVersion).filter_by(project_id="publish-project").count() == 2
        assert db.query(ProcessedCommand).one().command_id == original_command_id
        assert db.query(AgentCall).filter_by(operation="rewrite_prd").one().request[
            "command_id"
        ] == original_command_id
        assert db.get(PrdVersion, ("root-1", 2)) is not None


@pytest.mark.asyncio
async def test_need_clarification_file_failure_resumes_only_the_same_partial_task(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
):
    """A failed publish resumes the same REWORK draft without regenerating it."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea(
        [_thread(101, "Clarify the rollout owner")],
        put_failures=(RuntimeError("file endpoint unavailable"), None),
    )
    agent = ScriptedAgentGateway(
        rewrite_results=deque(
            [
                _rewrite(
                    valid_spec,
                    _response(
                        101,
                        RewriteAction.NEEDS_HUMAN_CONFIRMATION,
                        "The rollout owner still needs confirmation.",
                    ),
                )
            ]
        ),
        review_results=deque(
            [SemanticReview(verdict=ReviewVerdict.NEED_INFO, findings=[])]
        ),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    first = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(first.id)

    with session_factory() as db:
        failed = db.get(ReviewTask, first.id)
        current = db.get(SpecVersion, failed.new_spec_version_id)
        command_id = db.query(ProcessedCommand).one().command_id
        assert failed.status == "error"
        assert failed.new_version == 2
        assert current.status == SpecStatus.REWORK.value

    resumed = await coordinator.create_or_resume("root-1", "owner")
    assert resumed.id == first.id
    await coordinator.run(resumed.id)

    assert coordinator.task(first.id).status == "done"
    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]
    with session_factory() as db:
        assert db.query(SpecVersion).filter_by(project_id="publish-project").count() == 2
        assert db.query(ProcessedCommand).one().command_id == command_id
        assert db.get(PrdVersion, ("root-1", 2)) is not None


@pytest.mark.asyncio
async def test_preparing_retry_reuses_durable_semantic_review_result(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
    monkeypatch,
):
    """A crash after saving review evidence must not ask the reviewer twice."""
    from app.services.spec_service import SpecService

    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")
    record_result = SpecService._record_call_result

    class SimulatedProcessInterruption(BaseException):
        pass

    def interrupt_after_review_is_saved(self, project_id, call_id, result):
        record_result(self, project_id, call_id, result)
        with session_factory() as db:
            if db.get(AgentCall, call_id).operation == "review_spec":
                raise SimulatedProcessInterruption("simulated process stop")

    monkeypatch.setattr(
        SpecService, "_record_call_result", interrupt_after_review_is_saved
    )
    with pytest.raises(SimulatedProcessInterruption):
        await coordinator.run(task.id)

    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(
            command_id=f"publish-review:{task.id}"
        ).one()
        reviewer_call = db.query(AgentCall).filter_by(operation="review_spec").one()
        assert attempt.status == "PREPARING"
        assert reviewer_call.status == "RESULT_READY"
        assert reviewer_call.response == passing_semantic_review.model_dump(mode="json")
        original_reviewer_session_id = reviewer_call.agent_session_id

    monkeypatch.setattr(SpecService, "_record_call_result", record_result)
    assert coordinator.mark_interrupted_tasks() == 1
    resumed = await coordinator.create_or_resume("root-1", "owner")
    assert resumed.id == task.id
    await coordinator.run(resumed.id)

    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]
    with session_factory() as db:
        attempt = db.query(CommandAttempt).filter_by(
            command_id=f"publish-review:{task.id}"
        ).one()
        assert attempt.status == "MATERIALIZED"
        assert db.query(AgentCall).filter_by(operation="review_spec").count() == 1
        assert db.query(AgentCall).filter_by(
            operation="review_spec", status="SUCCEEDED"
        ).count() == 1
        agent_review = db.query(SpecReview).filter_by(kind=ReviewKind.AGENT.value).one()
        assert agent_review.reviewer_id == original_reviewer_session_id


@pytest.mark.asyncio
async def test_retry_adopts_an_exact_file_created_before_commit_evidence_was_saved(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
    monkeypatch,
):
    """A crash after Gitea success but before SQLite evidence must not duplicate the file."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")
    real_record = coordinator._record_commit
    monkeypatch.setattr(
        coordinator,
        "_record_commit",
        lambda *_: (_ for _ in ()).throw(RuntimeError("simulated process stop")),
    )

    await coordinator.run(task.id)

    assert coordinator.task(task.id).status == "error"
    assert len([call for call in gitea.calls if call[0] == "put_file"]) == 1
    monkeypatch.setattr(coordinator, "_record_commit", real_record)
    resumed = await coordinator.create_or_resume("root-1", "owner")
    await coordinator.run(resumed.id)

    assert coordinator.task(task.id).status == "done"
    assert len([call for call in gitea.calls if call[0] == "put_file"]) == 1
    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]


@pytest.mark.asyncio
async def test_partial_reply_retry_detects_exact_marker_and_skips_prior_reply(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Blind reply retry would duplicate already successful external effects."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea(
        [_thread(101, "Add a publication step"), _thread(102, "Name the fallback")],
        reply_failures={101: (RuntimeError("reply endpoint unavailable"), None)},
    )
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
        _response(102, RewriteAction.CLARIFIED, "Named the fallback."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    first = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(first.id)

    assert coordinator.task(first.id).status == "error"
    assert sum(
        f"firstflight-receipt:v1:{first.id}:102" in reply.body
        for reply in gitea.threads[1].replies
    ) == 1

    resumed = await coordinator.create_or_resume("root-1", "owner")
    assert resumed.id == first.id
    await coordinator.run(resumed.id)

    assert coordinator.task(first.id).status == "done"
    assert sum(
        f"firstflight-receipt:v1:{first.id}:101" in reply.body
        for reply in gitea.threads[0].replies
    ) == 1
    assert sum(
        f"firstflight-receipt:v1:{first.id}:102" in reply.body
        for reply in gitea.threads[1].replies
    ) == 1
    assert [call[2] for call in gitea.calls if call[0] == "reply_comment"] == [
        102,
        101,
        101,
    ]
    assert [operation for operation, _ in agent.calls] == ["rewrite_prd", "review_spec"]


@pytest.mark.asyncio
async def test_marker_looking_human_reply_cannot_suppress_agent_receipt(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Only an exact signed service reply can suppress a required receipt."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")
    original = gitea.threads[0]
    gitea.threads[0] = GiteaThread(
        comment=original.comment,
        replies=(
            GiteaReply(
                id=9010,
                body=f"<!-- firstflight-receipt:v1:{task.id}:101:{'0' * 64} -->",
                user="human-reviewer",
                created_at="2026-09-01T08:30:00Z",
                user_id=41,
            ),
        ),
    )

    await coordinator.run(task.id)

    assert coordinator.task(task.id).status == "done"
    assert [call[2] for call in gitea.calls if call[0] == "reply_comment"] == [101]
    verified = coordinator._reviews._verified_agent_reply_ids("root-1", gitea.threads)
    assert 9010 not in verified
    assert len(verified) == 1
    # The comment belongs to v1 and is intentionally absent from the active
    # v2 review queue after publication.
    assert await coordinator._reviews.comments("root-1") == []


@pytest.mark.asyncio
async def test_retry_backfills_local_evidence_for_an_existing_exact_reply_marker(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """A crash after external reply success must retain local audit evidence on retry."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Add a publication step")])
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(101, RewriteAction.MODIFIED, "Added the publication step."),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")
    signed_body = coordinator._signed_reply_body(
        task.id,
        101,
        RewriteAction.MODIFIED,
        "Added the publication step.",
        2,
        "c" * 40,
        "自动审核已通过，待人工评审",
    )
    root = gitea.threads[0]
    gitea.threads[0] = GiteaThread(
        comment=root.comment,
        replies=(
            GiteaReply(
                id=9001,
                body=signed_body,
                user="firstflight",
                created_at="2026-09-01T08:30:00Z",
                user_id=99,
            ),
        ),
    )

    await coordinator.run(task.id)

    assert coordinator.task(task.id).status == "done"
    assert not any(call[0] == "reply_comment" for call in gitea.calls)
    with session_factory() as db:
        evidence = db.query(AuditEvent).filter_by(
            event_type="REVIEW_COMMENT_REPLIED"
        ).all()
        assert len(evidence) == 1
        assert evidence[0].payload["comment_id"] == 101


@pytest.mark.asyncio
async def test_human_confirmation_reply_never_claims_completion(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """A generic success sentence would misrepresent an unresolved PM decision."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Choose the retention duration")])
    agent = _agent(
        valid_spec,
        passing_semantic_review,
        _response(
            101,
            RewriteAction.NEEDS_HUMAN_CONFIRMATION,
            "The retention duration still needs an owner decision.",
        ),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(task.id)

    reply = next(call[3] for call in gitea.calls if call[0] == "reply_comment")
    assert "待确认" in reply
    assert "NEEDS_HUMAN_CONFIRMATION" in reply
    assert "已完成" not in reply
    with session_factory() as db:
        stored = db.get(ReviewTask, task.id)
        revision = db.get(SpecVersion, stored.new_spec_version_id)
        assert revision.status == SpecStatus.REWORK.value
        assert db.query(ClarificationRequest).filter_by(
            spec_version_id=revision.id
        ).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "verdict", "expected_action", "expected_review"),
    [
        (
            RewriteAction.NOT_ACCEPTED,
            ReviewVerdict.REJECT,
            "未采纳",
            "未通过自动审核，需返工",
        ),
        (
            RewriteAction.MODIFIED,
            ReviewVerdict.NEED_INFO,
            "已修改",
            "未通过自动审核，需返工",
        ),
    ],
)
async def test_replies_report_nonacceptance_and_blocking_review_outcomes(
    session_factory,
    db_session,
    complete_brief,
    valid_spec,
    action,
    verdict,
    expected_action,
    expected_review,
):
    """Generic success text would conceal rejection or a blocked automatic review."""
    _seed_review(db_session, complete_brief, valid_spec)
    gitea = FakeGitea([_thread(101, "Clarify the requested policy")])
    agent = ScriptedAgentGateway(
        rewrite_results=deque(
            [
                _rewrite(
                    valid_spec,
                    _response(101, action, "Recorded the PM decision truthfully."),
                )
            ]
        ),
        review_results=deque([SemanticReview(verdict=verdict, findings=[])]),
    )
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)
    task = await coordinator.create_or_resume("root-1", "owner")

    await coordinator.run(task.id)

    reply = next(call[3] for call in gitea.calls if call[0] == "reply_comment")
    assert expected_action in reply
    assert expected_review in reply
    assert "已完成" not in reply


def test_interrupted_recovery_is_local_only_and_public_errors_are_stable(
    session_factory, db_session, complete_brief, valid_spec
):
    """Startup recovery must neither leak diagnostics nor repeat external work."""
    _seed_review(db_session, complete_brief, valid_spec)
    rows = [
        ReviewTask(
            id=f"task-{status}",
            wi="root-1",
            initiator_actor_id="owner",
            status=status,
            base_version=1,
            base_commit_sha=BASE_SHA,
            comment_ids=[100 + index],
            comment_snapshot=[],
            comment_snapshot_hash=str(index) * 64,
            reply_receipts={},
            error_code="OLD_ERROR" if status == "error" else None,
            error="existing private error" if status == "error" else None,
        )
        for index, status in enumerate(("pending", "processing", "done", "error"), 1)
    ]
    db_session.add_all(rows)
    db_session.commit()
    gitea = FakeGitea()
    agent = ScriptedAgentGateway()
    coordinator = ReviewPublishCoordinator(session_factory, gitea, agent)

    changed = coordinator.mark_interrupted_tasks()

    assert changed == 2
    assert gitea.calls == []
    assert agent.calls == []
    assert coordinator.task("task-pending").error == (
        "Review publication was interrupted. Submit it again to retry."
    )
    with session_factory() as db:
        assert db.get(ReviewTask, "task-pending").error_code == "PROCESS_INTERRUPTED"
        assert db.get(ReviewTask, "task-processing").error_code == "PROCESS_INTERRUPTED"
        assert db.get(ReviewTask, "task-done").status == "done"
        assert db.get(ReviewTask, "task-error").error_code == "OLD_ERROR"


def test_retry_settles_orphaned_review_agent_call(
    session_factory, db_session, complete_brief, valid_spec
):
    """A failed publisher retry must not remain blocked by its dead model call."""

    _seed_review(db_session, complete_brief, valid_spec)
    task = ReviewTask(
        id="task-interrupted-agent",
        wi="root-1",
        initiator_actor_id="owner",
        status="error",
        base_version=1,
        base_commit_sha=BASE_SHA,
        comment_ids=[101],
        comment_snapshot=[],
        comment_snapshot_hash="a" * 64,
        reply_receipts={},
        error_code="PROCESS_INTERRUPTED",
        error="review publication process was interrupted",
    )
    command_id = f"publish-review:{task.id}"
    attempt = CommandAttempt(
        id="attempt-interrupted-agent",
        project_id="publish-project",
        session_id="publish-session",
        command_id=command_id,
        input_hash="b" * 64,
        expected_state_version=4,
        action=CommandAction.PUBLISH_REVIEW.value,
        status="PREPARING",
        agent_call_ids=["call-interrupted-review"],
    )
    call = AgentCall(
        id="call-interrupted-review",
        project_id="publish-project",
        agent_session_id="publish-reviewer-session",
        operation="review_spec",
        request={"command_id": command_id, "input_hash": attempt.input_hash},
        status="PENDING",
    )
    db_session.add_all([task, attempt, call])
    db_session.commit()

    with session_factory() as db:
        with db.begin():
            ReviewPublishCoordinator._reset_error_task(db, task.id)

    with session_factory() as db:
        recovered_task = db.get(ReviewTask, task.id)
        recovered_call = db.get(AgentCall, call.id)
        assert recovered_task.status == "pending"
        assert recovered_call.status == "FAILED"
        assert recovered_call.response == {"error_code": "PROCESS_INTERRUPTED"}
        assert recovered_call.completed_at is not None
