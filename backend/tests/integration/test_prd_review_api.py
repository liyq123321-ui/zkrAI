"""HTTP contracts for the Gitea-backed PRD review surface."""

import hashlib
from collections import deque

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from app.database.models import Project, ReviewTask, SpecVersion, WorkItem
from app.domain.types import (
    PrdRewriteOutput,
    ProjectPhase,
    RewriteAction,
    SpecStatus,
    WorkItemKind,
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
from tests.helpers.fake_agent import ScriptedAgentGateway
from main import create_app


class FakeGitea:
    """In-memory Gitea boundary for HTTP tests; no real network is available."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], GiteaFile] = {}
        self.threads: dict[int, list[GiteaThread]] = {}
        self.calls: list[tuple[object, ...]] = []
        self.error: Exception | None = None
        self.branch_sha = "base-commit"
        self.put_count = 0
        self.close_calls = 0
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
        return GiteaCommit(self.branch_sha)

    async def get_file(self, path: str, ref: str) -> GiteaFile:
        self.calls.append(("get_file", path, ref))
        if self.error is not None:
            raise self.error
        try:
            return self.files[(path, ref)]
        except KeyError as error:
            raise GiteaError("GITEA_NOT_FOUND", "missing fake file", False) from error

    async def put_file(
        self,
        path: str,
        content: str,
        branch: str,
        message: str,
        sha: str | None = None,
    ) -> GiteaCommit:
        self.calls.append(("put_file", path, content, branch, message, sha))
        self.put_count += 1
        commit = GiteaCommit(
            "created-commit" if self.put_count == 1 else f"updated-commit-{self.put_count}"
        )
        self.branch_sha = commit.sha
        file = GiteaFile(path, content, "created-blob")
        self.files[(path, branch)] = file
        self.files[(path, commit.sha)] = file
        return commit

    async def ensure_review_pr(self, wi: str, head: str) -> GiteaPullRequest:
        self.calls.append(("ensure_review_pr", wi, head))
        return GiteaPullRequest(number=17, head=head, base="main")

    async def list_comment_threads(self, pr_number: int) -> list[GiteaThread]:
        self.calls.append(("list_comment_threads", pr_number))
        return list(self.threads.get(pr_number, []))

    async def create_comment(
        self, pr_number: int, path: str, line: int, body: str, commit_sha: str
    ) -> None:
        self.calls.append(("create_comment", pr_number, path, line, body, commit_sha))

    async def read_pr_refs(self, pr_number: int):
        return ("base-sha", self.branch_sha)

    async def file_diff(self, pr_number: int, path: str):
        return "@@ -0,0 +1 @@\n+# PRD\n"

    async def commentable_lines(self, pr_number: int, path: str):
        self.calls.append(("commentable_lines", pr_number, path))
        return [(1, "addition", "# PRD"), (3, "context", "A first version.")]

    async def reply_comment(self, pr_number: int, comment_id: int, body: str) -> GiteaReply:
        self.calls.append(("reply_comment", pr_number, comment_id, body))
        return GiteaReply(
            id=9000 + len(self.calls), body=body, user="firstflight",
            created_at="2026-09-01T09:00:00Z", user_id=99,
        )

    async def set_comment_resolved(self, comment_id: int, resolved: bool) -> None:
        self.calls.append(("set_comment_resolved", comment_id, resolved))

    async def aclose(self) -> None:
        self.close_calls += 1


def _review_project(db_session):
    markdown = "# PRD\n\nA first version.\n"
    project = Project(
        id="project-prd-api",
        session_id="session-prd-api",
        creation_request_id="request-prd-api",
        brief={"title": "PRD"},
        final_approver="approver-1",
        project_manager_ids=["pm-1"],
        root_owner_ids=["owner-1"],
        phase=ProjectPhase.REVIEW.value,
        state_version=2,
        current_spec_version_id="spec-prd-api-1",
    )
    spec = SpecVersion(
        id="spec-prd-api-1",
        project_id=project.id,
        revision=1,
        content={"background_and_goals": ["A first version."]},
        markdown=markdown,
        generation_source="PM_AGENT",
        input_refs=["artifact:request-prd-api"],
        generator_agent_session_id="pm-session",
        generator_call_id="pm-call",
        parent_version_id=None,
        change_summary="Initial specification",
        content_hash=hashlib.sha256(markdown.encode()).hexdigest(),
        status=SpecStatus.HUMAN_REVIEW.value,
    )
    root = WorkItem(
        id="root-prd-api",
        project_id=project.id,
        local_key="root",
        kind=WorkItemKind.ROOT.value,
        parent_id=None,
        executable=False,
    )
    db_session.add_all([project, spec, root])
    db_session.commit()
    return root


@pytest.fixture
def prd_api_client(
    session_factory,
    db_session,
    test_settings,
    valid_spec,
    passing_semantic_review,
):
    root = _review_project(db_session)
    gitea = FakeGitea()
    revised = valid_spec.model_copy(
        update={
            "main_flows": [
                "Submit, review, and publish the revised project specification"
            ]
        }
    )
    agent = ScriptedAgentGateway(
        rewrite_results=deque(
            [
                PrdRewriteOutput(
                    spec=revised,
                    responses=[
                        {
                            "comment_id": 20,
                            "action": RewriteAction.MODIFIED,
                            "note": "Named the responsible owner.",
                        },
                        {
                            "comment_id": 21,
                            "action": RewriteAction.MODIFIED,
                            "note": "Documented the fallback.",
                        },
                    ],
                    change_summary="Applied both review comments.",
                )
            ]
        ),
        review_results=deque([passing_semantic_review]),
    )
    gitea.agent = agent
    with TestClient(
        create_app(test_settings, agent, session_factory, gitea_client=gitea)
    ) as client:
        yield client, root, gitea


def _thread(comment_id: int, line: int, body: str) -> GiteaThread:
    return GiteaThread(
        comment=GiteaComment(
            id=comment_id,
            path="docs/prd/root-prd-api/v1.md",
            line=line,
            body=body,
            user="reviewer",
            created_at=f"2026-09-01T09:00:{comment_id:02d}Z",
            resolved=False,
        ),
        replies=(),
    )


def test_document_endpoints_return_only_the_prd_document_contract(prd_api_client):
    """Dropping a route or leaking a binding field would break PRD readers."""
    client, root, _ = prd_api_client

    latest = client.get(f"/prd/{root.id}")
    specific = client.get(f"/prd/{root.id}/v/1")
    versions = client.get(f"/prd/{root.id}/versions")

    expected_fields = {
        "wi",
        "version",
        "filename",
        "pr_number",
        "commit_sha",
        "content",
        "change_summary",
    }
    assert latest.status_code == 200
    assert latest.json()["content"] == "# PRD\n\nA first version.\n"
    assert set(latest.json()) == expected_fields
    assert specific.status_code == 200
    assert specific.json() == latest.json()
    assert versions.status_code == 200
    assert versions.json() == [latest.json()]


def test_commentable_lines_endpoint_uses_the_authoritative_pr_diff(prd_api_client):
    client, root, gitea = prd_api_client

    response = client.get(f"/prd/{root.id}/commentable-lines")

    assert response.status_code == 200
    assert response.json() == {
        "wi": root.id,
        "version": 1,
        "filename": f"docs/prd/{root.id}/v1.md",
        "commit_sha": "created-commit",
        "lines": [
            {"line": 1, "kind": "addition", "text": "# PRD"},
            {"line": 3, "kind": "context", "text": "A first version."},
        ],
    }
    assert ("commentable_lines", 17, f"docs/prd/{root.id}/v1.md") in gitea.calls


def test_comment_endpoints_expose_threads_and_apply_authorized_mutations(prd_api_client):
    """A wrong route or payload would make comments unavailable or mutate another target."""
    client, root, gitea = prd_api_client
    gitea.threads[17] = [
        GiteaThread(
            comment=GiteaComment(
                id=10,
                path="docs/prd/root-prd-api/v1.md",
                line=3,
                body="Name the owner.",
                user="reviewer",
                created_at="2026-09-01T09:00:00Z",
                resolved=False,
            ),
            replies=(
                GiteaReply(
                    id=11,
                    body="Will update.",
                    user="owner-1",
                    created_at="2026-09-01T09:01:00Z",
                ),
            ),
        )
    ]

    comments = client.get(f"/prd/{root.id}/comments")
    created = client.post(
        f"/prd/{root.id}/comments",
        json={"actor_id": "owner-1", "line": 3, "text": "Assign an owner."},
    )
    replied = client.post(
        f"/prd/{root.id}/comments/10/reply",
        json={"actor_id": "owner-1", "author_type": "human", "text": "Assigned."},
    )
    resolved = client.post(
        f"/prd/{root.id}/comments/10/resolve",
        json={"actor_id": "owner-1", "resolved": True},
    )

    assert comments.status_code == 200
    assert comments.json() == [
        {
            "id": 10,
            "path": "docs/prd/root-prd-api/v1.md",
            "line": 3,
            "author_type": "human",
            "body": "Name the owner.",
            "resolved": False,
            "replies": [{"id": 11, "author_type": "human", "body": "Will update."}],
        }
    ]
    assert created.status_code == 204
    assert replied.status_code == 204
    assert resolved.status_code == 204
    assert created.content == b""
    assert replied.content == b""
    assert resolved.content == b""
    assert ("create_comment", 17, "docs/prd/root-prd-api/v1.md", 3, "Assign an owner.", "created-commit") in gitea.calls
    assert ("reply_comment", 17, 10, "Assigned.") in gitea.calls
    assert ("set_comment_resolved", 10, True) in gitea.calls


def test_comment_mutations_map_forbidden_and_cross_project_stably(prd_api_client):
    """Authorization and ownership failures must not leak service failures."""
    client, root, gitea = prd_api_client
    gitea.threads[17] = [
        GiteaThread(
            comment=GiteaComment(
                id=99,
                path="docs/prd/other-root/v1.md",
                line=1,
                body="Foreign thread.",
                user="reviewer",
                created_at="2026-09-01T09:00:00Z",
                resolved=False,
            ),
            replies=(),
        )
    ]

    forbidden = client.post(
        f"/prd/{root.id}/comments",
        json={"actor_id": "outsider", "line": 3, "text": "No authority."},
    )
    foreign = client.post(
        f"/prd/{root.id}/comments/99/resolve",
        json={"actor_id": "owner-1", "resolved": True},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "PRD_FORBIDDEN"
    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "PRD_NOT_FOUND"
    assert "outsider" not in forbidden.text
    assert not any(call[0] == "set_comment_resolved" for call in gitea.calls)


@pytest.mark.parametrize(
    ("payload", "rejected_field"),
    [
        (
            {"actor_id": "owner-1", "line": 0, "text": "A valid comment."},
            "line",
        ),
        (
            {"actor_id": "owner-1", "line": 3, "text": "   "},
            "text",
        ),
    ],
)
def test_create_comment_rejects_each_invalid_field_independently(
    prd_api_client, payload, rejected_field
):
    """Removing either request validator would let an invalid review comment through."""
    client, root, gitea = prd_api_client

    response = client.post(f"/prd/{root.id}/comments", json=payload)

    assert response.status_code == 422, rejected_field
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert not any(call[0] == "create_comment" for call in gitea.calls)


def test_gitea_failure_is_a_safe_service_unavailable_response(prd_api_client):
    """Returning an upstream body or token would expose repository credentials."""
    client, root, gitea = prd_api_client
    gitea.error = GiteaError(
        "GITEA_UNAVAILABLE", "token secret; upstream body must stay private", True
    )

    response = client.get(f"/prd/{root.id}")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "GITEA_UNAVAILABLE",
        "message": "The PRD review service is temporarily unavailable.",
    }
    assert "secret" not in response.text
    assert "upstream" not in response.text


def test_publish_returns_accepted_contract_and_background_task_is_pollable(
    prd_api_client,
):
    """Dropping scheduling or returning before task creation would make polling unusable."""
    client, root, gitea = prd_api_client
    gitea.threads[17] = [
        _thread(20, 2, "Name the responsible owner."),
        _thread(21, 3, "Document the failure fallback."),
    ]

    response = client.post(
        f"/prd/{root.id}/reviews/publish", json={"actor_id": "approver-1"}
    )

    assert response.status_code == 202
    assert response.json() == {
        "task_id": response.json()["task_id"],
        "base_version": 1,
        "comment_count": 2,
    }
    task = client.get(f"/tasks/{response.json()['task_id']}")
    assert task.status_code == 200
    assert task.json() == {
        "task_id": response.json()["task_id"],
        "wi": root.id,
        "status": "done",
        "base_version": 1,
        "new_version": 2,
        "new_commit_sha": "updated-commit-2",
        "error": None,
    }
    assert [operation for operation, _ in gitea.agent.calls].count("rewrite_prd") == 1
    assert [call[2] for call in gitea.calls if call[0] == "reply_comment"] == [
        20,
        21,
    ]


def test_task_polling_returns_stable_not_found_without_external_reads(prd_api_client):
    """Treating task reads like publication could contact Gitea or expose internals."""
    client, _, gitea = prd_api_client

    response = client.get("/tasks/unknown-task")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "PRD_NOT_FOUND",
        "message": "The requested PRD review resource was not found.",
    }
    assert gitea.calls == []


def test_publish_rejects_empty_reviews_before_scheduling_agent_work(prd_api_client):
    """An empty frozen snapshot must not create a task or invoke the PM Agent."""
    client, root, gitea = prd_api_client

    response = client.post(
        f"/prd/{root.id}/reviews/publish", json={"actor_id": "approver-1"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "NO_UNRESOLVED_COMMENTS",
        "message": "The PRD review has no unresolved comments to publish.",
    }
    assert gitea.agent.calls == []


def test_publish_rejects_unauthorized_actor_before_any_external_call(prd_api_client):
    """Skipping publication authorization would freeze and process outsider evidence."""
    client, root, gitea = prd_api_client

    response = client.post(
        f"/prd/{root.id}/reviews/publish", json={"actor_id": "outsider"}
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PRD_FORBIDDEN"
    assert gitea.calls == []
    assert gitea.agent.calls == []


def test_publish_redacts_unexpected_internal_failure(prd_api_client):
    """An unexpected publication exception must not reveal credentials or diagnostics."""
    client, root, gitea = prd_api_client
    gitea.error = RuntimeError("token secret; private stack and upstream body")

    response = client.post(
        f"/prd/{root.id}/reviews/publish", json={"actor_id": "approver-1"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "PRD_REVIEW_UNAVAILABLE",
        "message": "The PRD review service is temporarily unavailable.",
    }
    assert "secret" not in response.text
    assert "private" not in response.text
    assert "upstream" not in response.text


def test_lifespan_marks_interrupted_tasks_without_external_recovery(
    session_factory,
    db_session,
    test_settings,
):
    """Omitting local startup recovery would leave abandoned work permanently pending."""
    root = _review_project(db_session)
    db_session.add(
        ReviewTask(
            id="interrupted-http-task",
            wi=root.id,
            initiator_actor_id="owner-1",
            status="pending",
            base_version=1,
            base_commit_sha="base-commit",
            comment_ids=[20],
            comment_snapshot=[],
            comment_snapshot_hash="a" * 64,
            reply_receipts={},
        )
    )
    db_session.commit()
    gitea = FakeGitea()
    agent = ScriptedAgentGateway()

    with TestClient(
        create_app(test_settings, agent, session_factory, gitea_client=gitea)
    ):
        pass

    with session_factory() as db:
        task = db.get(ReviewTask, "interrupted-http-task")
        assert task.status == "error"
        assert task.error_code == "PROCESS_INTERRUPTED"
    assert gitea.calls == []
    assert agent.calls == []


def test_lifespan_closes_only_the_gitea_client_owned_by_the_app(
    session_factory,
    test_settings,
    monkeypatch,
):
    """Leaking the default AsyncClient or closing an injected one breaks ownership."""
    real_client_type = main.GiteaClient
    owned_clients = []

    def build_owned_client(settings):
        client = real_client_type(
            settings,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, request=request)
            ),
        )
        owned_clients.append(client)
        return client

    monkeypatch.setattr(main, "GiteaClient", build_owned_client)
    with TestClient(
        main.create_app(
            test_settings,
            ScriptedAgentGateway(),
            session_factory,
        )
    ):
        pass

    class FalseyInjectedGitea(FakeGitea):
        def __bool__(self) -> bool:
            return False

    injected = FalseyInjectedGitea()
    with TestClient(
        main.create_app(
            test_settings,
            ScriptedAgentGateway(),
            session_factory,
            gitea_client=injected,
        )
    ):
        pass

    assert len(owned_clients) == 1
    assert owned_clients[0]._client.is_closed is True
    assert injected.close_calls == 0


def test_lifespan_closes_owned_gitea_when_database_startup_fails(
    test_settings,
    monkeypatch,
):
    """A database startup exception must not leak the app-owned AsyncClient."""
    real_client_type = main.GiteaClient
    owned_clients = []

    def build_owned_client(settings):
        client = real_client_type(
            settings,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, request=request)
            ),
        )
        owned_clients.append(client)
        return client

    def broken_session_factory():
        raise RuntimeError("database startup failed")

    monkeypatch.setattr(main, "GiteaClient", build_owned_client)
    app = main.create_app(
        test_settings,
        ScriptedAgentGateway(),
        broken_session_factory,
    )

    with pytest.raises(RuntimeError, match="database startup failed"):
        with TestClient(app):
            pass

    assert len(owned_clients) == 1
    assert owned_clients[0]._client.is_closed is True


def test_prd_diff_returns_deletions_and_context_separately_from_commentable_lines(prd_api_client):
    client, root, gitea = prd_api_client

    async def file_diff(pr_number, path):
        assert pr_number == 17
        assert path == f'docs/prd/{root.id}/v1.md'
        return '@@ -1,2 +1,2 @@\n keep\n-old requirement\n+new requirement\n'

    gitea.file_diff = file_diff
    response = client.get(f'/prd/{root.id}/diff')
    assert response.status_code == 200
    assert response.json() == {
        'wi':root.id,'version':1,'filename':f'docs/prd/{root.id}/v1.md',
        'commit_sha':'created-commit','patch':'@@ -1,2 +1,2 @@\n keep\n-old requirement\n+new requirement\n',
    }


def test_prd_diff_rejects_live_target_content_different_from_bound_document(prd_api_client):
    client, root, gitea = prd_api_client
    document = client.get(f'/prd/{root.id}').json()
    gitea.branch_sha = 'external-head'
    gitea.files[(document['filename'], 'external-head')] = GiteaFile(document['filename'], '# changed outside the app', 'other-blob')

    response = client.get(f'/prd/{root.id}/diff')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'PRD_CONTENT_CONFLICT'


def test_prd_diff_rejects_a_pr_revision_changed_while_diff_is_loading(prd_api_client):
    client, root, gitea = prd_api_client
    client.get(f'/prd/{root.id}').raise_for_status()
    refs = iter([('base-sha',gitea.branch_sha),('new-base-sha',gitea.branch_sha)])

    async def read_pr_refs(pr_number):
        return next(refs)

    gitea.read_pr_refs = read_pr_refs
    response = client.get(f'/prd/{root.id}/diff')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'PRD_CONTENT_CONFLICT'
