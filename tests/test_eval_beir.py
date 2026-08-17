"""Offline unit tests for BEIR evaluation metric helpers (no network)."""

from __future__ import annotations

import math

from scripts.eval_beir import dcg_at_k, mrr_at_k, ndcg_at_k, recall_at_k


def test_dcg_at_k_log2_discount() -> None:
    # gains [3, 2, 1]: 3/log2(2) + 2/log2(3) + 1/log2(4)
    expected = 3.0 / math.log2(2) + 2.0 / math.log2(3) + 1.0 / math.log2(4)
    assert dcg_at_k([3.0, 2.0, 1.0], k=3) == expected
    assert dcg_at_k([3.0, 2.0, 1.0], k=1) == 3.0


def test_ndcg_at_k_perfect_and_partial() -> None:
    qrels = {"a": 2.0, "b": 1.0, "c": 0.0}
    perfect = ndcg_at_k(["a", "b", "c"], qrels, k=3)
    assert perfect == 1.0

    # gains use 2^rel - 1 => a:3, b:1
    # ranked [b, a]: DCG = 1/log2(2) + 3/log2(3)
    # IDCG = 3/log2(2) + 1/log2(3)
    dcg = 1.0 / math.log2(2) + 3.0 / math.log2(3)
    idcg = 3.0 / math.log2(2) + 1.0 / math.log2(3)
    assert ndcg_at_k(["b", "a"], qrels, k=2) == dcg / idcg


def test_ndcg_at_k_empty_ideal_is_zero() -> None:
    assert ndcg_at_k(["x"], {}, k=10) == 0.0


def test_recall_at_k_counts_relevant_only() -> None:
    qrels = {"a": 1.0, "b": 2.0, "c": 0.0}
    assert recall_at_k(["a", "z"], qrels, k=10) == 0.5
    assert recall_at_k(["a", "b"], qrels, k=10) == 1.0
    assert recall_at_k(["z"], qrels, k=1) == 0.0
    assert recall_at_k(["a"], {}, k=5) == 0.0


def test_mrr_at_k_first_relevant_rank() -> None:
    qrels = {"b": 1.0, "c": 1.0}
    assert mrr_at_k(["a", "b", "c"], qrels, k=10) == 0.5
    assert mrr_at_k(["b"], qrels, k=10) == 1.0
    assert mrr_at_k(["a", "z"], qrels, k=2) == 0.0
    assert mrr_at_k(["a", "b"], qrels, k=1) == 0.0
