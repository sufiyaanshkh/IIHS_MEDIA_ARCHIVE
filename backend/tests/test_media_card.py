from dataclasses import replace

from app.api import assets as assets_api
from app.api import auth as auth_api
from app.database import get_db
from app.main import app
from app.models import MediaAsset
from app.services import insights as insight_service

from test_api import client  # noqa: F401  (shared fixture)


def test_keyword_vocabulary_excludes_place_names():
    keywords, excluded = insight_service.keyword_vocabulary()
    names = {item["keyword"].casefold() for item in keywords}
    assert len(keywords) >= 600 and excluded > 100
    assert {"iihs", "group photo", "rainwater harvesting"} <= names
    assert not names & {"bengaluru", "chennai", "karnataka", "kenya", "india", "sadashivanagar", "western ghats"}
    assert not any(item["category"].startswith("Geographies") for item in keywords)


def test_ranking_orders_labels_by_similarity(tmp_path, monkeypatch):
    monkeypatch.setattr(insight_service, "settings", replace(insight_service.settings, storage_root=tmp_path))
    insight_service._vector_cache.clear()

    def fake_embeddings(texts):
        return [([1.0 if "person" in text else 0.0, 1.0] + [0.0] * 766, "m", "r") for text in texts]

    monkeypatch.setattr(insight_service, "text_embeddings", fake_embeddings)
    image = insight_service.mean_image_vector([[1.0, 0.0] + [0.0] * 766])
    result = insight_service.rank_objects(image)
    assert result["status"] == "ready"
    assert result["items"][0]["label"] == "person"
    assert insight_service.rank_objects(None)["status"] == "not_indexed"
    assert insight_service.employees_status()["status"] == "disabled"


def test_media_card_insights_and_review_request(client, tmp_path, monkeypatch):  # noqa: F811
    token = "test-setup-key-" + "z" * 32
    monkeypatch.setattr(auth_api, "settings", replace(auth_api.settings, initial_admin_token=token))
    monkeypatch.setattr(assets_api, "settings", replace(assets_api.settings, storage_root=tmp_path))
    assert client.post("/api/auth/bootstrap", json={"username": "card.admin", "password": "S3cure!8", "setup_token": token}).status_code == 201
    db = next(app.dependency_overrides[get_db]())
    asset = MediaAsset(original_filename="scene.jpg", media_type="image", detected_mime="image/jpeg", size_bytes=10, sha256="a" * 64, storage_key="aa/missing.jpg", metadata_json={})
    db.add(asset)
    db.commit()
    insights = client.get(f"/api/assets/{asset.id}/insights")
    assert insights.status_code == 200
    body = insights.json()
    assert body["source"]["original_filename"] == "scene.jpg"
    assert body["keywords"]["status"] == "not_indexed"
    assert body["employees"]["status"] == "disabled"
    assert body["review"] is None
    assert client.post(f"/api/assets/{asset.id}/review", json={}).status_code == 403
    review = client.post(f"/api/assets/{asset.id}/review", json={}, headers={"X-CSRF-Token": client.cookies.get("iihs_csrf")})
    assert review.status_code == 200
    assert review.json()["review"]["status"] == "requested"
    assert review.json()["review"]["requested_by"] == "card.admin"

