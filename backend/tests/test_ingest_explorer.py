"""Tests for Windows Explorer drive selection and ingestion service."""
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import SourceDefinition
from app.services.ingest_explorer import (
    browse_drive_from_explorer,
    get_system_drives,
    register_drive_source,
    sync_active_drive_env,
)


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_get_system_drives_returns_list():
    drives = get_system_drives()
    assert isinstance(drives, list)
    if drives:
        first = drives[0]
        assert "drive" in first
        assert "ready" in first
        assert "total_gb" in first
        assert "free_gb" in first


def test_register_drive_source(test_db):
    source = register_drive_source(test_db, drive_path="D:\\", name="External HDD (D:\\)")
    assert source is not None
    assert source.name == "External HDD (D:\\)"
    assert source.enabled is True

    # Re-registering same path updates existing record without duplicate
    source2 = register_drive_source(test_db, drive_path="D:\\", name="Updated External HDD")
    assert source2.id == source.id
    assert source2.name == "Updated External HDD"

    persisted = test_db.get(SourceDefinition, source.id)
    assert persisted is not None
    assert persisted.name == "Updated External HDD"


def test_browse_drive_from_explorer_mock():
    with patch("urllib.request.urlopen") as mock_url:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"cancelled": false, "path": "D:\\\\Archive", "name": "Archive (D:\\\\Archive)"}'
        mock_resp.__enter__.return_value = mock_resp
        mock_url.return_value = mock_resp

        res = browse_drive_from_explorer(title="Test Dialog")
        assert res.get("cancelled") is False
        assert res.get("path") == "D:\\Archive"

