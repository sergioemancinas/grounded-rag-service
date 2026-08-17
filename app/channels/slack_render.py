"""Slack-side rendering: markdown answer plus sources into Block Kit.

Rendering lives in the adapter on purpose. The core returns portable
markdown with structured citations; only this module knows that Slack wants
``mrkdwn``, single asterisks for bold, ``<url|label>`` links, and a 3000
character ceiling per section block.
"""

from __future__ import annotations

import re
from typing import Any

from app.api_models import SourceRef

SECTION_CHAR_LIMIT = 3000
"""Slack rejects section blocks whose text exceeds this length."""


def markdown_to_mrkdwn(text: str) -> str:
    """Convert the markdown subset the core emits into Slack mrkdwn."""
    converted = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    converted = re.sub(r"\*\*([^*]+)\*\*", r"*\1*", converted)
    return converted


def render_answer_blocks(answer: str, sources: list[SourceRef]) -> list[dict[str, Any]]:
    """Build the Block Kit payload: answer sections, sources, feedback buttons."""
    blocks: list[dict[str, Any]] = []
    for part in _split_text(markdown_to_mrkdwn(answer), limit=SECTION_CHAR_LIMIT):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": part}})
    if sources:
        source_lines: list[str] = []
        for index, source in enumerate(sources, start=1):
            label = " > ".join(source.heading_path) if source.heading_path else source.title
            if source.url:
                source_lines.append(f"{index}. <{source.url}|{source.title}> - {label}")
            else:
                source_lines.append(f"{index}. {source.title} - {label}")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Sources*\n" + "\n".join(source_lines)}})
    blocks.append(
        {
            "type": "actions",
            "block_id": "feedback",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Helpful"},
                    "value": "up",
                    "action_id": "feedback_helpful",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Not helpful"},
                    "value": "down",
                    "action_id": "feedback_not_helpful",
                },
            ],
        }
    )
    return blocks


def _split_text(text: str, limit: int) -> list[str]:
    """Split on paragraph boundaries so no section block exceeds ``limit``."""
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
