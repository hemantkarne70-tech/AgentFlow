import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app, get_db


@pytest.fixture()
def client():
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


TEMPLATE = {
    "name": "Text cleanup",
    "description": "Convert text to uppercase",
    "steps": [{"tool": "uppercase", "input": "hello agentflow"}],
}


def test_create_and_get_workflow_template(client):
    created = client.post("/workflows/templates", json=TEMPLATE)
    assert created.status_code == 201
    payload = created.json()
    assert payload["name"] == TEMPLATE["name"]
    assert payload["steps"] == TEMPLATE["steps"]
    assert payload["id"] > 0

    fetched = client.get(f"/workflows/templates/{payload['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["description"] == TEMPLATE["description"]


def test_template_name_must_be_unique(client):
    assert client.post("/workflows/templates", json=TEMPLATE).status_code == 201
    duplicate = client.post("/workflows/templates", json=TEMPLATE)
    assert duplicate.status_code == 409


def test_list_templates_supports_search_and_pagination(client):
    client.post("/workflows/templates", json=TEMPLATE)
    second = {**TEMPLATE, "name": "Number calculation"}
    client.post("/workflows/templates", json=second)

    response = client.get("/workflows/templates?search=Text&page=1&per_page=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total_items"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["name"] == "Text cleanup"


def test_update_and_delete_template(client):
    created = client.post("/workflows/templates", json=TEMPLATE).json()
    updated_data = {
        **TEMPLATE,
        "name": "Text cleanup v2",
        "description": "Updated description",
    }
    updated = client.put(
        f"/workflows/templates/{created['id']}", json=updated_data
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Text cleanup v2"

    deleted = client.delete(f"/workflows/templates/{created['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/workflows/templates/{created['id']}").status_code == 404


def test_template_not_found(client):
    assert client.get("/workflows/templates/999999").status_code == 404
    assert client.put("/workflows/templates/999999", json=TEMPLATE).status_code == 404
    assert client.delete("/workflows/templates/999999").status_code == 404
