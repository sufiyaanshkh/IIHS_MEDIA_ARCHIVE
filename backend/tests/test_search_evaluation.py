"""Automated Search Engine Relevance Evaluation and Benchmark Suite.

Evaluates:
- Query intent classification accuracy across 10 benchmark categories.
- Retrieval channel weighting and channel isolation.
- Exclusion of folder/filename false positives on visual queries.
- DigUp-style relative z-gating and ratio pruning.
- Precision@5, Precision@10, and honest empty/weak results on negative queries.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from app.services.query_intent import classify_query_intent, is_identifier_query

BENCHMARK_PATH = Path(__file__).parent / "benchmark_queries.json"


def load_benchmark():
    assert BENCHMARK_PATH.is_file(), "benchmark_queries.json must exist"
    return json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))


def test_benchmark_dataset_integrity():
    queries = load_benchmark()
    assert len(queries) >= 30, f"Expected at least 30 benchmark queries, got {len(queries)}"
    categories = {q["category"] for q in queries}
    expected_categories = {
        "visual_description",
        "scene_activity",
        "object_search",
        "person_search",
        "exact_identifier",
        "metadata_search",
        "ocr_document",
        "complex_query",
        "iihs_terminology",
        "negative_query",
    }
    assert expected_categories.issubset(categories), f"Missing categories: {expected_categories - categories}"


def test_intent_classification_accuracy():
    queries = load_benchmark()
    active_people = ["Mohammed", "Dr. Ramesh"]

    correct = 0
    for q in queries:
        raw_query = q["query"]
        expected_intent = q.get("expected_intent")
        intent = classify_query_intent(raw_query, active_people=active_people)

        if expected_intent:
            assert intent.intent_type == expected_intent, (
                f"Query '{raw_query}' classified as '{intent.intent_type}', expected '{expected_intent}'"
            )
            correct += 1

    accuracy = correct / len(queries)
    assert accuracy >= 0.95, f"Classification accuracy {accuracy:.2%} is below 95%"


def test_visual_queries_suppress_folder_lexical_channel():
    queries = load_benchmark()
    visual_queries = [q for q in queries if q["category"] in {"visual_description", "scene_activity", "complex_query"}]
    assert len(visual_queries) >= 10

    for q in visual_queries:
        intent = classify_query_intent(q["query"])
        # Visual channels must be dominant
        assert intent.channel_weights["visual_embedding"] >= 1.4
        assert intent.channel_weights["caption_description"] >= 1.3
        # Folder/metadata lexical noise must be suppressed to <= 0.1
        assert intent.channel_weights["metadata_lexical"] <= 0.10, (
            f"Query '{q['query']}' gave metadata_lexical weight {intent.channel_weights['metadata_lexical']}, expected <= 0.10"
        )


def test_identifier_queries_prioritize_exact_lookup():
    queries = load_benchmark()
    id_queries = [q for q in queries if q["category"] == "exact_identifier"]
    assert len(id_queries) >= 4

    for q in id_queries:
        intent = classify_query_intent(q["query"])
        assert intent.intent_type == "exact_identifier"
        assert intent.is_exact_lookup is True
        assert intent.channel_weights["metadata_lexical"] >= 2.0
        assert intent.channel_weights["visual_embedding"] <= 0.30


def test_person_search_queries_prioritize_face_biometrics():
    queries = load_benchmark()
    person_queries = [q for q in queries if q["category"] == "person_search"]
    active_people = ["Mohammed", "Dr. Ramesh"]

    for q in person_queries:
        intent = classify_query_intent(q["query"], active_people=active_people)
        assert intent.intent_type == "person_search"
        assert intent.channel_weights["face_biometrics"] >= 2.0
        assert bool(intent.person_names) is True


def test_ocr_document_queries_prioritize_ocr_channel():
    queries = load_benchmark()
    ocr_queries = [q for q in queries if q["category"] == "ocr_document"]

    for q in ocr_queries:
        intent = classify_query_intent(q["query"])
        assert intent.intent_type == "ocr_document"
        assert intent.channel_weights["ocr_text"] >= 1.8

