import json
from pathlib import Path


def test_paths_config_defaults_to_no_approved_roots(monkeypatch):
    monkeypatch.setenv("INGEST_ROOTS_JSON", "[]")
    from app.config import Settings
    settings = Settings(_env_file=None)
    assert json.loads(settings.ingest_roots_json) == []
