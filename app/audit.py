"""Structured audit log for MCP tool invocations (OWASP MCP08).

Verified claims already reach the tool layer via ``current_subject()``; what
was missing was the sink. One JSON event per call is enough to reconstruct
who invoked what after an incident, without recording secrets or the raw
question text that would turn the audit trail into another disclosure path.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

from app.mcp_auth import current_subject

# Never log secrets, bearer tokens, or raw question text.
logger = logging.getLogger("grounded_rag.audit")

P = ParamSpec("P")
R = TypeVar("R")

# Argument names whose values are natural-language and must never appear raw.
_SENSITIVE_ARGS = frozenset({"query", "question", "text", "content"})


def _digest(value: str, key: bytes) -> str:
    """Keyed HMAC-SHA256 hex digest; same threat model as feedback digests."""
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def summarize_arguments(arguments: dict[str, Any], hmac_key: bytes) -> dict[str, object]:
    """Lengths and digests only — never the raw values of sensitive fields."""
    summary: dict[str, object] = {}
    for name, value in arguments.items():
        if isinstance(value, str):
            entry: dict[str, object] = {"length": len(value)}
            if name in _SENSITIVE_ARGS:
                entry["digest"] = _digest(value, hmac_key)
            summary[name] = entry
        elif isinstance(value, (int, float, bool)) or value is None:
            summary[name] = value
        else:
            summary[name] = {"type": type(value).__name__}
    return summary


_FALLBACK_KEY: bytes | None = None


def _resolve_key(hmac_key: str) -> bytes:
    """Key the argument digests, deriving a process-wide fallback if needed.

    The fallback is derived once per process rather than per decorated tool.
    A key per tool would digest the same question differently in the `search`
    event and the `ask` event, which defeats the point of the digest: joining
    a caller's activity across tools without storing what they asked.
    """
    if hmac_key:
        return hmac_key.encode("utf-8")
    global _FALLBACK_KEY
    if _FALLBACK_KEY is None:
        _FALLBACK_KEY = secrets.token_bytes(32)
        logger.info(
            "audit: FEEDBACK_HMAC_KEY unset, using a per-process key; digests will not be comparable across restarts"
        )
    return _FALLBACK_KEY


def audit_tool_call(
    tool_name: str,
    hmac_key: str = "",
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator that emits one structured audit event around a tool body.

    Preserves the wrapped function's name, docstring, and annotations so the
    MCP SDK still derives the correct input schema from the signature.
    """
    key = _resolve_key(hmac_key)

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            subject = current_subject() or "anonymous"
            # Bind to names for the summary; positional-only tools are rare
            # and still appear under their parameter names via signature bind.
            bound: dict[str, Any] = dict(kwargs)
            if args:
                try:
                    import inspect

                    parameters = list(inspect.signature(fn).parameters)
                    for index, value in enumerate(args):
                        if index < len(parameters):
                            bound.setdefault(parameters[index], value)
                except (TypeError, ValueError):
                    bound["_positional_count"] = len(args)

            started = time.perf_counter()
            outcome = "ok"
            error_class: str | None = None
            try:
                return fn(*args, **kwargs)
            except Exception as error:
                outcome = "error"
                error_class = type(error).__name__
                raise
            finally:
                duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
                event = {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "event": "mcp.tool_call",
                    "tool": tool_name,
                    "subject": subject,
                    "arguments": summarize_arguments(bound, key),
                    "outcome": outcome,
                    "duration_ms": duration_ms,
                }
                if error_class is not None:
                    event["error_class"] = error_class
                logger.info("%s", json.dumps(event, ensure_ascii=True, sort_keys=True))

        # functools.wraps copies __annotations__, but be explicit: the MCP SDK
        # builds the JSON schema from these and a silent drop would ship a
        # broken tool surface.
        wrapper.__annotations__ = dict(getattr(fn, "__annotations__", {}))
        return wrapper

    return decorator
