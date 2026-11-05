from app.services.query_intent import classify_query_intent, is_identifier_query


def test_is_identifier_query():
    assert is_identifier_query("IMG_20240517_0021.jpg") is True
    assert is_identifier_query("DSC_0042.JPG") is True
    assert is_identifier_query("report_final.pdf") is True
    assert is_identifier_query("video_clip.mp4") is True
    assert is_identifier_query("IIHS-DOC-2023-01") is True
    assert is_identifier_query("97331596f62816d3") is True
    assert is_identifier_query("20241029") is True

    # Natural language queries must NOT be classified as identifiers
    assert is_identifier_query("People sitting under trees") is False
    assert is_identifier_query("Construction workers wearing safety helmets") is False
    assert is_identifier_query("Students attending an outdoor workshop") is False
    assert is_identifier_query("A building surrounded by greenery") is False


def test_classify_visual_description_intent():
    # Spatial, action, and relational visual queries
    intent1 = classify_query_intent("People sitting under trees")
    assert intent1.intent_type == "visual_description"
    assert intent1.channel_weights["visual_embedding"] >= 1.5
    assert intent1.channel_weights["caption_description"] >= 1.5
    # Folder lexical matches must be heavily suppressed for visual descriptions
    assert intent1.channel_weights["metadata_lexical"] <= 0.1

    intent2 = classify_query_intent("Construction workers wearing safety helmets")
    assert intent2.intent_type == "visual_description"
    assert intent2.channel_weights["metadata_lexical"] <= 0.1

    intent3 = classify_query_intent("A building surrounded by greenery")
    assert intent3.intent_type == "visual_description"
    assert intent3.channel_weights["visual_embedding"] >= 1.5

    intent4 = classify_query_intent("Aerial view of a campus")
    assert intent4.intent_type == "visual_description"


def test_classify_object_search_intent():
    intent = classify_query_intent("Solar panels")
    assert intent.intent_type == "object_search"
    assert intent.channel_weights["object_tag"] >= 1.4

    intent2 = classify_query_intent("Birds")
    assert intent2.intent_type == "object_search"


def test_classify_exact_identifier_intent():
    intent = classify_query_intent("IMG_0021.JPG")
    assert intent.intent_type == "exact_identifier"
    assert intent.is_exact_lookup is True
    assert intent.channel_weights["metadata_lexical"] >= 2.0
    assert intent.channel_weights["visual_embedding"] <= 0.3


def test_classify_person_search_intent():
    active_people = ["Mohammed", "Dr. Ramesh"]
    intent = classify_query_intent("Mohammed attending a meeting", active_people=active_people)
    assert intent.intent_type == "person_search"
    assert "Mohammed" in intent.person_names
    assert intent.channel_weights["face_biometrics"] >= 2.0


def test_classify_ocr_document_intent():
    intent = classify_query_intent("Signboard with text saying Emergency Exit")
    assert intent.intent_type == "ocr_document"
    assert intent.channel_weights["ocr_text"] >= 1.8


def test_classify_mixed_metadata_intent():
    intent = classify_query_intent("Images of trees from 2022")
    assert intent.intent_type == "mixed"
    assert 2022 in intent.years
    assert intent.modality_hint == "image"

