from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.providers import get_embedder
from app.retrieval import extract_identifiers


HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


@dataclass(frozen=True)
class MarkdownSection:
    heading_path: list[str]
    text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a JSONL retrieval index from markdown docs.")
    parser.add_argument("--docs", type=Path, default=Path("data/sample_docs"))
    parser.add_argument("--out", type=Path, default=Path("data/index.jsonl"))
    return parser.parse_args()


def read_sections(path: Path) -> tuple[str, list[MarkdownSection]]:
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
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def build_records(docs_dir: Path, settings: Settings) -> list[dict[str, object]]:
    embedder = get_embedder(settings)
    records: list[dict[str, object]] = []
    for path in sorted(docs_dir.rglob("*.md")):
        doc_id = path.stem
        title, sections = read_sections(path)
        chunked_sections: list[MarkdownSection] = []
        for section in merge_tiny_sections(sections):
            chunked_sections.extend(chunk_section(section))
        texts = [section.text for section in chunked_sections]
        embeddings = embedder.embed(texts) if texts else []
        for index, (section, embedding) in enumerate(zip(chunked_sections, embeddings), start=1):
            heading_slug = slugify("-".join(section.heading_path))
            identifiers = extract_identifiers(section.text)
            records.append(
                {
                    "id": f"{doc_id}:{index}",
                    "doc_id": doc_id,
                    "title": title,
                    "heading_path": section.heading_path,
                    "url": f"https://docs.acme-storefront.example/{doc_id}#{heading_slug}",
                    "text": section.text,
                    "identifiers": identifiers,
                    "embedding": embedding,
                }
            )
    return records


def main() -> None:
    args = parse_args()
    settings = Settings(index_path=args.out)
    records = build_records(args.docs, settings)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    print(f"Wrote {len(records)} chunks to {args.out}")


if __name__ == "__main__":
    main()
