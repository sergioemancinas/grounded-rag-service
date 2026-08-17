"""The feedback store must not leave readable identifiers or questions behind.

The earlier implementation used unkeyed SHA-256, which is reversible for
these inputs: platform user ids come from a small enumerable space and
questions fall to a dictionary attack. These tests encode that difference.
"""

from __future__ import annotations

import hashlib
import sqlite3

from app.feedback import FeedbackStore

USER = "U024BE7LH"
QUESTION = "How do refunds work?"


def test_no_plaintext_identifier_or_question_is_stored(tmp_path) -> None:
    store = FeedbackStore(tmp_path / "fb.sqlite3", hmac_key="test-key")
    store.record(user_id=USER, question=QUESTION, answer_ts="1712345678.9", verdict="up")

    rows = sqlite3.connect(tmp_path / "fb.sqlite3").execute("SELECT * FROM feedback").fetchall()
    blob = " ".join(str(value) for row in rows for value in row)

    assert USER not in blob
    assert QUESTION not in blob


def test_digests_are_keyed_not_plain_sha256(tmp_path) -> None:
    """An attacker who guesses the input must not be able to confirm it.

    With unkeyed SHA-256 the stored digest equals sha256(user_id), so
    enumerating a workspace's user ids reverses the column outright.
    """
    store = FeedbackStore(tmp_path / "fb.sqlite3", hmac_key="test-key")
    store.record(user_id=USER, question=QUESTION, answer_ts="1.0", verdict="up")

    user_hash = sqlite3.connect(tmp_path / "fb.sqlite3").execute("SELECT user_hash FROM feedback").fetchone()[0]

    assert user_hash != hashlib.sha256(USER.encode()).hexdigest()


def test_same_key_yields_stable_digests(tmp_path) -> None:
    """Aggregation still works: one user's verdicts group together."""
    store = FeedbackStore(tmp_path / "fb.sqlite3", hmac_key="test-key")
    store.record(user_id=USER, question=QUESTION, answer_ts="1.0", verdict="up")
    store.record(user_id=USER, question=QUESTION, answer_ts="2.0", verdict="down")

    hashes = sqlite3.connect(tmp_path / "fb.sqlite3").execute("SELECT user_hash FROM feedback").fetchall()

    assert hashes[0][0] == hashes[1][0]


def test_different_keys_yield_unlinkable_digests(tmp_path) -> None:
    """Two deployments cannot correlate their users without the key."""
    first = FeedbackStore(tmp_path / "a.sqlite3", hmac_key="key-a")
    second = FeedbackStore(tmp_path / "b.sqlite3", hmac_key="key-b")
    first.record(user_id=USER, question=QUESTION, answer_ts="1.0", verdict="up")
    second.record(user_id=USER, question=QUESTION, answer_ts="1.0", verdict="up")

    hash_a = sqlite3.connect(tmp_path / "a.sqlite3").execute("SELECT user_hash FROM feedback").fetchone()[0]
    hash_b = sqlite3.connect(tmp_path / "b.sqlite3").execute("SELECT user_hash FROM feedback").fetchone()[0]

    assert hash_a != hash_b


def test_unset_key_still_records_and_summarizes(tmp_path) -> None:
    """The safe default must not break the feature."""
    store = FeedbackStore(tmp_path / "fb.sqlite3")
    store.record(user_id=USER, question=QUESTION, answer_ts="1.0", verdict="up")
    store.record(user_id="U2", question="other", answer_ts="2.0", verdict="down")

    summary = store.summary()

    assert summary["counts_by_verdict"] == {"up": 1, "down": 1}
    assert summary["last_7_days"] == 2
