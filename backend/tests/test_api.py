from pathlib import Path
from io import BytesIO
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app import models  # noqa: F401
from app.main import app
from app.services.media import MediaValidationError, resolve_under_root
from app.api import auth as auth_api
from app.api import jobs as jobs_api
from app.api.assets import _year_window
from app.services import media as media_service
from app.services import embeddings as embedding_service


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_liveness_is_available_without_authentication(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_protected_catalogue_and_source_routes_require_authentication(client):
    assert client.get("/api/assets").status_code == 401
    assert client.get("/api/sources").status_code == 401


def test_error_response_is_generic_and_has_request_id(client):
    response = client.get("/api/assets")
    assert response.status_code == 401
    payload = response.json()["error"]
    assert payload["code"] == "UNAUTHENTICATED"
    assert payload["request_id"] == response.headers["x-request-id"]
    assert "password" not in response.text.lower()


def test_media_path_resolver_rejects_traversal_and_symlinks(tmp_path: Path):
    root = tmp_path / "approved"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"synthetic")
    (root / "link.jpg").symlink_to(outside)
    with pytest.raises(MediaValidationError):
        resolve_under_root(root, "../outside.jpg")
    with pytest.raises(MediaValidationError):
        resolve_under_root(root, "link.jpg")


def test_added_year_filter_uses_utc_calendar_bounds():
    assert _year_window(None) is None
    start, end = _year_window(2026)
    assert start.isoformat() == "2026-01-01T00:00:00+00:00"
    assert end.isoformat() == "2027-01-01T00:00:00+00:00"


def test_ocr_normalizes_and_bounds_extracted_text(tmp_path: Path, monkeypatch):
    image = tmp_path / "image.png"
    image.touch()
    monkeypatch.setattr(media_service.shutil, "which", lambda _: "/usr/bin/tesseract")
    monkeypatch.setattr(media_service.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "Printed   name\n  Urban study "})())
    assert media_service.extract_image_text(image) == "Printed name Urban study"


def test_image_embedding_accepts_bridge_flat_vector(tmp_path: Path, monkeypatch):
    image = tmp_path / "image.png"
    image.write_bytes(b"synthetic")
    response = {"embedding": [0.0] * 768, "model_id": embedding_service.settings.embedding_model, "model_revision": embedding_service.settings.embedding_revision}
    monkeypatch.setattr(embedding_service, "_post", lambda *args, **kwargs: response)
    vector, model, revision = embedding_service.image_embedding(image)
    assert len(vector) == 768
    assert (model, revision) == (embedding_service.settings.embedding_model, embedding_service.settings.embedding_revision)


def test_file_picker_import_queues_validated_media_without_review(client, tmp_path: Path, monkeypatch):
    token = "test-setup-key-" + "y" * 32
    monkeypatch.setattr(auth_api, "settings", replace(auth_api.settings, initial_admin_token=token))
    import_settings = replace(jobs_api.settings, storage_root=tmp_path, max_files_per_job=5, max_file_bytes=1024 * 1024, max_import_bytes=2 * 1024 * 1024, min_storage_free_bytes=0)
    monkeypatch.setattr(jobs_api, "settings", import_settings)
    monkeypatch.setattr(media_service, "settings", import_settings)
    assert client.post("/api/auth/bootstrap", json={"username": "first.admin", "password": "S3cure!8", "setup_token": token}).status_code == 201
    csrf = client.cookies.get("iihs_csrf")
    image = BytesIO()
    from PIL import Image
    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    response = client.post("/api/ingestions/imports", files={"files": ("scene.png", image.getvalue(), "image/png")}, headers={"X-CSRF-Token": csrf, "Idempotency-Key": "x" * 32})
    assert response.status_code == 202
    job = response.json()
    assert job["kind"] == "ingest"
    assert job["state"] == "queued"
    detail = client.get("/api/jobs/" + job["id"]).json()
    assert detail["preview_files"][0]["path"] == "scene.png"
    assert detail["preview_files"][0]["state"] == "ready"


def test_bootstrap_creates_the_only_public_admin_and_starts_a_session(client, monkeypatch):
    monkeypatch.setattr(auth_api, "settings", replace(auth_api.settings, initial_admin_token="test-setup-key-" + "x" * 32))
    assert client.get("/api/auth/bootstrap/status").json() == {"can_create_admin": True, "can_register": False}
    assert client.post("/api/auth/register", json={"username": "early.user", "password": "S3cure!8"}).status_code == 409
    assert client.post("/api/auth/bootstrap", json={"username": "first.admin", "password": "S3cure!", "setup_token": "test-setup-key-" + "x" * 32}).status_code == 422
    response = client.post("/api/auth/bootstrap", json={"username": "First.Admin", "password": "S3cure!8", "setup_token": "test-setup-key-" + "x" * 32})
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "admin"
    assert client.get("/api/auth/me").json()["username"] == "first.admin"
    assert client.get("/api/auth/bootstrap/status").json() == {"can_create_admin": False, "can_register": True}
    assert client.post("/api/auth/bootstrap", json={"username": "second.admin", "password": "S3cure!8", "setup_token": "test-setup-key-" + "x" * 32}).status_code == 409
    assert client.post("/api/auth/register", json={"username": "short.pass", "password": "S3cure!"}).status_code == 422
    signup = client.post("/api/auth/register", json={"username": "Archive.Researcher", "password": "S3cure!8", "role": "admin"})
    assert signup.status_code == 201
    assert signup.json()["user"]["role"] == "researcher"
    assert client.get("/api/auth/me").json()["role"] == "researcher"
    assert client.post("/api/auth/register", json={"username": "archive.researcher", "password": "S3cure!8"}).status_code == 409
    assert client.post("/api/auth/login", json={"username": "first.admin", "password": "S3cure!8"}).status_code == 200
    csrf = client.cookies.get("iihs_csrf")
    headers = {"X-CSRF-Token": csrf}
    updated = client.post(f"/api/auth/users/{signup.json()['user']['id']}/role", json={"role": "admin"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["role"] == "admin"
    assert client.post(f"/api/auth/users/{response.json()['user']['id']}/role", json={"role": "researcher"}, headers=headers).status_code == 409
