"""Public PRD review and PM rewrite boundary contracts."""

import pytest
from pydantic import ValidationError

from app.domain.types import PrdRewriteOutput
from app.schemas.prd_review import (
    CommentCreateRequest,
    CommentReplyRequest,
    CommentResolveRequest,
    PrdCommentRead,
    PrdDocumentRead,
    PublishReviewRequest,
    ReviewTaskRead,
)


def test_rewrite_output_forbids_unknown_fields_and_invalid_actions(valid_spec):
    """An unrecognised PM action must not reach the rewrite coordinator."""
    with pytest.raises(ValidationError):
        PrdRewriteOutput.model_validate(
            {
                "spec": valid_spec.model_dump(),
                "responses": [{"comment_id": 1, "action": "DONE", "note": "x"}],
                "change_summary": "changed",
            }
        )

    with pytest.raises(ValidationError):
        PrdRewriteOutput.model_validate(
            {
                "spec": valid_spec.model_dump(),
                "responses": [
                    {"comment_id": 1, "action": "MODIFIED", "note": "x"}
                ],
                "change_summary": "changed",
                "unexpected": True,
            }
        )


@pytest.mark.parametrize("value", ["", "   ", "x\x00y"])
def test_comment_text_rejects_blank_or_nul(value):
    """Blank or control-bearing comments would be unsafe evidence to publish."""
    with pytest.raises(ValidationError):
        CommentCreateRequest(actor_id="reviewer", line=1, text=value)


def test_public_reply_cannot_forge_agent_author_type():
    """Only the server's PM pipeline may author replies as the agent."""
    with pytest.raises(ValidationError):
        CommentReplyRequest(actor_id="reviewer", text="Please clarify this.", author_type="agent")


@pytest.mark.parametrize(
    ("contract", "payload"),
    [
        (
            CommentCreateRequest,
            {"actor_id": "reviewer", "line": 1, "text": "Add an owner."},
        ),
        (
            CommentReplyRequest,
            {
                "actor_id": "reviewer",
                "text": "Please use the platform team.",
                "author_type": "human",
            },
        ),
        (CommentResolveRequest, {"actor_id": "reviewer", "resolved": True}),
        (PublishReviewRequest, {"actor_id": "reviewer"}),
        (
            PrdDocumentRead,
            {
                "wi": "root-1",
                "version": 1,
                "filename": "docs/prd/root-1/v1.md",
                "pr_number": 3,
                "commit_sha": "a" * 40,
                "content": "# PRD",
                "change_summary": "Initial version",
            },
        ),
        (
            PrdCommentRead,
            {
                "id": 1,
                "path": "docs/prd/root-1/v1.md",
                "line": 1,
                "author_type": "human",
                "body": "Clarify ownership.",
                "resolved": False,
                "replies": [],
            },
        ),
        (
            ReviewTaskRead,
            {
                "task_id": "task-1",
                "wi": "root-1",
                "status": "done",
                "base_version": 1,
                "new_version": 2,
                "new_commit_sha": "b" * 40,
                "error": None,
            },
        ),
    ],
)
def test_prd_review_contracts_forbid_unknown_fields(contract, payload):
    """Unexpected API fields must not silently become workflow controls."""
    with pytest.raises(ValidationError):
        contract.model_validate({**payload, "unexpected": True})


def test_text_fields_are_trimmed_before_exposing_contracts():
    """Responses and public requests keep canonical plain-text values."""
    request = CommentCreateRequest(actor_id=" reviewer ", line=1, text="  Add an owner.  ")
    response = PrdCommentRead(
        id=1,
        path=" docs/prd/root-1/v1.md ",
        line=1,
        author_type="human",
        body="  Clarify ownership.  ",
        resolved=False,
        replies=[],
    )

    assert request.actor_id == "reviewer"
    assert request.text == "Add an owner."
    assert response.path == "docs/prd/root-1/v1.md"
    assert response.body == "Clarify ownership."

