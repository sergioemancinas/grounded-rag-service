"""Built-in stage implementations and their registry factories.

Every stage keeps a zero-dependency local default so the whole service runs
offline with an empty environment. Provider SDK imports stay lazy inside
method bodies, keeping ``openai`` optional. Built-ins register themselves in
app/registry.py via the ``@register_*`` decorators; the ``get_*`` resolvers
below check the dotted-path ``*_CLASS`` escape hatches first.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from app.config import Settings
from app.interfaces import Embedder, Generator, Reranker, Retriever
from app.registry import (
    EMBEDDERS,
    GENERATORS,
    RERANKERS,
    STORES,
    register_embedder,
    register_generator,
    register_reranker,
    register_store,
    resolve,
)
from app.rerank import CrossEncoderReranker, PassthroughReranker
from app.retrieval import Retriever as JSONLRetriever


TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")


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


@register_embedder("local")
def _local_embedder(settings: Settings) -> Embedder:
    del settings
    return LocalHashEmbedder()


@register_embedder("openai")
def _openai_embedder(settings: Settings) -> Embedder:
    return OpenAIEmbedder(settings.openai_api_key, settings.openai_embedding_model)


@register_generator("local")
def _local_generator(settings: Settings) -> Generator:
    del settings
    return LocalExtractiveGenerator()


@register_generator("openai")
def _openai_generator(settings: Settings) -> Generator:
    return OpenAIGenerator(settings.openai_api_key, settings.openai_generation_model)


@register_reranker("passthrough")
def _passthrough_reranker(settings: Settings) -> Reranker:
    del settings
    return PassthroughReranker()


@register_reranker("cross_encoder")
def _cross_encoder_reranker(settings: Settings) -> Reranker:
    del settings
    return CrossEncoderReranker()


@register_store("jsonl")
def _jsonl_retriever(settings: Settings) -> Retriever:
    return JSONLRetriever(settings.index_path, settings)


def get_embedder(settings: Settings) -> Embedder:
    """Resolve the embedder from EMBEDDER_CLASS or EMBEDDING_PROVIDER."""
    return resolve(settings.embedding_provider, settings.embedder_class, EMBEDDERS, settings, "embedder")


def get_generator(settings: Settings) -> Generator:
    """Resolve the generator from GENERATOR_CLASS or GENERATION_PROVIDER."""
    return resolve(settings.generation_provider, settings.generator_class, GENERATORS, settings, "generator")


def get_reranker(settings: Settings) -> Reranker:
    """Resolve the reranker from RERANKER_CLASS or the RERANK_ENABLED switch."""
    name = "cross_encoder" if settings.rerank_enabled else "passthrough"
    return resolve(name, settings.reranker_class, RERANKERS, settings, "reranker")


def get_retriever(settings: Settings) -> Retriever:
    """Resolve the retriever from RETRIEVER_CLASS or the 'jsonl' default."""
    return resolve("jsonl", settings.retriever_class, STORES, settings, "retriever")
