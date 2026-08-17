from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import Settings
from app.providers import Generator


@dataclass(frozen=True)
class RouterResult:
    intent: str
    reply: str | None = None


SMALLTALK_RE = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good morning|good afternoon|good evening)[!. ]*\s*$", re.IGNORECASE)


def route_intent(question: str, settings: Settings, generator: Generator | None = None) -> RouterResult:
    if not settings.router_enabled:
        return RouterResult(intent="knowledge")
    if not question.strip():
        return RouterResult(intent="unsupported", reply="Ask a question about the Acme Storefront documentation.")
    if settings.generation_provider == "openai" and generator is not None:
        label = generator.generate(
            "Classify the user message as exactly one label: knowledge, smalltalk, unsupported.",
            question,
            max_tokens=8,
        ).strip().lower()
        if label in {"knowledge", "smalltalk", "unsupported"}:
            return RouterResult(intent=label, reply=_canned_reply(label))
    if SMALLTALK_RE.match(question):
        return RouterResult(intent="smalltalk", reply=_canned_reply("smalltalk"))
    return RouterResult(intent="knowledge")


def _canned_reply(intent: str) -> str | None:
    if intent == "smalltalk":
        return "Hi. Ask me about the Acme Storefront docs and I will answer with sources."
    if intent == "unsupported":
        return "I can answer questions grounded in the Acme Storefront documentation."
    return None
