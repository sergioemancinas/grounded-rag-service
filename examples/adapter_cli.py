"""The smallest possible channel adapter: your terminal.

Proves the adapter seam without any platform at all. It builds the same
``AskFn`` the HTTP API and the Slack adapter use, then renders answers as
plain text. Runs fully offline with no credentials.

Run it:

    python examples/adapter_cli.py
    python examples/adapter_cli.py "How do refunds work?"

Point it at a remote service instead of running the pipeline in-process:

    GROUNDED_RAG_URL=https://rag.example.com python examples/adapter_cli.py

That switch is the whole argument for the AskFn seam: in-process and
over-HTTP adapters are the same code with a different callable.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api_models import AskRequest, AskResponse
from app.config import Settings
from app.deps import build_deps
from app.main import run_ask


def local_ask(settings: Settings):
    """Build an in-process AskFn over the local pipeline."""
    deps = build_deps(settings)

    async def ask(payload: AskRequest) -> AskResponse:
        return run_ask(payload, settings, deps)

    return ask


def render(response: AskResponse) -> str:
    """Format an answer for a terminal."""
    lines = [response.answer, ""]
    if response.sources:
        lines.append("Sources")
        for index, source in enumerate(response.sources, start=1):
            lines.append(f"  [{index}] {source.title} - {source.url}")
    if response.grounding:
        lines.append(f"\ngrounding: {response.grounding.score:.2f} (passed={response.grounding.passed})")
    return "\n".join(lines)


async def main() -> None:
    settings = Settings()
    remote = os.environ.get("GROUNDED_RAG_URL", "")
    if remote:
        from app.channels.http_client import remote_ask

        ask = remote_ask(remote, token=os.environ.get("GROUNDED_RAG_TOKEN", ""))
    else:
        ask = local_ask(settings)

    if len(sys.argv) > 1:
        print(render(await ask(AskRequest(question=" ".join(sys.argv[1:]), channel="cli"))))
        return

    print("Ask a question, or press Ctrl-C to quit.")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if question:
            print("\n" + render(await ask(AskRequest(question=question, channel="cli"))))


if __name__ == "__main__":
    asyncio.run(main())
