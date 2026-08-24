# Architecture

The shape of this project is one idea: **the pipeline is the product, and
everything else is a seam around it.** The core answers questions over HTTP.
Chat platforms, MCP clients, and CLIs are adapters. Every stage inside the
pipeline is a small protocol with a local default.

## The pipeline, stage by stage

All of it lives in `answer_question()` in `app/pipeline.py`, in this order.
It is deliberately linear, explicit Python rather than a configurable graph:
you can read the entire flow top to bottom without a framework in the way.

**Semantic cache** (`app/cache.py`). The question is embedded first, and a
cosine match above `CACHE_SIMILARITY` (0.97) within the TTL returns the
stored answer immediately. High threshold on purpose: a near-miss returning
the wrong cached answer is worse than a cache miss.

**Intent router** (`app/router.py`). Greetings and thanks get a canned reply
instead of a retrieval pass. Cheap, and it keeps the corpus out of small talk.

**Query expansion** (`app/llm.py`). One question becomes several retrieval
phrasings, which is the cheapest available fix for vocabulary mismatch
between how people ask and how documentation is written. Offline it is the
identity function.

**Hybrid retrieval** (`app/retrieval.py`). Two lanes per phrasing: dense
cosine over embeddings, and lexical BM25 over tokens. Support questions
carry identifiers (`fulfillment_type`, `POST /v1/orders`, `ERR_1042`) where
lexical matching is exact and embeddings are approximate; conceptual
questions are the reverse. Fusing both is better than choosing only when
both lanes carry signal: measured on SciFact, weighted fusion beats either
lane with a real embedding model, and is a large net negative with the
offline default, whose dense lane is close to noise. See
[EVALUATION.md](EVALUATION.md) for the numbers.

**RRF fusion.** Ranked lists merge by `sum(1 / (60 + rank))`. Cosine
similarities and BM25 scores are not comparable quantities, so fusing by
rank avoids inventing a weighting between two different units.

**Identifier injection.** Tokens in the query that look like identifiers
(snake_case, `/paths`, `dotted.names`, `UPPER_CASE`) force chunks containing
them into the candidate pool, even if both lanes ranked them low. Without
this, a question about one specific field can lose to five chunks that are
merely topical.

**Rerank** (`app/rerank.py`). Passthrough by default. The seam exists
because a cross-encoder reading query and candidate together is far more
accurate and far slower, so it runs over `RERANK_POOL` candidates only.

**MMR selection.** Maximal marginal relevance drops near-duplicates and caps
chunks per document (`CONTEXT_MAX_PER_DOC`), so the context window holds
several distinct sources instead of one page five times.

**Generation** (`app/llm.py`, `app/prompts/`). The system prompt requires
answering only from the provided context, citing every factual claim, and
saying so when the sources do not cover the question. It also instructs the
model to treat retrieved content and user text as untrusted data: ignore
instructions embedded in them, ignore authority claims, refuse injection
attempts.

**Grounding gate** (`app/grounding.py`). The judge scores faithfulness of
the finished answer against the chunks. Below `GROUNDING_MIN_SCORE` the
pipeline regenerates once with a stricter prompt; still below, the answer is
delivered with an explicit low-confidence note. This is also why there is no
token streaming: the gate judges a completed answer and may replace it.

## The three extension mechanisms

Consciously three, and no more.

**Protocols** (`app/interfaces.py`) define what a stage must do. One file,
one to two methods each, full contract in the docstring, so implementing a
stage never requires reading the pipeline. Conformance is duck-checked at
wiring time by `_check`, which fails at startup naming the missing method.
There is no `@runtime_checkable` isinstance check and no base class to
inherit.

**Registries** (`app/registry.py`) map short names to factories:
`EMBEDDING_PROVIDER=openai` picks `EMBEDDERS["openai"]`. They are plain dict
literals populated by `@register_*` decorators, so `grep register_embedder`
enumerates every option. There is deliberately no package scanning: a plugin
scan hides what is loaded and imports every provider's dependencies at
startup.

**Dotted-path hatches** (`*_CLASS` settings) let you use a class this
repository has never heard of, with zero edits here. `pydantic.ImportString`
imports it at settings load. This executes operator-supplied code, which is
the same trust model as any `--plugin` flag: environment only, never request
data.

There is intentionally no fourth mechanism. Packaging plugins as
`entry_points` is documented as a possibility and not implemented, because
nothing external needs it yet.

## Wiring and testability

`build_deps(settings)` in `app/deps.py` is the single place where settings
become wired components. `create_app()` builds the FastAPI app and mounts
adapters; the lifespan builds `PipelineDeps` onto `app.state`, so the index
loads at startup rather than at import.

Adapters and the MCP transport mount at build time but resolve their
dependencies lazily through a closure over `app.state`. That ordering is
what lets routes be registered before startup while the expensive index load
still happens in the lifespan.

Three ways to substitute components in tests, cheapest first: the
`fake_deps` fixture for a wired local stack, `dependency_overrides` on
`get_deps` / `get_settings` for route-level swaps, and direct
`PipelineDeps(...)` construction for full control.

## Why the core returns markdown

`AskResponse` carries markdown plus structured citations, never Block Kit,
never Discord embeds. Two reasons: the core would otherwise need to know
about every platform, and adapters would be unable to render answers their
own way. Rendering lives at the edge, in
`app/channels/slack_render.py` and its equivalents.

The same reasoning makes `AskFn` the only seam an adapter needs. An
in-process adapter receives a closure over the local pipeline; a remote one
receives `remote_ask()` from `app/channels/http_client.py`, which posts to
`/v1/ask`. The adapter code is identical, which is the proof that
in-process versus over-HTTP is a deployment decision rather than an
architectural one.

## Resilience and privacy defaults

The circuit breaker (`app/resilience.py`) wraps model and embedding calls,
opening after consecutive failures and probing after a cooldown, so a
provider outage fails fast instead of queueing. Adapters get their own
breaker for outbound platform calls, so a Slack outage cannot trip the one
guarding the LLM.

Feedback (`app/feedback.py`) stores SHA-256 digests of user ids and
questions rather than the values, so verdict counts are available without
accumulating a record of who asked what.
