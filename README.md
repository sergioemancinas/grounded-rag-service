# grounded-rag-service

[![CI](https://github.com/sergioemancinas/grounded-rag-service/actions/workflows/ci.yml/badge.svg)](https://github.com/sergioemancinas/grounded-rag-service/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A grounded, citation-first retrieval-augmented generation service. The core is
a channel-agnostic HTTP API; Slack, MCP, and command-line front ends are
optional adapters over a single seam. It runs end to end with no credentials
and no network, and its retrieval quality is measured on a public benchmark
rather than asserted.

Roughly 3,600 lines of application code, deliberately explicit and meant to be
read, forked, and modified.

## Contents

1. [Architecture](#architecture) — the answer path, stage by stage
2. [Getting started](#getting-started) — running it with no credentials
3. [Retrieval evaluation](#retrieval-evaluation) — measured results on a public benchmark
   - [Offline default](#offline-default)
   - [Real embedder](#real-embedder)
   - [Interpretation](#interpretation)
4. [HTTP API](#http-api) — endpoints and request handling
5. [Repository layout](#repository-layout) — where each concern lives
6. [Extension points](#extension-points) — replacing any stage
7. [Design rationale](#design-rationale) — why the pipeline is shaped this way
8. [Security](#security) — threat model and implemented controls
9. [Scope](#scope) — what this deliberately does not do
10. [Acknowledgments](#acknowledgments)
11. [License](#license)

## Architecture

The answer path is a single linear function, `answer_question()` in
`app/pipeline.py`:

```text
question
  -> semantic cache        skip retrieval and generation on a near-identical question
  -> intent router         documentation question, or conversational filler
  -> query expansion       one question becomes several retrieval phrasings
  -> hybrid retrieval      dense cosine and BM25, per phrasing
  -> RRF fusion            merge ranked lists without comparing raw scores
  -> identifier injection  force exact matches for snake_case names and API paths
  -> rerank                optional cross-encoder over the candidate pool
  -> MMR selection         drop near-duplicates, cap chunks per document
  -> generation            answer only from context, cite every claim
  -> grounding gate        score faithfulness, regenerate once, then caveat
  -> cited markdown answer
```

Each stage is a small Protocol defined in `app/interfaces.py` and selected at
startup, so any one of them can be replaced without touching the others. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Getting started

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/build_index.py --docs data/sample_docs --out data/index.jsonl
uvicorn app.main:app --port 8000
```

```bash
curl -s localhost:8000/v1/ask -H 'content-type: application/json' \
  -d '{"question":"Which values does fulfillment_type accept?"}' \
  | jq '{answer, sources: [.sources[].title], grounding}'
```

No environment variables and no API keys are required. The default stack uses
deterministic local embeddings and extractive generation; the bundled corpus is
fictional API documentation for a platform that does not exist.

Other entry points:

```bash
python examples/adapter_cli.py "Which values does fulfillment_type accept?"
python scripts/smoke_query.py "Which values does fulfillment_type accept?"
python scripts/eval_beir.py --lanes bm25,dense,hybrid
python -m pytest
```

## Retrieval evaluation

Measured on [BEIR SciFact](https://arxiv.org/abs/2104.08663), 300 test queries
over 5,183 documents, using this repository's own retrieval code.

### Offline default

`LocalHashEmbedder`, no additional dependencies:

| Lane | nDCG@10 | Recall@10 | MRR@10 |
| --- | --- | --- | --- |
| BM25 only | 0.6047 | 0.7262 | 0.5697 |
| Dense only | 0.1557 | 0.2381 | 0.1322 |
| Hybrid, RRF | 0.3756 | 0.5361 | 0.3322 |

### Real embedder

`BAAI/bge-small-en-v1.5` via fastembed (`pip install -e ".[eval]"`):

| Lane | nDCG@10 | Recall@10 | MRR@10 |
| --- | --- | --- | --- |
| BM25 only | 0.6047 | 0.7262 | 0.5697 |
| Dense only | 0.7200 | 0.8452 | 0.6845 |
| Hybrid, RRF | 0.6783 | 0.7783 | 0.6528 |

### Interpretation

Hybrid retrieval is not unconditionally better than its parts. With the offline
hash embedder it is a 38% relative regression against BM25 alone, because
reciprocal rank fusion blends a strong lexical lane with a dense lane that
carries almost no semantic signal. With a real embedding model the ordering
changes: hybrid improves on BM25 by about 12% relative, but still trails
dense-only retrieval, since the same untuned fusion constant now mixes a
stronger dense ranking with a weaker lexical one. No fusion parameters were
tuned against this split.

The published BEIR reference for SciFact BM25 is nDCG@10 0.665, obtained with a
different implementation. It is shown for orientation and is not reproduced
here.

The three-document sample corpus is a smoke test, not an evaluation: it cannot
express a regression of this size, which is why a public benchmark was adopted.
Methodology, licensing, and limitations are in
[docs/EVALUATION.md](docs/EVALUATION.md).

## HTTP API

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/ask` | Full pipeline; returns a markdown answer with citations, sources, grounding score, timings, and a request id |
| `POST /v1/search` | Retrieval only; returns ranked corpus chunks without generation |
| `POST /v1/feedback` | Records a verdict against a previously returned request id |
| `GET /health` | Liveness, resolved provider names, and chunk count |

`API_AUTH_TOKEN` enables a static bearer on `/v1`. Platform signature
verification belongs to adapters, never to the core. A per-caller rate limit
and a maximum question length are enabled by default.

Streaming is intentionally absent. The grounding gate evaluates a completed
answer and may replace it, which token streaming cannot express, and chat
adapters render a placeholder and edit it rather than consuming an event
stream.

## Repository layout

```text
app/
  main.py          core service: /v1 routes, lifespan wiring, adapter mounts
  pipeline.py      answer_question(): the full flow, in order
  interfaces.py    every stage Protocol, with its contract in the docstring
  registry.py      name-to-factory tables and the dotted-path escape hatch
  deps.py          build_deps(): where settings become wired components
  retrieval.py     JSONL index, BM25, cosine similarity, RRF, MMR
  providers.py     local and OpenAI embedders and generators
  grounding.py     faithfulness judges           llm.py       expansion, generation
  cache.py         scoped semantic cache         router.py    intent classification
  ratelimit.py     token-bucket limiter          audit.py     structured tool audit
  feedback.py      SQLite store, keyed digests   ingest.py    Document and Source
  channels/        optional adapters (Slack reference implementation)
  mcp_server.py    MCP tools: search, fetch, ask
  mcp_auth.py      OAuth 2.1 resource server for MCP
scripts/           build_index, smoke_query, eval_golden, eval_beir
examples/          one runnable file per extension point
docs/              architecture, extending, adapters, MCP, evaluation, threat model
```

## Extension points

Every stage has a zero-dependency default and is selected by environment
variable. Pointing a `*_CLASS` variable at an external class requires no change
to this repository:

```bash
EMBEDDER_CLASS=mypkg.embed:E5Embedder
RETRIEVER_CLASS=mypkg.store:PgVectorRetriever
```

| Seam | Selected by | Example |
| --- | --- | --- |
| Embedder | `EMBEDDING_PROVIDER`, `EMBEDDER_CLASS` | `examples/custom_embedder_fastembed.py` |
| Generator | `GENERATION_PROVIDER`, `GENERATOR_CLASS` | `examples/custom_generator_anthropic.py` |
| Reranker | `RERANKER_CLASS`, `RERANK_ENABLED` | `examples/custom_reranker_crossencoder.py` |
| Retriever | `RETRIEVER_CLASS` | `examples/custom_store_sqlite.py` |
| Grounding judge | `GROUNDING_JUDGE`, `GROUNDING_JUDGE_CLASS` | `app/grounding.py` |
| Prompts | `PROMPTS_DIR` | `app/prompts/*.md` |
| Ingestion source | `--source` on `build_index.py` | `examples/custom_source_sitemap.py` |
| Channel adapter | one `include_router` call | `examples/adapter_discord.py` |
| MCP tools | `MCP_EXTENSIONS_MODULE` | `examples/mcp_tool_custom.py` |

Recipes are in [docs/EXTENDING.md](docs/EXTENDING.md); adapter design in
[docs/adapters.md](docs/adapters.md); MCP and OAuth setup in
[docs/mcp.md](docs/mcp.md); deployment in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Design rationale

**Hybrid retrieval.** Technical questions contain identifiers. Dense vectors
handle paraphrase; lexical matching handles `fulfillment_type` and
`POST /v1/orders`, where an approximate match is not useful. Exact identifier
matches are injected into the candidate pool so precise questions cannot be
ranked away. The measured limits of this design are reported above.

**Rank fusion rather than score mixing.** A cosine similarity and a BM25 score
are not on a common scale, so fusing by rank avoids inventing a weighting
between incommensurable units.

**A grounding gate before delivery.** A confident wrong answer is more
expensive than a slow one. The judge scores faithfulness against the retrieved
chunks; below threshold the pipeline regenerates once under a stricter prompt
and, failing that, delivers with an explicit low-confidence note.

**Fail-closed verification.** An unset signing secret rejects every request
rather than accepting unsigned ones, and an unknown provider name fails at
startup rather than on first use.

**Scoped answer cache.** A cache hit bypasses retrieval entirely, which makes
the cache, not the retriever, the boundary at which an answer could cross an
authorization line. Entries are partitioned by caller.

**Keyed feedback digests.** Identifiers and question text are stored as
HMAC-SHA256 digests under a secret key. Unkeyed hashes of these inputs are
reversible by enumeration or dictionary attack. Even keyed, this is
pseudonymization rather than anonymization.

## Security

[docs/THREAT-MODEL.md](docs/THREAT-MODEL.md) decomposes the system into
components, data flows, and trust boundaries, then works STRIDE, the OWASP Top
10 for LLM Applications, and the OWASP MCP Top 10 against them. Each entry is
marked mitigated, partial, or not mitigated, with the control named or the gap
stated; automated findings that do not apply are listed with the reason for
dismissal.

Implemented controls include HMAC webhook verification on raw request bytes,
OAuth 2.1 resource-server validation for MCP with a canonical audience,
per-caller rate limiting, index integrity verification against a signed
manifest, and structured audit events for tool calls. Known gaps, including the
absence of document-level authorization and the fact that prompt-injection
resistance is argued rather than demonstrated, are documented in the same file.

## Scope

No vector database is required; the JSONL index is the default and
`RETRIEVER_CLASS` is the seam for anything larger. Retrieval arithmetic is
pure Python, so the core installs without numpy — it appears only in the
optional `eval` extra, pulled in by the embedding model used for benchmarking.
There is no RAG framework, no multi-tenancy, no web interface, and no agent
loop. Additional channels, stores, and rerankers belong in `examples/` rather
than in the core dependency list.

## Acknowledgments

- [RAGFlow](https://github.com/infiniflow/ragflow) informed the citation-first
  pipeline design. No code is shared with it.
- [Model Context Protocol](https://modelcontextprotocol.io) specifies the
  server and authorization model implemented in `app/mcp_server.py` and
  `app/mcp_auth.py`.

## License

MIT. See [LICENSE](LICENSE).
