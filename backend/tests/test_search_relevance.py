"""Regression tests for query-relative ranking helpers."""
from app.services.hybrid_search import (
    _is_identifier_query,
    _semantic_gate,
    _semantic_z_scores,
    _token_coverage,
)


def test_semantic_z_scores_are_query_relative():
    scores = [0.12, 0.18, 0.24, 0.30]
    z = _semantic_z_scores(scores)
    assert len(z) == len(scores)
    assert z[0] < z[-1]
    assert abs(sum(z)) < 1e-8


def test_semantic_gate_rejects_uniformly_weak_matches():
    assert _semantic_gate([0.02, 0.03, 0.04]) == [False, False, False]


def test_semantic_gate_keeps_distinct_strong_match():
    accepted = _semantic_gate([0.12, 0.14, 0.18, 0.31])
    assert accepted[-1] is True


def test_identifier_queries_are_detected():
    assert _is_identifier_query("IMG_20240517_0021.jpg")
    assert _is_identifier_query("IIHS-2024-103")
    assert not _is_identifier_query("workers building a road")


def test_token_coverage_uses_meaningful_query_terms():
    assert _token_coverage("construction workers building a road", ["construction", "workers", "road"]) == 1.0
    assert _token_coverage("workers near a road", ["construction", "workers", "road"]) == 2 / 3
