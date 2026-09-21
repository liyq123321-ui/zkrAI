"""Freezing a PRD (「进入下一阶段」) must start HTML prototype generation.

The prototype itself is produced by :class:`PrdPrototypeService`; these tests
pin the wiring from the block-editor submit endpoint to that existing service,
without invoking an Agent.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.documents import build_router


class StubActors:
    def resolve(self, request, supplied_actor_id):
        return supplied_actor_id or "owner-1"


class StubDocuments:
    def __init__(self, result=None):
        self.result = result or {
            "spec_version_id": "spec-1",
            "project_id": "project-1",
        }
        self.submitted = []

    def submit(self, document_id, actor, expected_revision):
        self.submitted.append((document_id, actor, expected_revision))
        return self.result


class StubPrototypes:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def automatic_generation_allowed(self, project_id):
        self.calls.append(("allowed", project_id))
        return self.allowed

    async def ensure_for_project(self, project_id):
        self.calls.append(("ensure", project_id))


def _client(documents, prototypes):
    app = FastAPI()
    app.include_router(
        build_router(documents, None, StubActors(), prototypes=prototypes)
    )
    return TestClient(app)


def test_submit_returns_project_id_and_starts_generation():
    documents = StubDocuments()
    prototypes = StubPrototypes()

    response = _client(documents, prototypes).post(
        "/prd-documents/doc-1/submit",
        json={"expected_revision": 7, "actor_id": "owner-1"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "spec_version_id": "spec-1",
        "project_id": "project-1",
    }
    assert documents.submitted == [("doc-1", "owner-1", 7)]
    # BackgroundTasks run before TestClient returns the response.
    assert prototypes.calls == [("allowed", "project-1"), ("ensure", "project-1")]


def test_submit_skips_generation_when_review_findings_remain():
    prototypes = StubPrototypes(allowed=False)

    response = _client(StubDocuments(), prototypes).post(
        "/prd-documents/doc-1/submit",
        json={"expected_revision": 7, "actor_id": "owner-1"},
    )

    assert response.status_code == 200
    assert prototypes.calls == [("allowed", "project-1")]


def test_submit_without_prototype_service_still_succeeds():
    response = _client(StubDocuments(), None).post(
        "/prd-documents/doc-1/submit",
        json={"expected_revision": 7, "actor_id": "owner-1"},
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == "project-1"
