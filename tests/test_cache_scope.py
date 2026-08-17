"""Regression tests for two defects the semantic cache used to have.

Both are the kind that pass every happy-path test: a key collision that only
appears after an eviction, and a missing authorization boundary that only
matters once there is more than one audience.
"""

from __future__ import annotations

from app.cache import GLOBAL_SCOPE, SemanticCache

VEC_A = [1.0, 0.0, 0.0]
VEC_B = [0.0, 1.0, 0.0]
VEC_C = [0.0, 0.0, 1.0]


def make_cache(clock_ref: dict[str, float], **kwargs: object) -> SemanticCache:
    return SemanticCache(
        enabled=True,
        similarity_threshold=0.99,
        ttl_seconds=100,
        clock=lambda: clock_ref["t"],
        **kwargs,  # type: ignore[arg-type]
    )


def test_eviction_does_not_destroy_a_live_entry() -> None:
    """Keys derived from len() collide after an eviction and overwrite.

    Against the previous implementation this test fails: inserting a fourth
    entry after the first expired silently replaced the third.
    """
    clock = {"t": 0.0}
    cache = make_cache(clock)

    clock["t"] = 0.0
    cache.set(VEC_A, "answer-1-old")
    clock["t"] = 90.0
    cache.set(VEC_B, "answer-2")
    clock["t"] = 95.0
    cache.set(VEC_C, "answer-3-victim")

    clock["t"] = 150.0
    cache.get([0.5, 0.5, 0.5])  # a miss, which is what triggers eviction
    cache.set([0.6, 0.8, 0.0], "answer-4-new")

    assert cache.get(VEC_C) == "answer-3-victim"
    assert cache.get(VEC_B) == "answer-2"
    assert cache.get(VEC_A) is None  # genuinely expired


def test_entries_are_isolated_by_scope() -> None:
    """A cached answer must never be served to a different scope."""
    clock = {"t": 0.0}
    cache = make_cache(clock)

    cache.set(VEC_A, "answer for alice", scope="user:alice")

    assert cache.get(VEC_A, scope="user:alice") == "answer for alice"
    assert cache.get(VEC_A, scope="user:bob") is None
    assert cache.get(VEC_A) is None  # global scope is not a wildcard


def test_same_question_caches_separately_per_scope() -> None:
    clock = {"t": 0.0}
    cache = make_cache(clock)

    cache.set(VEC_A, "alice answer", scope="user:alice")
    cache.set(VEC_A, "bob answer", scope="user:bob")

    assert cache.get(VEC_A, scope="user:alice") == "alice answer"
    assert cache.get(VEC_A, scope="user:bob") == "bob answer"


def test_cache_is_bounded() -> None:
    """Unbounded growth is a denial-of-service waiting to happen."""
    clock = {"t": 0.0}
    cache = make_cache(clock, max_entries=3)

    for index in range(10):
        cache.set([float(index), 1.0, 0.0], f"answer-{index}")

    assert len(cache.entries) == 3
    assert cache.get([9.0, 1.0, 0.0]) == "answer-9"  # newest survived


def test_scope_survives_persistence(tmp_path) -> None:
    clock = {"t": 0.0}
    path = tmp_path / "cache.json"
    cache = make_cache(clock, path=path)
    cache.set(VEC_A, "scoped answer", scope="tenant:acme")

    reloaded = make_cache(clock, path=path)

    assert reloaded.get(VEC_A, scope="tenant:acme") == "scoped answer"
    assert reloaded.get(VEC_A, scope="tenant:other") is None


def test_pipeline_scope_defaults_to_global() -> None:
    from app.api_models import AskRequest
    from app.main import cache_scope_for

    assert cache_scope_for(AskRequest(question="q")) == GLOBAL_SCOPE
    assert cache_scope_for(AskRequest(question="q", user_id="U123")) == "U123"
