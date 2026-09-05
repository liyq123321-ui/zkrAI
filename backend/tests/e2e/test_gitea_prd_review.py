"""End-to-end proof of Gitea-backed PRD review publication."""

import asyncio
import base64
import hashlib
import json
from collections import deque
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from app.database.database import create_engine_for_url, make_session_factory
from app.database.models import PrdVersion, Project, SpecVersion
from app.domain.types import (
    ClarificationAnalysis,
    PrdRewriteOutput,
    RewriteAction,
    SpecStatus,
)
from app.services.gitea import GiteaClient
from main import create_app
from tests.helpers.fake_agent import ScriptedAgentGateway


class MockGiteaRepository:
    """Stateful Gitea HTTP surface exercised through the real async client."""

    def __init__(self) -> None:
        self.branches = {"main": "a" * 40}
        self.files: dict[tuple[str, str], str] = {}
        self.pull_request: dict[str, object] | None = None
        self.reviews: dict[int, list[dict[str, object]]] = {}
        self.requests: list[tuple[str, str]] = []
        self.commit_count = 0
        self.next_review_id = 201
        self.next_comment_id = 101
        self.next_reply_id = 1001

    @property
    def repository_files(self) -> set[str]:
        return {path for path, _ in self.files}

    @property
    def agent_replies(self) -> list[dict[str, object]]:
        return [
            comment
            for comments in self.reviews.values()
            for comment in comments[1:]
            if comment["user"].get("login") == "firstflight"
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if path == "/api/v1/version" and request.method == "GET":
            return self._response(request, 200, {"version": "1.27.0"})
        if path == "/api/v1/user" and request.method == "GET":
            return self._response(
                request, 200, {"id": 99, "login": "firstflight"}
            )
        if path == "/api/v1/repos/product/requirements" and request.method == "GET":
            return self._response(
                request,
                200,
                {
                    "full_name": "product/requirements",
                    "permissions": {"pull": True, "push": True},
                },
            )
        repo = "/api/v1/repos/product/requirements/"
        if not path.startswith(repo):
            return self._response(request, 404)
        suffix = path.removeprefix(repo)

        if suffix.startswith("branches/") and request.method == "GET":
            branch = suffix.removeprefix("branches/")
            sha = self.branches.get(branch)
            return self._response(
                request,
                200 if sha is not None else 404,
                {"name": branch, "commit": {"id": sha}} if sha is not None else None,
            )
        if suffix == "branches" and request.method == "POST":
            payload = self._body(request)
            branch = str(payload["new_branch_name"])
            sha = self.branches[str(payload["old_ref_name"])]
            self.branches[branch] = sha
            return self._response(
                request, 201, {"name": branch, "commit": {"id": sha}}
            )

        if suffix.startswith("contents/"):
            filename = suffix.removeprefix("contents/")
            if request.method == "PUT":
                payload = self._body(request)
                branch = str(payload["branch"])
                content = base64.b64decode(str(payload["content"])).decode("utf-8")
                self.commit_count += 1
                commit_sha = f"{self.commit_count:040x}"
                self.branches[branch] = commit_sha
                self.files[(filename, commit_sha)] = content
                return self._response(
                    request, 201, {"commit": {"sha": commit_sha}}
                )
            if request.method == "GET":
                ref = request.url.params["ref"]
                commit_sha = self.branches.get(ref, ref)
                content = self.files.get((filename, commit_sha))
                if content is None:
                    return self._response(request, 404)
                return self._response(
                    request,
                    200,
                    {
                        "path": filename,
                        "content": base64.b64encode(content.encode()).decode(),
                        "sha": hashlib.sha256(content.encode()).hexdigest(),
                    },
                )

        if suffix == "pulls" and request.method == "GET":
            return self._response(
                request, 200, [self.pull_request] if self.pull_request else []
            )
        if suffix == "pulls" and request.method == "POST":
            payload = self._body(request)
            self.pull_request = {
                "number": 7,
                "head": {"ref": payload["head"]},
                "base": {"ref": payload["base"]},
            }
            return self._response(request, 201, self.pull_request)
        if suffix == "pulls/7/files" and request.method == "GET":
            filename, content = self._v1_file()
            lines = content.splitlines()
            patch = f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(
                f"+{line}\n" for line in lines
            )
            return self._response(
                request, 200, [{"filename": filename, "patch": patch}]
            )
        if suffix == "pulls/7/reviews" and request.method == "GET":
            return self._response(
                request,
                200,
                [
                    {"id": review_id, "comments_count": len(comments)}
                    for review_id, comments in sorted(self.reviews.items())
                ],
            )
        if suffix == "pulls/7/reviews" and request.method == "POST":
            payload = self._body(request)
            review_id = self.next_review_id
            comment_id = self.next_comment_id
            self.next_review_id += 1
            self.next_comment_id += 1
            submitted = payload["comments"][0]
            self.reviews[review_id] = [
                {
                    "id": comment_id,
                    "body": submitted["body"],
                    "user": {"id": 41, "login": "reviewer"},
                    "path": submitted["path"],
                    "position": submitted["new_position"],
                    "resolver": None,
                    "pull_request_review_id": review_id,
                    "created_at": f"2026-09-02T09:00:{comment_id - 100:02d}Z",
                }
            ]
            return self._response(
                request, 200, {"id": review_id, "state": "COMMENT"}
            )
        if (
            suffix.startswith("pulls/7/reviews/")
            and suffix.endswith("/comments")
            and request.method == "GET"
        ):
            review_id = int(suffix.split("/")[3])
            return self._response(request, 200, self.reviews[review_id])
        if (
            suffix.startswith("pulls/7/comments/")
            and suffix.endswith("/replies")
            and request.method == "POST"
        ):
            comment_id = int(suffix.split("/")[3])
            review_id, root = next(
                (review_id, comments[0])
                for review_id, comments in self.reviews.items()
                if comments[0]["id"] == comment_id
            )
            payload = self._body(request)
            reply = {
                "id": self.next_reply_id,
                "body": payload["body"],
                "user": {"id": 99, "login": "firstflight"},
                "path": root["path"],
                "position": root["position"],
                "resolver": None,
                "pull_request_review_id": review_id,
                "created_at": f"2026-09-02T09:01:{comment_id - 100:02d}Z",
            }
            self.next_reply_id += 1
            self.reviews[review_id].append(reply)
            return self._response(request, 201, reply)

        return self._response(request, 404)

    def _v1_file(self) -> tuple[str, str]:
        matches = [
            (filename, content)
            for (filename, _), content in self.files.items()
            if filename.endswith("/v1.md")
        ]
        assert len(matches) == 1
        return matches[0]

    @staticmethod
    def _body(request: httpx.Request) -> dict[str, object]:
        return json.loads(request.content)

    @staticmethod
    def _response(
        request: httpx.Request,
        status_code: int,
        payload: object | None = None,
    ) -> httpx.Response:
        if payload is None:
            return httpx.Response(status_code, request=request)
        return httpx.Response(status_code, json=payload, request=request)


def test_gitea_review_comments_publish_a_new_human_review_spec_without_child_work(
    request,
    tmp_path,
    test_settings,
    complete_brief,
    valid_spec,
    passing_semantic_review,
):
    """Breaking any real boundary would lose v2, a reply, or the human review stop."""
    database_url = f"sqlite:///{tmp_path / 'gitea-prd-review.sqlite'}"
    settings = replace(
        test_settings,
        database_url=database_url,
        gitea_url="https://gitea.example",
        gitea_token="test-token",
        gitea_owner="product",
        gitea_repo="requirements",
        gitea_base_branch="main",
    )
    engine = create_engine_for_url(database_url)
    session_factory = make_session_factory(engine)
    remote = MockGiteaRepository()
    gitea = GiteaClient(settings, transport=httpx.MockTransport(remote))

    def cleanup() -> None:
        try:
            asyncio.run(gitea.aclose())
        finally:
            engine.dispose()

    request.addfinalizer(cleanup)
    revised = valid_spec.model_copy(
        update={
            "main_flows": [
                "Submit, review, publish, and recover the project specification"
            ]
        }
    )
    agent = ScriptedAgentGateway(
        analyze_results=deque(
            [ClarificationAnalysis(ready_for_spec=True, questions=[], assumptions=[])]
        ),
        generate_results=deque([valid_spec]),
        review_results=deque(
            [passing_semantic_review, passing_semantic_review]
        ),
        rewrite_results=deque(
            [
                PrdRewriteOutput(
                    spec=revised,
                    responses=[
                        {
                            "comment_id": 101,
                            "action": RewriteAction.MODIFIED,
                            "note": "Named the responsible owner.",
                        },
                        {
                            "comment_id": 102,
                            "action": RewriteAction.MODIFIED,
                            "note": "Documented the recovery fallback.",
                        },
                    ],
                    change_summary="Applied both Gitea review comments.",
                )
            ]
        ),
    )
    app = create_app(
        settings,
        agent,
        session_factory,
        gitea_client=gitea,
        auto_bind_prd_review=True,
    )

    with TestClient(app) as client:
        assert remote.requests == []
        assert agent.calls == []
        created_response = client.post(
            "/sessions",
            json={
                "request_id": "gitea-prd-e2e-create",
                "actor_id": "approver-1",
                "brief": complete_brief.model_dump(mode="json"),
            },
        )
        assert created_response.status_code == 201
        created = created_response.json()
        create_spec = client.post(
            f"/sessions/{created['session_id']}/commands",
            json={
                "command_id": "gitea-prd-e2e-spec-v1",
                "action": "create_spec",
                "expected_state_version": created["state_version"],
                "actor_id": "approver-1",
                "payload": {},
            },
        )
        assert create_spec.status_code == 200, create_spec.text
        v1_state = create_spec.json()["state"]
        assert v1_state["current_spec_status"] == "HUMAN_REVIEW"
        root = next(
            item
            for item in client.get(
                f"/sessions/{created['session_id']}/work-items"
            ).json()
            if item["kind"] == "ROOT"
        )

        with session_factory() as db:
            eager_binding = db.query(PrdVersion).filter_by(
                spec_version_id=v1_state["current_spec_version_id"]
            ).one()
            assert eager_binding.wi == root["id"]

        bound_v1 = client.get(f"/prd/{root['id']}")
        assert bound_v1.status_code == 200, bound_v1.text
        assert bound_v1.json()["version"] == 1
        for line, text in (
            (2, "Name the responsible owner."),
            (3, "Document the failure fallback."),
        ):
            comment = client.post(
                f"/prd/{root['id']}/comments",
                json={"actor_id": "approver-1", "line": line, "text": text},
            )
            assert comment.status_code == 204, comment.text

        accepted = client.post(
            f"/prd/{root['id']}/reviews/publish",
            json={"actor_id": "approver-1"},
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["base_version"] == 1
        assert accepted.json()["comment_count"] == 2
        task = client.get(f"/tasks/{accepted.json()['task_id']}")
        assert task.status_code == 200
        assert task.json()["status"] == "done", task.json()
        assert task.json()["new_version"] == 2

        v2 = client.get(f"/prd/{root['id']}/v/2")
        assert v2.status_code == 200, v2.text
        assert v2.json()["content"].startswith("# Project Spec")
        assert "recover the project specification" in v2.json()["content"]
        current = client.get(f"/sessions/{created['session_id']}/state").json()

    with session_factory() as db:
        project = db.get(Project, root["project_id"])
        current_spec = db.get(SpecVersion, project.current_spec_version_id)
        assert current_spec.revision == 2
        assert current_spec.status == SpecStatus.HUMAN_REVIEW.value
    assert current["current_spec_version_id"] == current_spec.id
    assert current["current_spec_status"] == "HUMAN_REVIEW"
    assert len(remote.agent_replies) == 2
    assert all(
        "<!-- firstflight-receipt:v1:" in str(reply["body"])
        and "v2" in str(reply["body"])
        for reply in remote.agent_replies
    )
    assert remote.repository_files == {
        f"docs/prd/{root['id']}/v1.md",
        f"docs/prd/{root['id']}/v2.md",
    }
    assert agent.child_process_calls == 0
