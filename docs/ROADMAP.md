# Roadmap

What this project does next, why, and how each item will be judged done.

The ordering principle is evidence density: each milestone exists to answer a
question a reviewer would actually ask, and each produces an artifact that can
be checked rather than a claim that must be believed.

## Contents

1. [Current state](#current-state)
2. [M1 — Measure the served path](#m1--measure-the-served-path)
3. [M2 — Resolve the hybrid retrieval question](#m2--resolve-the-hybrid-retrieval-question)
4. [M3 — Test injection resistance](#m3--test-injection-resistance)
5. [M4 — Second evaluation dataset](#m4--second-evaluation-dataset)
6. [M5 — Runnable evidence](#m5--runnable-evidence)
7. [Explicit non-goals](#explicit-non-goals)
8. [Claim discipline](#claim-discipline)

## Current state

Implemented and measured: a channel-agnostic RAG service with hybrid
retrieval, a grounding gate, optional Slack and MCP front ends, OAuth 2.1
resource-server authorization, rate limiting, index integrity checking, and
tool-call auditing. Retrieval quality is measured on BEIR SciFact and reported
in [EVALUATION.md](EVALUATION.md), including a result unfavourable to the
project's own architecture.

Open questions, in the order they damage credibility:

| # | Question | Status |
| --- | --- | --- |
| 1 | Do the published numbers measure the code the service runs? | Addressed in M1 |
| 2 | Why keep hybrid retrieval when it loses to dense-only? | Addressed in M2 |
| 3 | Does the system actually resist prompt injection? | Addressed in M3 |
| 4 | Does one dataset generalize? | Addressed in M4 |
| 5 | Can a reader see it work without cloning? | Addressed in M5 |

## M1 — Measure the served path

**Objection defused:** "Is 0.6047 the number your service actually produces?"

The benchmark harness scores the retrieval lanes as components. It calls the
ranking helpers and the fusion function directly, so identifier injection,
per-document deduplication, MMR, and the second fusion across expanded query
phrasings are excluded. Those stages are part of the served path, so the
published figures describe a configuration the service does not run.

Tasks:

- Add a `pipeline` lane to `scripts/eval_beir.py` that calls the public
  `Retriever.retrieve()` end to end.
- Publish its score beside the component lanes, whatever it turns out to be.
- State in [EVALUATION.md](EVALUATION.md) which lanes are component
  measurements and which is the served path.

Acceptance criteria:

- The results tables contain a lane whose numbers come from
  `Retriever.retrieve()`.
- No table caption implies a component measurement is an end-to-end one.
- The distinction is stated in prose, not only implied by lane names.

## M2 — Resolve the hybrid retrieval question

**Objection defused:** "Your headline feature loses to a simpler baseline.
Why is it still the default?"

Equal-weight reciprocal rank fusion gives a lane scoring 0.1557 the same vote
as one scoring 0.6047, which is sufficient to explain the observed regression
without any implementation fault. Two things follow, and both are measurable.

First, fusion weight should be selected on a held-out split rather than fixed
at parity. SciFact ships a train split of 809 queries that the reported test
split does not touch, so a weight can be chosen honestly and reported without
contaminating the test result.

Second, the architectural justification for hybrid retrieval — that exact
identifiers defeat pure dense retrieval — has never been measured. It can be,
without authoring any data: the repository's own identifier regular expression
partitions a public benchmark's queries into strata, and per-stratum scores
then either support the claim or refute it.

Tasks:

- Add lane weighting to fusion, with the weight selected on the train split
  and reported on test. Do not retune the rank constant; keep it at its
  published default and say why.
- Add identifier-based query stratification to the harness, using the existing
  `IDENTIFIER_RE`. Commit the stratum definition before running the
  measurement.
- Publish both tables, including the case where a stratum contradicts the
  architectural claim.
- Correct any document still asserting that fusion is unconditionally better
  than a single lane.

Acceptance criteria:

- The weight-selection split and the reporting split are named, distinct, and
  stated in the document.
- Per-stratum results are published for both embedders.
- The architectural claim in [ARCHITECTURE.md](ARCHITECTURE.md) is stated
  conditionally, matching what the numbers support.
- Identifier matching that fires on a large fraction of the corpus is bounded,
  so the mechanism cannot dominate ranking through breadth alone.

## M3 — Test injection resistance

**Objection defused:** "You claim the system resists prompt injection. Show
me."

This is the only security claim in the [threat model](THREAT-MODEL.md) resting
on an argument rather than a test. Two constraints shape the design.

The first is an oracle problem. Faithfulness to retrieved context is exactly
what a successful indirect injection satisfies, so a grounding score cannot
serve as the definition of "blocked". Success has to be defined as the absence
of the attacker's observable effect in the delivered answer.

The second is that any measurement involving a real model cannot gate
continuous integration on a public repository, because the credentials cannot
be exposed to pull requests from forks. The measurement therefore runs on a
schedule and publishes a dated artifact; the build gate stays deterministic.

Tasks:

- Structural tests first, deterministic and credential-free: retrieved content
  cannot forge a citation marker, cannot reach a privileged position in the
  prompt, and is delimited from instructions.
- An attack corpus drawn from a permissively licensed public source, vendored
  with provenance recorded per entry.
- A harness reporting attack success rate with a confidence interval, and for
  a zero result, the upper bound that a sample of that size actually supports.
- A scheduled workflow, never triggered by pull requests, never a build gate,
  writing a dated result artifact.

Acceptance criteria:

- The structural suite runs in the existing offline job with no network.
- The published claim names the model, the date, the number of trials, and the
  interval. A zero result is reported as a bound, not as immunity.
- The threat model's LLM01 entry cites the artifact instead of an argument.

## M4 — Second evaluation dataset

**Objection defused:** "One dataset, and it's claim verification. Does this
generalize?"

A second licence-clean dataset, ideally one whose queries resemble technical
documentation search more closely than scientific claim verification. The
result is published whether or not it flatters the system.

Acceptance criteria:

- The dataset's licence is recorded next to its results.
- Both datasets are reported; neither is dropped for being unfavourable.
- The interpretation states what two datasets do and do not establish.

## M5 — Runnable evidence

**Objection defused:** "Can I see it work without cloning it?"

A hosted demo is deliberately not planned. The only configuration with
structurally zero cost is the offline stack, whose generator returns extractive
text and whose default embedder is close to noise — a live endpoint would
showcase the weakest configuration to a reader who judges output rather than
architecture, and an unauthenticated public endpoint calling a paid model is a
denial-of-wallet target.

Instead, evidence a reader can inspect without running anything:

- Extend the existing container job so its log contains a real, complete
  response, and link that run.
- A short terminal recording of the offline stack answering a grounded
  question and declining an out-of-corpus one.

Acceptance criteria:

- A reader with neither Docker nor Python can see a genuine response and its
  citations.
- The recording shows both a grounded answer and a refusal.

## Explicit non-goals

Deliberate omissions, each with the reason:

| Not doing | Why |
| --- | --- |
| Document-level authorization | Without an identity provider it enforces nothing while appearing to. The cache boundary that would be required already exists. |
| A hosted public demo | Showcases the weakest configuration; unauthenticated inference is a spending risk. See M5. |
| Gating the build on an injection threshold | Requires credentials unavailable to fork pull requests; a flaky security gate is worse than a documented gap. |
| Retuning the rank constant on the test split | It would improve the headline number by optimizing against the data used to report it. |
| A vector database or numpy in the core | Pure-Python retrieval is what keeps the code readable, which is the point of the project. |
| A web interface | The core is an API; front ends are adapters and belong outside it. |

## Claim discipline

Every published number states the code path that produced it, the split it was
measured on, and the date. Parameters selected on a split are never reported on
that same split. Security controls are described as mitigated only where a test
covers them; everything else is marked partial or absent, with the gap named.

A result that undermines a design decision is published in the same place as
one that supports it.
