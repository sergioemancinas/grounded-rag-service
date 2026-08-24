"""The grounding gate's regenerate-then-caveat flow.

This control is named in the README, the architecture document, and the threat
model, and until these tests existed the entire suite passed with the block
deleted. A headline safety control with no coverage is a claim, not a feature.
"""

from __future__ import annotations

from app.config import Settings
from app.interfaces import GroundingResult
from app.pipeline import answer_question

LOW = 0.2
HIGH = 0.9


class ScriptedJudge:
    """Returns a predetermined sequence of faithfulness scores."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)
        self.calls = 0

    def judge(self, answer: str, chunks: object) -> GroundingResult:
        del answer, chunks
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        return GroundingResult(score=score, verdict="scripted", reasons=[])


class RecordingGenerator:
    """Captures the system prompt of every generation call."""

    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        del user, max_tokens
        self.system_prompts.append(system)
        return f"answer {len(self.system_prompts)}"


def run(fake_deps, judge: ScriptedJudge, generator: RecordingGenerator):
    fake_deps.generator = generator
    fake_deps.grounding_judge = judge
    settings = Settings(grounding_check_enabled=True, grounding_min_score=0.55, router_enabled=False)
    return answer_question("Which values does fulfillment_type accept?", history=[], settings=settings, deps=fake_deps)


def test_answer_above_threshold_is_delivered_unchanged(fake_deps) -> None:
    judge, generator = ScriptedJudge([HIGH]), RecordingGenerator()

    result = run(fake_deps, judge, generator)

    assert len(generator.system_prompts) == 1, "a faithful answer must not be regenerated"
    assert judge.calls == 1
    assert not result.answer.lower().startswith("low confidence")


def test_weak_answer_is_regenerated_once_under_a_stricter_prompt(fake_deps) -> None:
    judge, generator = ScriptedJudge([LOW, HIGH]), RecordingGenerator()

    result = run(fake_deps, judge, generator)

    assert len(generator.system_prompts) == 2, "a weak answer must trigger exactly one retry"
    assert generator.system_prompts[0] != generator.system_prompts[1], "the retry must use a different prompt"
    assert result.answer == "answer 2"
    assert not result.answer.lower().startswith("low confidence")


def test_persistently_weak_answer_ships_with_a_caveat(fake_deps) -> None:
    judge, generator = ScriptedJudge([LOW, LOW]), RecordingGenerator()

    result = run(fake_deps, judge, generator)

    assert len(generator.system_prompts) == 2, "the gate must not retry more than once"
    assert result.answer.lower().startswith("low confidence")
    assert result.grounding is not None and result.grounding.score == LOW


def test_gate_can_be_disabled(fake_deps) -> None:
    judge, generator = ScriptedJudge([LOW]), RecordingGenerator()
    fake_deps.generator = generator
    fake_deps.grounding_judge = judge
    settings = Settings(grounding_check_enabled=False, router_enabled=False)

    result = answer_question("q", history=[], settings=settings, deps=fake_deps)

    assert judge.calls == 0
    assert result.grounding is None
    assert not result.answer.lower().startswith("low confidence")
