"""Feedback storage: verdict counts without a record of who asked what.

Identifiers and question text are stored as keyed HMAC-SHA256 digests, not
plain hashes. That distinction is the whole point. A bare SHA-256 of a
platform user id or a natural-language question is reversible by
enumeration: user id spaces are small and enumerable, and questions fall to
a dictionary attack. Only a secret key makes the digest unlinkable to
anyone who later reads the database.

Even keyed, this is pseudonymization rather than anonymization: with the key
in hand the mapping is recoverable, so under GDPR the rows remain personal
data. The honest claim is "verdicts are usable for aggregate analysis
without storing readable identifiers or question text", not "anonymous".

Set FEEDBACK_HMAC_KEY in production. When it is unset the store derives a
random key per process, which keeps digests unlinkable but means counts
cannot be grouped across restarts; that is the safe default, and it is
logged once at startup so the trade-off is never silent.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger("grounded_rag.feedback")


class FeedbackStore:
    """SQLite store of up/down verdicts keyed by HMAC digests."""

    def __init__(self, db_path: Path, hmac_key: str = "") -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if hmac_key:
            self._key = hmac_key.encode("utf-8")
        else:
            self._key = secrets.token_bytes(32)
            logger.info(
                "feedback: FEEDBACK_HMAC_KEY unset, using a per-process key; "
                "digests will not be comparable across restarts"
            )
        self._ensure_schema()

    def record(self, user_id: str, question: str, answer_ts: str, verdict: str) -> None:
        """Store one verdict; the user id and question are never stored in the clear."""
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO feedback (ts, user_hash, question_hash, answer_ts, verdict) VALUES (?, ?, ?, ?, ?)",
                (
                    time.time(),
                    self._digest(user_id),
                    self._digest(question),
                    answer_ts,
                    verdict,
                ),
            )
            connection.commit()

    def summary(self) -> dict[str, object]:
        """Aggregate verdict counts, overall and for the last seven days."""
        since = time.time() - 7 * 24 * 60 * 60
        with self._connect() as connection:
            verdict_rows = connection.execute("SELECT verdict, COUNT(*) FROM feedback GROUP BY verdict").fetchall()
            recent_count = connection.execute(
                "SELECT COUNT(*) FROM feedback WHERE ts >= ?",
                (since,),
            ).fetchone()[0]
        return {
            "counts_by_verdict": {str(verdict): int(count) for verdict, count in verdict_rows},
            "last_7_days": int(recent_count),
        }

    def _connect(self) -> sqlite3.Connection:
        """Open a connection that closes itself.

        ``with sqlite3.connect(...)`` commits but does not close, which leaks
        a handle per call; ``contextlib.closing`` is what actually closes it.
        """
        from contextlib import closing

        return closing(sqlite3.connect(self.db_path))  # type: ignore[return-value]

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    ts REAL NOT NULL,
                    user_hash TEXT NOT NULL,
                    question_hash TEXT NOT NULL,
                    answer_ts TEXT NOT NULL,
                    verdict TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _digest(self, value: str) -> str:
        """Keyed digest; unkeyed SHA-256 over these inputs is reversible."""
        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()
