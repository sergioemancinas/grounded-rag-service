from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Any

from app.retrieval import ScoredChunk


def verify_slack_signature(signing_secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    if not signing_secret:
        return False
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - timestamp_int) > 300:
        return False
    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def markdown_to_mrkdwn(text: str) -> str:
    converted = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    converted = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", converted)
    return converted


def render_answer_blocks(answer: str, sources: list[ScoredChunk]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in _split_text(markdown_to_mrkdwn(answer), limit=3000):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": part}})
    if sources:
        source_lines: list[str] = []
        for index, scored in enumerate(sources, start=1):
            chunk = scored.chunk
            label = " > ".join(chunk.heading_path) if chunk.heading_path else chunk.title
            if chunk.url:
                source_lines.append(f"{index}. <{chunk.url}|{chunk.title}> - {label}")
            else:
                source_lines.append(f"{index}. {chunk.title} - {label}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Sources*\n" + "\n".join(source_lines)}})
    blocks.append(
        {
            "type": "actions",
            "block_id": "feedback",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Helpful"},
                    "value": "helpful",
                    "action_id": "feedback_helpful",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Not helpful"},
                    "value": "not_helpful",
                    "action_id": "feedback_not_helpful",
                },
            ],
        }
    )
    return blocks


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        boundary = remaining.rfind("\n\n", 0, limit)
        if boundary < 500:
            boundary = limit
        parts.append(remaining[:boundary])
        remaining = remaining[boundary:].lstrip()
    return parts
