"""The adapter seam: one callable in, one router out.

An adapter is a translator with two sides. Inbound, it turns a platform
webhook into an :class:`~app.api_models.AskRequest` and calls the injected
``AskFn``. Outbound, it renders the returned markdown answer into whatever
the platform speaks and delivers it. The core never learns the platform
exists, and the adapter never imports pipeline internals.

Three rules hold for every adapter, and each exists because violating it
breaks in production rather than in tests:

1. **Verify signatures on the raw request bytes, before parsing.** Every
   platform signs the exact body it sent. Re-serializing parsed JSON changes
   whitespace and key order, so the recomputed digest will not match.
2. **Acknowledge fast, answer in the background.** Chat platforms retry
   webhooks they consider slow (Slack: 3 seconds), which turns one question
   into duplicate answers. Ack immediately, post a placeholder, then edit it
   with the real answer.
3. **Deduplicate retries locally.** Retry semantics are platform-specific,
   so the dedup window belongs in the adapter, not in the core.

Adapters that cannot run in this process (a different language, a serverless
function, an existing bot) are equally welcome: they call ``POST /v1/ask``
over HTTP and skip this module entirely. See app/channels/http_client.py for
the client-side ``AskFn`` that proves the two paths are interchangeable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.api_models import AskFn, AskRequest, AskResponse, FeedbackRequest

FeedbackFn = Callable[[FeedbackRequest], Awaitable[None]]
"""Records a user verdict; the in-process twin of ``POST /v1/feedback``."""

__all__ = ["AskFn", "AskRequest", "AskResponse", "FeedbackFn", "FeedbackRequest", "InboundMessage"]


@dataclass(frozen=True)
class InboundMessage:
    """A platform message normalized to what the core actually needs.

    Adapters build one of these from their webhook payload and map it onto an
    ``AskRequest``. Anything platform-specific that the adapter needs later
    (message timestamps, response URLs) rides along in ``raw`` rather than
    leaking into the core request shape.
    """

    text: str
    conversation_id: str = ""
    user_id: str = ""
    channel: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_request(self) -> AskRequest:
        """Map this message onto the core's request model."""
        return AskRequest(
            question=self.text,
            conversation_id=self.conversation_id or None,
            user_id=self.user_id or None,
            channel=self.channel,
        )
