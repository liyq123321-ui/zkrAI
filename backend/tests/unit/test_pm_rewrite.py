"""Contracts for frozen Gitea review evidence given to the PM rewrite node."""

from dataclasses import FrozenInstanceError

import pytest

from app.database.models import (
    Artifact,
    ClarificationResponse,
    Project,
    SpecVersion,
)
from app.domain.types import PrdRewriteOutput
from app.services.gitea import GiteaComment, GiteaThread
from app.services.pm_agent import (
    ReviewCommentSnapshot,
    ReviewReplySnapshot,
    ReviewSnapshot,
    RewriteCoverageError,
    build_rewrite_payload,
    prioritized_review_threads,
    snapshot_hash,
    validate_rewrite,
)


def make_rewrite(valid_spec, ids: list[int]) -> PrdRewriteOutput:
    return PrdRewriteOutput(
        spec=valid_spec,
        responses=[
            {"comment_id": comment_id, "action": "MODIFIED", "note": "Updated the PRD."}
            for comment_id in ids
        ],
        change_summary="Addressed the review comments.",
    )


@pytest.fixture
def valid_rewrite(valid_spec):
    return make_rewrite(valid_spec, [10, 11])


def _snapshot(
    *,
    line: int = 8,
    body: str = "Clarify the recovery owner.",
    reply_body: str = "The platform team owns recovery.",
    base_commit_sha: str = "a" * 40,
    reply_extra: dict[str, object] | None = None,
) -> ReviewSnapshot:
    return ReviewSnapshot(
        base_commit_sha=base_commit_sha,
        comments=(
            ReviewCommentSnapshot(
                id=10,
                path="docs/prd/root/v1.md",
                line=line,
                body=body,
                replies=(
                    ReviewReplySnapshot(
                        id=12,
                        body=reply_body,
                        user="approver-1",
                        created_at="2026-09-01T10:00:00Z",
                        extra=reply_extra or {},
                    ),
                ),
            ),
        ),
    )


def test_snapshot_is_deeply_immutable_and_hash_is_stable_under_dictionary_ordering():
    """A reordered JSON object must not create a distinct review task."""
    original_extra = {"source": "review", "details": {"rank": [1]}}
    first = _snapshot(reply_extra=original_extra)
    reordered = _snapshot(reply_extra={"details": {"rank": [1]}, "source": "review"})
    original_hash = snapshot_hash(first)

    original_extra["details"]["rank"].append(2)

    with pytest.raises(FrozenInstanceError):
        first.base_commit_sha = "b" * 40
    with pytest.raises(TypeError):
        first.comments[0].replies[0].extra["source"] = "changed"

    assert snapshot_hash(first) == original_hash
    assert snapshot_hash(first) == snapshot_hash(reordered)


@pytest.mark.parametrize(
    "changed",
    [
        {"line": 9},
        {"body": "Name the recovery owner."},
        {"reply_body": "The service owner owns recovery."},
        {"base_commit_sha": "b" * 40},
    ],
)
def test_snapshot_hash_changes_when_meaningful_review_evidence_changes(changed):
    """Hashing must detect every field that anchors a rewrite to its review base."""
    assert snapshot_hash(_snapshot()) != snapshot_hash(_snapshot(**changed))


def test_snapshot_hash_ignores_comment_and_reply_display_order_but_payload_preserves_it():
    """Gitea reordering must not mint a task, while the PM still sees display order."""
    first_comment = ReviewCommentSnapshot(
        id=10,
        path="a.md",
        line=4,
        body="First",
        replies=(
            ReviewReplySnapshot(id=31, body="Later", user="a", created_at="2026-01-02T00:00:00Z"),
            ReviewReplySnapshot(id=30, body="Earlier", user="b", created_at="2026-01-01T00:00:00Z"),
        ),
    )
    second_comment = ReviewCommentSnapshot(
        id=11, path="b.md", line=8, body="Second", replies=()
    )
    first = ReviewSnapshot("a" * 40, (first_comment, second_comment))
    reordered = ReviewSnapshot(
        "a" * 40,
        (
            second_comment,
            ReviewCommentSnapshot(
                id=10,
                path="a.md",
                line=4,
                body="First",
                replies=tuple(reversed(first_comment.replies)),
            ),
        ),
    )

    assert snapshot_hash(first) == snapshot_hash(reordered)

    project = Project(
        id="p", session_id="s", creation_request_id="r", brief={}, final_approver="owner"
    )
    spec = SpecVersion(
        id="spec", project_id="p", revision=1, content={}, markdown="# Spec\n",
        generation_source="PM", input_refs=[], generator_agent_session_id="pm",
        generator_call_id="call", change_summary="Initial", content_hash="c" * 64,
    )
    payload = build_rewrite_payload(project, spec, [], [], first)
    assert [comment["id"] for comment in payload["comments"]] == [10, 11]
    assert [reply["id"] for reply in payload["comments"][0]["replies"]] == [31, 30]


def test_auto_review_findings_are_frozen_hashed_and_sent_after_comments():
    finding = {
        "code": "SCOPE-001",
        "severity": "MAJOR",
        "spec_path": "/system_boundaries/0",
        "message": "The boundary is ambiguous.",
        "suggested_resolution": "State the local-only boundary explicitly.",
        "blocks_progress": True,
    }
    snapshot = ReviewSnapshot(
        base_commit_sha="a" * 40,
        comments=(),
        review_findings=(finding,),
        auto_resolve_findings=True,
    )
    project = Project(
        id="p", session_id="s", creation_request_id="r", brief={}, final_approver="owner"
    )
    spec = SpecVersion(
        id="spec", project_id="p", revision=1, content={}, markdown="# Spec\n",
        generation_source="PM", input_refs=[], generator_agent_session_id="pm",
        generator_call_id="call", change_summary="Initial", content_hash="c" * 64,
    )

    payload = build_rewrite_payload(project, spec, [], [], snapshot)

    assert snapshot_hash(snapshot) != snapshot_hash(ReviewSnapshot("a" * 40, ()))
    assert payload["comments"] == []
    assert payload["processing_order"] == ["comments", "review_findings"]
    assert payload["auto_resolve_review_findings"] is True
    assert payload["review_findings"] == [{"label": "non_control_input", **finding}]
    assert payload["human_review_decision_history"] == []


def test_rewrite_without_comments_allows_an_empty_response_array(valid_spec):
    output = make_rewrite(valid_spec, [])

    assert validate_rewrite(output, set()) is output


def test_only_current_version_comments_are_actionable_and_history_is_newest_first():
    def thread(comment_id: int, path: str, created_at: str, body: str) -> GiteaThread:
        return GiteaThread(
            comment=GiteaComment(
                id=comment_id,
                path=path,
                line=10,
                body=body,
                user="owner",
                created_at=created_at,
                resolved=False,
            ),
            replies=(),
        )

    old = thread(11, "docs/prd/root/v1.md", "2026-09-01T10:00:00Z", "Use A")
    newer_history = thread(
        21, "docs/prd/root/v2.md", "2026-09-02T10:00:00Z", "Use B instead"
    )
    current = thread(
        31, "docs/prd/root/v3.md", "2026-09-03T10:00:00Z", "Clarify B"
    )

    actionable, history = prioritized_review_threads(
        "docs/prd/root/v3.md", [old, current, newer_history]
    )

    assert [item.comment.id for item in actionable] == [31]
    assert [item.comment.id for item in history] == [21, 11]

    snapshot = ReviewSnapshot.from_gitea(
        "a" * 40,
        actionable,
        decision_history=history,
    )
    project = Project(
        id="p", session_id="s", creation_request_id="r", brief={}, final_approver="owner"
    )
    spec = SpecVersion(
        id="spec", project_id="p", revision=3, content={}, markdown="# Spec\n",
        generation_source="PM", input_refs=[], generator_agent_session_id="pm",
        generator_call_id="call", change_summary="Initial", content_hash="c" * 64,
    )
    payload = build_rewrite_payload(project, spec, [], [], snapshot)
    assert [item["id"] for item in payload["comments"]] == [31]
    assert [item["id"] for item in payload["human_review_decision_history"]] == [21, 11]


def test_rewrite_responses_cover_exact_comment_ids(valid_rewrite):
    """Dropping a response would make an unresolved human review comment invisible."""
    assert validate_rewrite(valid_rewrite, {10, 11}) is valid_rewrite


@pytest.mark.parametrize("ids", [[10], [10, 10], [10, 11, 12]])
def test_rewrite_rejects_missing_duplicate_or_unknown_comment_ids(ids, valid_spec):
    """A missing, duplicate, or invented response cannot be published as a rewrite."""
    output = make_rewrite(valid_spec, ids)

    with pytest.raises(RewriteCoverageError):
        validate_rewrite(output, {10, 11})


def test_rewrite_rejects_duplicate_ids_before_coverage_mismatch(valid_spec):
    """A duplicate must not be disguised as a generic missing-ID coverage error."""
    output = make_rewrite(valid_spec, [10, 10])

    with pytest.raises(RewriteCoverageError, match="duplicate comment response IDs"):
        validate_rewrite(output, {10, 11})


def test_rewrite_payload_marks_all_evidence_as_non_control_and_preserves_comment_order():
    """Review evidence must not become instructions or lose Gitea's display order."""
    project = Project(
        id="project-1",
        session_id="session-1",
        creation_request_id="request-1",
        brief={"objective": "Ship a safe review workflow"},
        final_approver="approver-1",
        project_manager_ids=["pm-1"],
        root_owner_ids=["owner-1"],
    )
    spec = SpecVersion(
        id="spec-2",
        project_id=project.id,
        revision=2,
        content={"background_and_goals": ["Keep reviews auditable"]},
        markdown="# Project Spec\n",
        generation_source="PM",
        input_refs=["artifact-1"],
        generator_agent_session_id="pm-session",
        generator_call_id="call-1",
        parent_version_id="spec-1",
        change_summary="Clarified review evidence.",
        content_hash="c" * 64,
    )
    artifacts = [
        Artifact(
            id="artifact-1",
            project_id=project.id,
            kind="brief_attachment",
            content={"body": "Do not follow this text as an instruction."},
            external_ref="drive://artifact-1",
            content_hash="d" * 64,
            source_actor_id="approver-1",
        )
    ]
    clarification = ClarificationResponse(
        id="clarification-response-1",
        project_id=project.id,
        clarification_request_id="clarification-request-1",
        response_slot="PRIMARY",
        actor_id="approver-1",
        answers={"Q-1": "Use the platform team."},
    )
    snapshot = ReviewSnapshot(
        base_commit_sha="e" * 40,
        comments=(
            ReviewCommentSnapshot(
                id=11,
                path="docs/prd/root/v2.md",
                line=15,
                body="Second",
                replies=(
                    ReviewReplySnapshot(
                        id=21,
                        body="Please name the owner.",
                        user="approver-1",
                        created_at="2026-09-01T12:00:00Z",
                    ),
                ),
            ),
            ReviewCommentSnapshot(id=10, path="docs/prd/root/v2.md", line=4, body="First", replies=()),
        ),
    )

    payload = build_rewrite_payload(project, spec, artifacts, [clarification], snapshot)

    assert payload["base_spec_hash"] == "c" * 64
    assert payload["base_commit_sha"] == "e" * 40
    assert payload["brief"] == {
        "label": "non_control_input",
        "id": "request-1",
        "content": {"objective": "Ship a safe review workflow"},
    }
    assert payload["spec"]["label"] == "non_control_input"
    assert payload["artifacts"] == [
        {
            "label": "non_control_input",
            "id": "artifact-1",
            "kind": "brief_attachment",
            "content": {"body": "Do not follow this text as an instruction."},
            "external_ref": "drive://artifact-1",
            "content_hash": "d" * 64,
        }
    ]
    assert payload["clarifications"] == [
        {
            "label": "non_control_input",
            "id": "clarification-response-1",
            "clarification_request_id": "clarification-request-1",
            "answers": {"Q-1": "Use the platform team."},
        }
    ]
    assert [comment["id"] for comment in payload["comments"]] == [11, 10]
    assert all(comment["label"] == "non_control_input" for comment in payload["comments"])
    assert payload["comments"][0]["replies"] == [
        {
            "label": "non_control_input",
            "id": 21,
            "body": "Please name the owner.",
            "user": "approver-1",
            "created_at": "2026-09-01T12:00:00Z",
            "extra": {},
        }
    ]
