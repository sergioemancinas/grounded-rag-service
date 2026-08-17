"""Prompt loading and rendering.

Prompt texts live as markdown files in app/prompts/ and can be overridden
per deployment by pointing PROMPTS_DIR at a directory containing files with
the same names; packaged defaults are the fallback so tests stay
deterministic. Rendering uses ``string.Template.safe_substitute``, so
literal ``{``/``}`` in prompts (JSON examples) never break and unknown
``$placeholders`` are left intact.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from string import Template

from app.config import Settings

_PACKAGED_DIR = Path(__file__).resolve().parent / "prompts"


@cache
def _read_prompt(name: str, override_dir: str | None) -> str:
    if override_dir:
        candidate = Path(override_dir) / f"{name}.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return (_PACKAGED_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def load_prompt(name: str, settings: Settings | None = None, **variables: str) -> str:
    """Load prompt ``name`` (filename without .md) and substitute variables.

    Resolution order: ``settings.prompts_dir`` first, packaged defaults in
    app/prompts/ second. Variables are substituted as ``$name`` templates
    with ``safe_substitute``; prompts without variables pass through as-is.
    """
    override = None
    if settings is not None and settings.prompts_dir is not None:
        override = str(settings.prompts_dir)
    text = _read_prompt(name, override)
    if variables:
        return Template(text).safe_substitute(**variables)
    return text
