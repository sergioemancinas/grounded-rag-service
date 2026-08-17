"""Custom Generator: answer generation through the Anthropic API.

Implements the ``Generator`` protocol from app/interfaces.py. The circuit
breaker in app/pipeline.py wraps whatever you plug in here, so transient
provider failures are already handled for you.

Run it:

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    export GENERATOR_CLASS=examples.custom_generator_anthropic:ClaudeGenerator
    uvicorn app.main:app --port 8000

Retrieval stays local: only generation moves to the remote provider.
"""

from __future__ import annotations

import os

from app.config import Settings

MODEL_NAME = "claude-sonnet-5"


class ClaudeGenerator:
    """Generator backed by the Anthropic Messages API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings
        self.model = os.environ.get("ANTHROPIC_MODEL", MODEL_NAME)
        self._client = None

    def _get_client(self):
        """Create the SDK client lazily so the import stays optional."""
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        return self._client

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        """Return the model's answer as markdown text."""
        response = self._get_client().messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
