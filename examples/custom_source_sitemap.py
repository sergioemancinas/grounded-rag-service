"""Custom ingestion Source: pull pages listed in a sitemap.

Implements the ``Source`` protocol from app/ingest.py: yield ``Document``
objects with stable ids. Chunking, embedding, and index writing stay in the
pipeline, so a new source never touches them.

Run it:

    pip install beautifulsoup4
    python scripts/build_index.py --source examples.custom_source_sitemap:SitemapSource \\
        --out data/index.jsonl
    SITEMAP_URL=https://docs.example/sitemap.xml python scripts/build_index.py \\
        --source examples.custom_source_sitemap:SitemapSource --out data/index.jsonl

Be a good citizen when crawling: honor robots.txt, set a real user agent,
and rate-limit. This example keeps a fixed delay between requests.
"""

from __future__ import annotations

import os
import time
from typing import Iterator
from xml.etree import ElementTree

from app.ingest import Document

USER_AGENT = "citespine-example-crawler/1.0"
REQUEST_DELAY_SECONDS = 1.0


class SitemapSource:
    """Yields one Document per URL listed in a sitemap.xml."""

    def __init__(self, sitemap_url: str | None = None) -> None:
        self.sitemap_url = sitemap_url or os.environ.get("SITEMAP_URL", "")
        if not self.sitemap_url:
            raise ValueError("Set SITEMAP_URL or pass sitemap_url=...")

    def _urls(self) -> list[str]:
        """Read the sitemap and return every <loc> it lists."""
        import httpx

        response = httpx.get(self.sitemap_url, headers={"User-Agent": USER_AGENT}, timeout=30.0)
        response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        return [node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text]

    def load(self) -> Iterator[Document]:
        """Fetch each page, strip it to text, and yield it as a Document."""
        import httpx
        from bs4 import BeautifulSoup

        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
            for url in self._urls():
                response = client.get(url)
                if response.status_code != 200:
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer"]):
                    tag.decompose()
                title = soup.title.get_text(strip=True) if soup.title else url
                text = "\n".join(line for line in soup.get_text("\n").splitlines() if line.strip())
                if text:
                    yield Document(
                        id=url,
                        text=text,
                        metadata={"title": title, "heading_path": [title]},
                        source_url=url,
                    )
                time.sleep(REQUEST_DELAY_SECONDS)
