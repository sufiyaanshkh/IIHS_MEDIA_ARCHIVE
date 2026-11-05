from app.services import contextual_metadata as context


def test_matches_project_phrase_in_windows_folder_path(monkeypatch):
    monkeypatch.setattr(context, "load_vocabulary", lambda: [
        {"keyword": "Campus development", "category": "Campuses", "notes": "Construction"},
        {"keyword": "Media Lab", "category": "Labs", "notes": "Production"},
    ])
    result = context.infer_path_context(r"IIHS Archive\\Campus_Development\\IMG_001.jpg")
    assert result["folder_hierarchy"] == ["IIHS Archive", "Campus_Development"]
    assert [item["keyword"] for item in result["matched_keywords"]] == ["Campus development"]
    assert result["matched_keywords"][0]["evidence"] == "folder"
    assert result["matched_categories"] == ["Campuses"]
    assert "Campus development" in result["context_text"]


def test_filename_match_is_marked_separately(monkeypatch):
    monkeypatch.setattr(context, "load_vocabulary", lambda: [
        {"keyword": "Media Lab", "category": "Labs", "notes": "Production"},
    ])
    result = context.infer_path_context("Events/Media_Lab_Interview.mov")
    assert result["matched_keywords"][0]["evidence"] == "filename"


def test_no_false_substring_match(monkeypatch):
    monkeypatch.setattr(context, "load_vocabulary", lambda: [
        {"keyword": "UPP", "category": "Programmes", "notes": "Urban Practitioners Programme"},
    ])
    result = context.infer_path_context("Projects/SUPPORTED/clip.jpg")
    assert result["matched_keywords"] == []
