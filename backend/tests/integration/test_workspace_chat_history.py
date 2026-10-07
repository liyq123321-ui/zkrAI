from dataclasses import replace
import base64

from fastapi.testclient import TestClient

from app.database.models import Project
from main import create_app


def test_project_agent_chat_history_is_durable_and_session_scoped(session_factory, test_settings):
    with session_factory() as db:
        db.add(Project(id="project-chat", session_id="session-chat", creation_request_id="chat-request",
                       brief={"title": "PRD"}, final_approver="owner", phase="SPECIFICATION"))
        db.add(Project(id="other-project", session_id="other-session", creation_request_id="other-request",
                       brief={"title": "Other"}, final_approver="other", phase="SPECIFICATION"))
        db.commit()

    settings = replace(test_settings, identity_mode="local", identity_actor_id="owner")
    app = create_app(settings=settings, session_factory=session_factory, auto_bind_prd_review=False)
    with TestClient(app) as client:
        user = {"id": "message-user", "role": "user", "body": "把当前 PRD 的项目目标补充指标"}
        reply = {"id": "message-agent", "role": "agent", "body": "已接收修改指令"}
        assert client.post("/sessions/session-chat/chat-messages", json=user).status_code == 201
        assert client.post("/sessions/session-chat/chat-messages", json=reply).status_code == 201
        assert client.post("/sessions/session-chat/chat-messages", json=user).status_code == 201
        rows = client.get("/sessions/session-chat/chat-messages").json()
        assert [row["body"] for row in rows] == [user["body"], reply["body"]]
        assert all(row["createdAt"] for row in rows)
        assert client.get("/sessions/other-session/chat-messages").status_code == 403
        assert client.get("/sessions/session-chat/presentations/team").json() == {
            "ready": False, "frozen": False, "title": None, "created_at": None,
        }
        assert client.post("/sessions/session-chat/presentations/team").status_code == 409
    with TestClient(app) as client:
        assert len(client.get("/sessions/session-chat/chat-messages").json()) == 2


def test_project_chat_screenshot_is_saved_with_its_message(session_factory, test_settings):
    with session_factory() as db:
        db.add(Project(id="project-image", session_id="session-image", creation_request_id="image-request",
                       brief={"title": "PRD"}, final_approver="owner", phase="SPECIFICATION"))
        db.commit()
    settings = replace(test_settings, identity_mode="local", identity_actor_id="owner")
    app = create_app(settings=settings, session_factory=session_factory, auto_bind_prd_review=False)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"screenshot").decode()
    with TestClient(app) as client:
        uploaded = client.post("/sessions/session-image/chat-images", json={
            "mime_type": "image/png", "content_base64": png,
        })
        assert uploaded.status_code == 201
        image_id = uploaded.json()["id"]
        saved = client.post("/sessions/session-image/chat-messages", json={
            "id": "image-message", "role": "user", "body": "修改截图中这段话", "image_ids": [image_id],
        })
        assert saved.status_code == 201
        assert saved.json()["image_ids"] == [image_id]
        assert client.get("/sessions/session-image/chat-messages").json()[0]["image_ids"] == [image_id]
        assert client.get(f"/sessions/session-image/chat-images/{image_id}").content == base64.b64decode(png)
