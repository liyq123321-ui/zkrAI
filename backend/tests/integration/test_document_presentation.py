"""Draft team / external PPT endpoints for an unfrozen PRD.

These pin the wiring from the draft presentation router to the
block/asset/presentation services without invoking a real Agent.
"""

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.document_presentations import build_router
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


class StubPresentationService:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def generate_draft_payload(
        self, project_id, *, document_id, revision, markdown, audience, actor_id=None
    ):
        self.calls.append((project_id, document_id, revision, markdown, audience))
        if self.error is not None:
            raise self.error
        return self.result


def _client(blocks, assets, presentations):
    app = FastAPI()

    @app.exception_handler(BlockError)
    async def _block_error(_, error):
        return JSONResponse(status_code=error.status, content={"detail": str(error)})

    app.include_router(build_router(blocks, assets, presentations, StubActors()))
    return TestClient(app)


def test_missing_draft_presentation_reports_status():
    response = _client(StubBlockService(), StubAssetService(), StubPresentationService()).get(
        "/prd-documents/doc-1/presentations/team", params={"actor_id": "owner-1"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "document_id": "doc-1",
        "revision": 3,
        "audience": "team",
        "status": "missing",
    }


def test_generate_rejects_empty_draft():
    response = _client(
        StubBlockService(markdown="   "), StubAssetService(), StubPresentationService()
    ).post("/prd-documents/doc-1/presentations/external", json={"actor_id": "owner-1"})
    assert response.status_code == 422


def test_generate_persists_and_serves_pptx():
    result = SimpleNamespace(title="草稿 PPT", pptx=b"PK-pptx-bytes", preview_html="<html>preview</html>")
    presentations = StubPresentationService(result)
    assets = StubAssetService()
    client = _client(StubBlockService(markdown="# 标题\n\n正文"), assets, presentations)

    response = client.post(
        "/prd-documents/doc-1/presentations/team", json={"actor_id": "owner-1"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["title"] == "草稿 PPT"
    assert body["content_url"] == "/prd-documents/doc-1/presentations/team/file"
    assert body["preview_url"] == "/prd-documents/doc-1/presentations/team/preview"
    assert presentations.calls == [("project-1", "doc-1", 3, "# 标题\n\n正文", "team")]

    # The artifact is bound to the document revision and re-served without regenerating.
    status = client.get("/prd-documents/doc-1/presentations/team", params={"actor_id": "owner-1"})
    assert status.json()["status"] == "ready"
    download = client.get("/prd-documents/doc-1/presentations/team/file", params={"actor_id": "owner-1"})
    assert download.status_code == 200
    assert download.content == b"PK-pptx-bytes"
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    preview = client.get("/prd-documents/doc-1/presentations/team/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("text/html")
    assert preview.text == "<html>preview</html>"


def test_audiences_are_stored_separately():
    assets = StubAssetService()
    client = _client(
        StubBlockService(), assets, StubPresentationService(SimpleNamespace(title="t", pptx=b"x", preview_html="<html></html>"))
    )
    client.post("/prd-documents/doc-1/presentations/team", json={"actor_id": "owner-1"})
    assert (
        client.get("/prd-documents/doc-1/presentations/external", params={"actor_id": "owner-1"})
        .json()["status"]
        == "missing"
    )


def test_generate_reports_failure_when_agent_errors():
    response = _client(
        StubBlockService(),
        StubAssetService(),
        StubPresentationService(error=RuntimeError("boom")),
    ).post("/prd-documents/doc-1/presentations/team", json={"actor_id": "owner-1"})
    assert response.status_code == 502


def test_download_missing_before_generation():
    response = _client(
        StubBlockService(), StubAssetService(), StubPresentationService()
    ).get("/prd-documents/doc-1/presentations/team/file", params={"actor_id": "owner-1"})
    assert response.status_code == 404


def test_preview_missing_before_generation():
    response = _client(
        StubBlockService(), StubAssetService(), StubPresentationService()
    ).get("/prd-documents/doc-1/presentations/team/preview")
    assert response.status_code == 404
