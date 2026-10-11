import pytest
from fastapi.testclient import TestClient

from app.main import Note, SessionLocal, app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        db = SessionLocal()
        try:
            db.query(Note).delete()
            db.commit()
        finally:
            db.close()
        yield test_client


def test_version_endpoint(client: TestClient) -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json() == {
        "service": "workflow-lab-api",
        "version": "test",
        "framework": "FastAPI",
    }


def test_create_and_list_notes(client: TestClient) -> None:
    created = client.post("/notes", json={"text": "First Argo workflow note"})
    assert created.status_code == 201
    assert created.json()["text"] == "First Argo workflow note"
    assert "id" in created.json()

    listed = client.get("/notes")
    assert listed.status_code == 200
    assert [note["text"] for note in listed.json()] == ["First Argo workflow note"]


def test_empty_note_is_rejected(client: TestClient) -> None:
    response = client.post("/notes", json={"text": ""})
    assert response.status_code == 422
