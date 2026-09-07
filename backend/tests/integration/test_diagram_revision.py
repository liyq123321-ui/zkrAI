from app.database.models import AgentCall, AuditEvent, ReviewTask, SpecVersion
from app.domain.types import ProjectSpecPayload
from app.services.spec_service import _render_markdown
from tests.integration.test_prd_review_api import (
    ER_XML_V1,
    _canonical_hash,
    _spec_with_diagram,
    prd_api_client,
)


def _prepare_diagram_review(client, root, session_factory, valid_spec):
    payload = _spec_with_diagram(valid_spec, ER_XML_V1).model_dump(mode="json")
    payload["source_refs"] = ["artifact:request-prd-api"]
    spec = ProjectSpecPayload.model_validate(payload)
    content = spec.model_dump(mode="json")
    markdown = _render_markdown(content)
    with session_factory() as db:
        stored = db.get(SpecVersion, "spec-prd-api-1")
        stored.content = content
        stored.markdown = markdown
        stored.content_hash = _canonical_hash(content)
        db.commit()
    document = client.get(f"/prd/{root.id}")
    assert document.status_code == 200
    return spec, document.json()


def _request(document, xml, **overrides):
    return {
        "actor_id": "owner-1",
        "base_version": document["version"],
        "base_commit_sha": document["commit_sha"],
        "drawio_xml": xml,
        "change_summary": "Adjusted the ER diagram layout and relationship label.",
        **overrides,
    }


def test_diagram_revision_creates_an_immutable_reviewed_version(
    prd_api_client, session_factory, valid_spec
):
    client, root, gitea = prd_api_client
    original, document = _prepare_diagram_review(
        client, root, session_factory, valid_spec
    )
    revised_xml = ER_XML_V1.replace('value="places"', 'value="creates"')

    accepted = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, revised_xml),
    )

    assert accepted.status_code == 202
    assert accepted.json()["no_change"] is False
    task = client.get(f"/tasks/{accepted.json()['task_id']}")
    assert task.status_code == 200
    with session_factory() as db:
        stored_task = db.get(ReviewTask, accepted.json()["task_id"])
        failure_detail = stored_task.error
    assert task.json()["status"] == "done", failure_detail
    assert task.json()["new_version"] == 2
    latest = client.get(f"/prd/{root.id}")
    historical = client.get(f"/prd/{root.id}/v/1")
    assert latest.json()["er_diagrams"][0]["drawio_xml"] == revised_xml
    assert historical.json()["er_diagrams"][0]["drawio_xml"] == ER_XML_V1
    assert not any(call[0] == "reply_comment" for call in gitea.calls)

    replay = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, revised_xml),
    )
    assert replay.status_code == 202
    assert replay.json()["task_id"] == accepted.json()["task_id"]

    with session_factory() as db:
        specs = db.query(SpecVersion).order_by(SpecVersion.revision).all()
        revised = specs[1]
        assert len(specs) == 2
        assert specs[0].content["er_diagrams"][0]["drawio_xml"] == original.er_diagrams[0].drawio_xml
        assert revised.parent_version_id == specs[0].id
        assert revised.generation_source == "HUMAN_DRAWIO_EDIT"
        assert revised.status == "HUMAN_REVIEW"
        edit_call = db.query(AgentCall).filter_by(operation="diagram_edit").one()
        assert edit_call.response == revised.content
        audit = db.query(AuditEvent).filter_by(event_type="ER_DIAGRAM_REVISION_CREATED").one()
        assert audit.actor_id == "owner-1"
        assert "drawio_xml" not in str(audit.payload)


def test_identical_diagram_revision_is_a_durable_no_change(
    prd_api_client, session_factory, valid_spec
):
    client, root, gitea = prd_api_client
    _, document = _prepare_diagram_review(client, root, session_factory, valid_spec)

    accepted = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, f"\n{ER_XML_V1}\r\n"),
    )

    assert accepted.status_code == 202
    assert accepted.json()["no_change"] is True
    task = client.get(f"/tasks/{accepted.json()['task_id']}").json()
    assert task["status"] == "done"
    assert task["new_version"] is None
    with session_factory() as db:
        assert db.query(SpecVersion).count() == 1
        assert db.query(AgentCall).filter_by(operation="diagram_edit").count() == 0
    assert gitea.put_count == 1


def test_diagram_revision_rejects_invalid_stale_and_unauthorized_requests(
    prd_api_client, session_factory, valid_spec
):
    client, root, _ = prd_api_client
    _, document = _prepare_diagram_review(client, root, session_factory, valid_spec)

    invalid = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, "<mxfile><diagram>compressed</diagram></mxfile>"),
    )
    stale = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, ER_XML_V1.replace('value="places"', 'value="owns"'), base_version=9),
    )
    forbidden = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, ER_XML_V1.replace('value="places"', 'value="owns"'), actor_id="outsider"),
    )
    missing = client.post(
        f"/prd/{root.id}/diagrams/missing-model/revisions",
        json=_request(document, ER_XML_V1.replace('value="places"', 'value="owns"')),
    )

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "INVALID_DRAWIO_DIAGRAM"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "PRD_CONTENT_CONFLICT"
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "PRD_FORBIDDEN"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "PRD_NOT_FOUND"
    with session_factory() as db:
        assert db.query(ReviewTask).count() == 0


def test_diagram_revision_retry_reuses_materialized_review_and_task(
    prd_api_client, session_factory, valid_spec
):
    client, root, gitea = prd_api_client
    _, document = _prepare_diagram_review(client, root, session_factory, valid_spec)
    revised_xml = ER_XML_V1.replace('value="places"', 'value="owns"')
    original_get_file = gitea.get_file

    async def fail_revised_file(path: str, ref: str):
        if path.endswith("/v2.md"):
            raise RuntimeError("temporary publication failure")
        return await original_get_file(path, ref)

    gitea.get_file = fail_revised_file

    first = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, revised_xml),
    )
    first_task = client.get(f"/tasks/{first.json()['task_id']}").json()
    assert first_task["status"] == "error"
    assert first_task["new_version"] == 2

    gitea.get_file = original_get_file
    retry = client.post(
        f"/prd/{root.id}/diagrams/data-model/revisions",
        json=_request(document, revised_xml),
    )

    assert retry.status_code == 202
    assert retry.json()["task_id"] == first.json()["task_id"]
    recovered = client.get(f"/tasks/{retry.json()['task_id']}").json()
    assert recovered["status"] == "done"
    with session_factory() as db:
        assert db.query(SpecVersion).count() == 2
        assert db.query(AgentCall).filter_by(operation="review_spec").count() == 1
