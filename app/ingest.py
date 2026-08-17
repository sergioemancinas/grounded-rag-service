"""Ingestion sources and the chunk/embed/index stage that consumes them.

A ``Source`` yields normalized :class:`Document` objects; everything after
that (identifier extraction, embedding, index record layout) is source
agnostic. The bundled :class:`MarkdownSource` ports the original markdown
chunker: heading-aware sectioning, tiny-section merging, and paragraph
chunking with a small overlap.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.providers import get_embedder
from app.retrieval import extract_identifiers

HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


@dataclass(frozen=True)
class Document:
    """One ingestible unit of text with stable identity and provenance."""

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_url: str = ""


class Source(Protocol):
    """Anything that can enumerate Documents for indexing."""

    def load(self) -> Iterator[Document]:
        """Yield Documents with stable ids; called once per index build.

        Sources that support incremental sync may additionally expose
        ``poll(since)``, but ``load`` alone is enough for build_index.
        """
        ...


@dataclass(frozen=True)
class MarkdownSection:
    heading_path: list[str]
    text: str


def read_sections(path: Path) -> tuple[str, list[MarkdownSection]]:
    """Parse a markdown file into heading-scoped sections plus its title."""
    sections: list[MarkdownSection] = []
    title = path.stem.replace("-", " ").title()
    heading_stack: dict[int, str] = {}
    current_path: list[str] = []
    current_lines: list[str] = []

    def flush() -> None:
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append(MarkdownSection(heading_path=list(current_path), text=text))

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        heading = HEADING_RE.match(raw_line)
        if heading:
            flush()
            current_lines = [raw_line]
            level = len(heading.group(1))
            heading_text = heading.group(2).strip()
            if level == 1:
                title = heading_text
            heading_stack[level] = heading_text
            for stale_level in list(heading_stack):
                if stale_level > level:
                    del heading_stack[stale_level]
            current_path = [heading_stack[index] for index in sorted(heading_stack)]
            continue
        current_lines.append(raw_line)
    flush()
    return title, sections


def chunk_section(section: MarkdownSection, target_chars: int = 1500) -> list[MarkdownSection]:
    """Split an oversized section on paragraph boundaries with small overlap."""
    if len(section.text) <= target_chars:
        return [section]
    chunks: list[MarkdownSection] = []
    paragraphs = [paragraph.strip() for paragraph in section.text.split("\n\n") if paragraph.strip()]
    current: list[str] = []
    for paragraph in paragraphs:
        proposed = "\n\n".join(current + [paragraph])
        if current and len(proposed) > target_chars:
            chunks.append(MarkdownSection(section.heading_path, "\n\n".join(current)))
            overlap = current[-1:] if len(current[-1]) < 350 else []
            current = overlap + [paragraph]
        else:
            current.append(paragraph)
    if current:
        chunks.append(MarkdownSection(section.heading_path, "\n\n".join(current)))
    return chunks


def merge_tiny_sections(sections: Iterable[MarkdownSection], min_chars: int = 450) -> list[MarkdownSection]:
    """Fold undersized sections into their successors to avoid fragment chunks."""
    merged: list[MarkdownSection] = []
    pending: MarkdownSection | None = None
    for section in sections:
        if pending is None:
            pending = section
            continue
        if len(pending.text) < min_chars:
            pending = MarkdownSection(
                heading_path=section.heading_path,
                text=pending.text.rstrip() + "\n\n" + section.text.lstrip(),
            )
        else:
            merged.append(pending)
            pending = section
    if pending is not None:
        merged.append(pending)
    return merged


def slugify(value: str) -> str:
    """Lowercase a heading path into a URL fragment slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


@dataclass
class MarkdownSource:
    """Documents from a directory tree of markdown files (the default source)."""

    docs_dir: Path = Path("data/sample_docs")
    base_url: str = "https://docs.acme-storefront.example"

    def load(self) -> Iterator[Document]:
        """Yield one Document per chunked section, ids stable as doc:index."""
        for path in sorted(self.docs_dir.rglob("*.md")):
            doc_id = path.stem
            title, sections = read_sections(path)
            chunked_sections: list[MarkdownSection] = []
            for section in merge_tiny_sections(sections):
                chunked_sections.extend(chunk_section(section))
            for index, section in enumerate(chunked_sections, start=1):
                heading_slug = slugify("-".join(section.heading_path))
                yield Document(
                    id=f"{doc_id}:{index}",
                    text=section.text,
                    metadata={
                        "doc_id": doc_id,
                        "title": title,
                        "heading_path": section.heading_path,
                    },
                    source_url=f"{self.base_url}/{doc_id}#{heading_slug}",
                )


def build_records(
    source: Source,
    settings: Settings,
    source_id: str = "",
) -> list[dict[str, object]]:
    """Chunk-agnostic embed+index stage: Documents in, JSONL records out.

    ``source_id`` is recorded on every chunk so an answer can be traced to the
    ingestion source that produced its text; without it a poisoned document
    is indistinguishable from a trusted one after the fact.
    """
    embedder = get_embedder(settings)
    documents = list(source.load())
    texts = [document.text for document in documents]
    embeddings = embedder.embed(texts) if texts else []
    resolved_source = source_id or source_identifier(source)
    records: list[dict[str, object]] = []
    for document, embedding in zip(documents, embeddings, strict=True):
        records.append(
            {
                "id": document.id,
                "doc_id": str(document.metadata.get("doc_id", document.id)),
                "title": str(document.metadata.get("title", "")),
                "heading_path": list(document.metadata.get("heading_path", [])),
                "url": document.source_url,
                "source": resolved_source,
                "source_url": document.source_url,
                "text": document.text,
                "identifiers": extract_identifiers(document.text),
                "embedding": embedding,
            }
        )
    return records


def source_identifier(source: Source) -> str:
    """Stable identifier for the source that produced the documents."""
    if isinstance(source, MarkdownSource):
        return f"markdown:{source.docs_dir}"
    return f"{type(source).__module__}:{type(source).__qualname__}"
