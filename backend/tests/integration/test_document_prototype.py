"""Draft HTML prototype endpoints for an unfrozen PRD (「查看原型」 without 进入下一阶段).

These pin the wiring from the draft prototype router to the block/asset/prototype
services without invoking a real Agent.
"""

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.document_prototypes import build_router
from app.services.block_service import BlockError


class StubActors:
    def resolve(self, request, supplied_actor_id):
        return supplied_actor_id or "owner-1"


class StubBlockService:
    def __init__(self, markdown="# 标题\n\n正文"):
        self._markdown = markdown
        self.revision = 3
        self.project_id = "project-1"

    def prototype_source(self, document_id, actor):
        return {
            "document_id": document_id,
            "project_id": self.project_id,
            "revision": self.revision,
            "markdown": self._markdown,
        }

    def document_revision(self, document_id):
        return self.revision

    @contextmanager
    def factory(self):
        yield SimpleNamespace(commit=lambda: None)


class StubAssetService:
    def __init__(self):
        self._artifacts = {}
        self._bytes = {}

    def artifact_for_revision(self, db, document_id, revision, kind):
        return self._artifacts.get((document_id, revision, kind))

    def store_artifact(self, db, *, document_id, revision, actor, file_name, content, kind, mime_type, extension):
        key = (document_id, revision, kind)
        artifact = SimpleNamespace(
            file_name=file_name, size_bytes=len(content), storage_key=key, mime_type=mime_type
        )
        self._artifacts[key] = artifact
        self._bytes[key] = content
        return artifact

    def artifact_bytes(self, artifact):
        return self._bytes[artifact.storage_key]


class StubPrototypeService:
    def __init__(self, payload=None):
        self.payload = payload
        self.calls = []

    async def generate_draft_payload(self, project_id, *, document_id, revision, content_hash, markdown):
        self.calls.append((project_id, document_id, revision, markdown))
        return self.payload


def _client(blocks, assets, prototype):
    app = FastAPI()

    @app.exception_handler(BlockError)
    async def _block_error(_, error):
        return JSONResponse(status_code=error.status, content={"detail": str(error)})

    app.include_router(build_router(blocks, assets, prototype, StubActors()))
    return TestClient(app)


def test_missing_draft_prototype_reports_status():
    response = _client(StubBlockService(), StubAssetService(), StubPrototypeService()).get(
        "/prd-documents/doc-1/prototype", params={"actor_id": "owner-1"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "document_id": "doc-1",
        "revision": 3,
        "status": "missing",
    }


def test_generate_rejects_empty_draft():
    response = _client(
        StubBlockService(markdown="   "), StubAssetService(), StubPrototypeService()
    ).post("/prd-documents/doc-1/prototype", json={"actor_id": "owner-1"})
    assert response.status_code == 422


def test_generate_persists_and_serves_html():
    payload = SimpleNamespace(title="草稿原型", html="<html>hi</html>", generation_summary="ok")
    prototype = StubPrototypeService(payload)
    assets = StubAssetService()
    client = _client(StubBlockService(markdown="# 标题\n\n正文"), assets, prototype)

    response = client.post("/prd-documents/doc-1/prototype", json={"actor_id": "owner-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["title"] == "草稿原型"
    assert body["content_url"] == "/prd-documents/doc-1/prototype/content"
    assert prototype.calls == [("project-1", "doc-1", 3, "# 标题\n\n正文")]

    # The artifact is bound to the document revision and re-served without regenerating.
    status = client.get("/prd-documents/doc-1/prototype", params={"actor_id": "owner-1"})
    assert status.json()["status"] == "ready"
    content = client.get("/prd-documents/doc-1/prototype/content")
    assert content.status_code == 200
    assert content.text == "<html>hi</html>"


def test_generate_reports_failure_when_unavailable():
    response = _client(
        StubBlockService(), StubAssetService(), StubPrototypeService(None)
    ).post("/prd-documents/doc-1/prototype", json={"actor_id": "owner-1"})
    assert response.status_code == 502


def test_content_missing_before_generation():
    response = _client(
        StubBlockService(), StubAssetService(), StubPrototypeService()
    ).get("/prd-documents/doc-1/prototype/content")
    assert response.status_code == 404
