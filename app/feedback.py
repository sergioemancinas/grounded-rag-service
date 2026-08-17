from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path


class FeedbackStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def record(self, user_id: str, question: str, answer_ts: str, verdict: str) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "INSERT INTO feedback (ts, user_hash, question_hash, answer_ts, verdict) VALUES (?, ?, ?, ?, ?)",
                (
                    time.time(),
                    self._hash(user_id),
                    self._hash(question),
                    answer_ts,
                    verdict,
                ),
            )
            connection.commit()

    def summary(self) -> dict[str, object]:
        since = time.time() - 7 * 24 * 60 * 60
        with sqlite3.connect(self.db_path) as connection:
            verdict_rows = connection.execute(
                "SELECT verdict, COUNT(*) FROM feedback GROUP BY verdict"
            ).fetchall()
            recent_count = connection.execute(
                "SELECT COUNT(*) FROM feedback WHERE ts >= ?",
                (since,),
            ).fetchone()[0]
        return {
            "counts_by_verdict": {str(verdict): int(count) for verdict, count in verdict_rows},
            "last_7_days": int(recent_count),
        }

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
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

    def _hash(self, value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
