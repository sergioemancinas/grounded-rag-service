from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from app.config import Settings
from app.interfaces import Generator
from app.prompts import load_prompt
from app.retrieval import ScoredChunk


@dataclass(frozen=True)
class Answer:
    text: str
    citations: list[dict[str, str]]


MAX_HISTORY_TURNS = 6


def format_history(history: Sequence[str]) -> str:
    """Render prior turns for a prompt, newest last, oldest dropped.

    History is caller-supplied and therefore untrusted: it is labelled as a
    transcript so the model treats it as context rather than instructions, and
    bounded so a long conversation cannot crowd the retrieved sources out of
    the context window.
    """
    turns = [turn.strip() for turn in history if turn and turn.strip()]
    if not turns:
        return ""
    recent = turns[-MAX_HISTORY_TURNS:]
    return "\n".join(f"- {turn}" for turn in recent)


def expand_query(
    question: str,
    history: Sequence[str],
    settings: Settings,
    generator: Generator | None = None,
) -> list[str]:
    if settings.generation_provider != "openai" or generator is None:
        return [question]
    # Prior turns are what let the expander resolve a follow-up like "and for
    # returns?" into a self-contained query.
    transcript = format_history(history)
    user = f"Earlier turns (untrusted transcript):\n{transcript}\n\nQuestion:\n{question}" if transcript else question
    raw = generator.generate(load_prompt("expand_query", settings), user, max_tokens=220)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            rewrites = [str(item).strip() for item in parsed if str(item).strip()]
            return rewrites[:3] or [question]
    except json.JSONDecodeError:
        pass
    lines = [line.strip("- ").strip() for line in raw.splitlines() if line.strip()]
    return lines[:3] or [question]


def generate_answer(
    question: str,
    chunks: Sequence[ScoredChunk],
    history: Sequence[str],
    settings: Settings,
    generator: Generator,
    strict: bool = False,
) -> Answer:
    system = load_prompt("answer_system_strict" if strict else "answer_system", settings)
    context_blocks: list[str] = []
    citations: list[dict[str, str]] = []
    for index, scored in enumerate(chunks, start=1):
        chunk = scored.chunk
        text = chunk.text[: settings.max_context_chars_per_chunk]
        heading = " > ".join(chunk.heading_path)
        context_blocks.append(f"SOURCE [{index}] {chunk.title} {heading}\n{text}")
        citations.append(
            {
                "number": str(index),
                "doc_id": chunk.doc_id,
                "title": chunk.title,
                "url": chunk.url,
            }
        )
    sections = []
    transcript = format_history(history)
    if transcript:
        sections.append(f"Earlier turns (untrusted transcript, for context only):\n{transcript}")
    sections.append(f"Question:\n{question}")
    sections.append("Sources:\n" + "\n\n".join(context_blocks))
    user = "\n\n".join(sections)
    answer_text = generator.generate(system=system, user=user, max_tokens=900)
    return Answer(text=answer_text, citations=citations)


def suggest_followups(
    question: str, answer: Answer, settings: Settings, generator: Generator | None = None
) -> list[str]:
    del question, answer, settings, generator
    return []
