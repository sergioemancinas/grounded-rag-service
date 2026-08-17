from __future__ import annotations

import json
import re
from typing import Sequence

from app.config import Settings
from app.interfaces import GroundingJudge, GroundingResult
from app.prompts import load_prompt
from app.registry import GROUNDING_JUDGES, register_grounding_judge, resolve
from app.retrieval import ScoredChunk, tokenize


__all__ = [
    "GroundingJudge",
    "GroundingResult",
    "HeuristicGroundingJudge",
    "OpenAIGroundingJudge",
    "get_grounding_judge",
]


class HeuristicGroundingJudge:
    def judge(self, answer: str, chunks: Sequence[ScoredChunk]) -> GroundingResult:
        if not chunks:
            return GroundingResult(score=0.0, verdict="unsupported", reasons=["No context chunks were provided."])
        context_tokens = [set(tokenize(chunk.chunk.text)) for chunk in chunks]
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer)
            if len(tokenize(sentence)) >= 4 and not sentence.lower().startswith("offline mode")
        ]
        if not sentences:
            return GroundingResult(score=0.0, verdict="unsupported", reasons=["No factual sentences were found."])

        supported = 0
        for sentence in sentences:
            sentence_tokens = set(tokenize(sentence))
            if not sentence_tokens:
                continue
            best_overlap = max(
                (len(sentence_tokens & tokens) / len(sentence_tokens) for tokens in context_tokens),
                default=0.0,
            )
            if best_overlap >= 0.30:
                supported += 1
        score = supported / len(sentences)
        verdict = "supported" if score >= 0.55 else "weak"
        reasons = [f"{supported} of {len(sentences)} answer sentences overlap substantially with context."]
        return GroundingResult(score=score, verdict=verdict, reasons=reasons)


class OpenAIGroundingJudge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: object | None = None

    @property
    def client(self) -> object:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.settings.openai_api_key)
        return self._client

    def judge(self, answer: str, chunks: Sequence[ScoredChunk]) -> GroundingResult:
        context = "\n\n".join(
            f"SOURCE [{index}]\n{chunk.chunk.text[: self.settings.max_context_chars_per_chunk]}"
            for index, chunk in enumerate(chunks, start=1)
        )
        prompt = load_prompt("grounding_judge", self.settings, sources=context, answer=answer)
        try:
            response = self.client.chat.completions.create(  # type: ignore[attr-defined]
                model=self.settings.openai_expansion_model,
                messages=[
                    {"role": "system", "content": "You are a strict faithfulness judge."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            score = float(parsed.get("score", 0.0))
            verdict = str(parsed.get("verdict", "unknown"))
            reasons_raw = parsed.get("reasons", [])
            reasons = [str(item) for item in reasons_raw] if isinstance(reasons_raw, list) else [str(reasons_raw)]
            return GroundingResult(score=max(0.0, min(score, 1.0)), verdict=verdict, reasons=reasons)
        except Exception as exc:
            return GroundingResult(score=0.0, verdict="judge_error", reasons=[str(exc)])


@register_grounding_judge("heuristic")
def _heuristic_judge(settings: Settings) -> GroundingJudge:
    del settings
    return HeuristicGroundingJudge()


@register_grounding_judge("llm")
def _llm_judge(settings: Settings) -> GroundingJudge:
    return OpenAIGroundingJudge(settings)


def get_grounding_judge(settings: Settings) -> GroundingJudge:
    """Resolve the judge from GROUNDING_JUDGE_CLASS, GROUNDING_JUDGE, or auto.

    Auto-selection (empty GROUNDING_JUDGE) picks the LLM judge only when the
    generation provider is OpenAI with a key present, else the heuristic.
    """
    name = settings.grounding_judge
    if not name:
        use_llm = settings.generation_provider == "openai" and bool(settings.openai_api_key)
        name = "llm" if use_llm else "heuristic"
    return resolve(name, settings.grounding_judge_class, GROUNDING_JUDGES, settings, "grounding judge")
