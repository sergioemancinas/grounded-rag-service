from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings


TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class Generator(Protocol):
    def generate(self, system: str, user: str, max_tokens: int) -> str:
        ...


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        return values
    return [value / norm for value in values]


@dataclass
class LocalHashEmbedder:
    dimensions: int = 256

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in _tokens(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[bucket] += sign
            vectors.append(_l2_normalize(vector))
        return vectors


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, batch_size: int = 64) -> None:
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self._client: object | None = None

    @property
    def client(self) -> object:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch)  # type: ignore[attr-defined]
            embeddings.extend([list(item.embedding) for item in response.data])
        return embeddings


class LocalExtractiveGenerator:
    source_re = re.compile(r"SOURCE \[(\d+)\][^\n]*\n(.*?)(?=\nSOURCE \[|\Z)", re.DOTALL)

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        del system, max_tokens
        matches = self.source_re.findall(user)
        if not matches:
            return "Offline mode extractive answer: the provided sources do not contain enough information to answer."

        bullets: list[str] = []
        for source_number, text in matches:
            excerpt = self._excerpt(text)
            if excerpt:
                bullets.append(f"- {excerpt} [{source_number}]")
        if not bullets:
            return "Offline mode extractive answer: the provided sources do not contain enough information to answer."
        return "Offline mode extractive answer:\n" + "\n".join(bullets)

    def _excerpt(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) <= 900:
            return cleaned
        boundary = cleaned.rfind(". ", 0, 900)
        if boundary < 180:
            boundary = 900
        return cleaned[: boundary + 1].strip()


class OpenAIGenerator:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client: object | None = None

    @property
    def client(self) -> object:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        response = self.client.chat.completions.create(  # type: ignore[attr-defined]
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        return content or ""


def get_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "openai":
        return OpenAIEmbedder(settings.openai_api_key, settings.openai_embedding_model)
    return LocalHashEmbedder()


def get_generator(settings: Settings) -> Generator:
    if settings.generation_provider == "openai":
        return OpenAIGenerator(settings.openai_api_key, settings.openai_generation_model)
    return LocalExtractiveGenerator()
