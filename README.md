# citespine

[![CI](https://github.com/sergioemancinas/citespine/actions/workflows/ci.yml/badge.svg)](https://github.com/sergioemancinas/citespine/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Grounded, citation-first RAG service skeleton. FastAPI, channel-agnostic, zero API keys to run.**

- **Grounded by construction.** Every answer is generated from retrieved chunks, cites them inline, and passes a faithfulness gate before it is delivered.
- **Channel-agnostic.** The core is an HTTP service. Slack, Discord, a CLI, or an MCP client are adapters over one seam, and none of them are required.
- **Runs offline, out of the box.** Local hash embeddings and extractive generation mean `git clone` to first cited answer needs no credentials and no network.

It is a skeleton, not a framework: about 3,000 lines of explicit Python you are meant to read, fork, and own.

## Pipeline

```text
question
  -> semantic cache        (skip everything on a near-identical question)
  -> intent router         (documentation question, or small talk)
  -> query expansion       (one question becomes several phrasings)
  -> hybrid retrieval      (dense cosine + BM25, per phrasing)
  -> RRF fusion            (merge ranked lists without comparing raw scores)
  -> identifier injection  (force exact matches for /v1/orders, snake_case, ERR_CODES)
  -> rerank                (optional cross-encoder over the candidate pool)
  -> MMR selection         (drop near-duplicates, cap chunks per document)
  -> generation            (answer only from context, cite every claim)
  -> grounding gate        (score, regenerate once, then caveat)
  -> cited markdown answer
```

The whole flow is one readable function: `answer_question()` in `app/pipeline.py`.

## Quickstart

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/build_index.py --docs data/sample_docs --out data/index.jsonl
uvicorn app.main:app --port 8000
```

```bash
curl -s localhost:8000/v1/ask -H 'content-type: application/json' \
  -d '{"question":"How do refunds work?"}' | jq '{answer, sources: [.sources[].title], grounding}'
```

No environment variables, no API keys. The bundled corpus is fictional documentation for a made-up commerce platform, Acme Storefront.

Other ways in:

```bash
python examples/adapter_cli.py "How do refunds work?"   # same pipeline, in your terminal
python scripts/smoke_query.py "How do refunds work?"    # retrieval + timings
python scripts/eval_golden.py                           # fast smoke test (not an evaluation)
python scripts/eval_beir.py --lanes bm25,dense,hybrid   # the real benchmark, see Results
python -m pytest                                        # full suite, offline
```

## Results

Measured on [BEIR SciFact](https://arxiv.org/abs/2104.08663) (300 test
queries, 5,183 documents) with this repo's own retrieval code.

**Offline default** (`LocalHashEmbedder`, no extra deps):

```bash
python scripts/eval_beir.py --lanes bm25,dense,hybrid
```

| Lane | nDCG@10 | Recall@10 | MRR@10 |
| --- | --- | --- | --- |
| BM25 only | **0.6047** | 0.7262 | 0.5697 |
| Dense only (LocalHashEmbedder) | 0.1557 | 0.2381 | 0.1322 |
| Hybrid, RRF (LocalHashEmbedder) | 0.3756 | 0.5361 | 0.3322 |

**Real embedder** (`BAAI/bge-small-en-v1.5` via fastembed):

```bash
pip install -e ".[eval]"
python scripts/eval_beir.py --lanes bm25,dense,hybrid \
  --embedder examples.custom_embedder_fastembed:FastEmbedEmbedder
```

| Lane | nDCG@10 | Recall@10 | MRR@10 |
| --- | --- | --- | --- |
| BM25 only | 0.6047 | 0.7262 | 0.5697 |
| Dense only (bge-small-en-v1.5) | **0.7200** | 0.8452 | 0.6845 |
| Hybrid, RRF (bge-small-en-v1.5) | 0.6783 | 0.7783 | 0.6528 |

Published BEIR reference for SciFact BM25 is nDCG@10 0.665, from a different
implementation (Elasticsearch, multi-field). Shown for orientation; this is
not a reproduction of it.

**Read both tables honestly.** With the offline hash embedder, hybrid is 38%
worse than BM25 alone (0.3756 vs 0.6047): RRF blends a strong lexical lane
with a near-random dense lane (0.1557 standalone). With
`BAAI/bge-small-en-v1.5`, hybrid beats BM25 (0.6783 vs 0.6047) but still
trails dense-only (0.7200): the same untuned RRF constant 60 now mixes a
stronger dense lane with a weaker lexical one. No fusion parameters were
tuned against this split.

This is also why the bundled fictional corpus is a smoke test rather than an
evaluation: three documents cannot expose a regression that a public
benchmark found on the first run. Full methodology, licensing, and
limitations: [docs/EVALUATION.md](docs/EVALUATION.md).

## HTTP API

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/ask` | Full pipeline. Returns markdown `answer`, `citations`, `sources`, `grounding`, `timings`, `request_id`. |
| `POST /v1/search` | Retrieval only. Returns raw ranked chunks, no generation. |
| `POST /v1/feedback` | Records an up/down verdict against a `request_id`. |
| `GET /health` | Liveness plus the resolved provider names and chunk count. |

Set `API_AUTH_TOKEN` to require a static bearer on `/v1/*`. Platform signature verification (Slack HMAC, Discord Ed25519) belongs to adapters, never to the core.

**Streaming** is deliberately absent from v1. The grounding gate judges a *completed* answer and may regenerate it, which token streaming cannot express, and chat adapters render placeholder-then-edit rather than consuming SSE. `GET /v1/ask/stream` is reserved for when the gate becomes incremental.

## Repository layout

```text
app/
  main.py          Core service: /v1 routes, lifespan wiring, mount hooks
  pipeline.py      answer_question(): the whole flow, in order
  interfaces.py    Every stage Protocol, with its contract in the docstring
  registry.py      Name -> factory dicts, plus the dotted-path escape hatch
  deps.py          build_deps(): where settings become wired components
  providers.py     Local + OpenAI embedders and generators
  retrieval.py     JSONL index, BM25, cosine, RRF, MMR
  rerank.py        Passthrough default, cross-encoder seam
  grounding.py     Faithfulness judges (heuristic and LLM)
  llm.py           Query expansion, answer generation, follow-ups
  prompts/         Prompt text as .md files; override with PROMPTS_DIR
  cache.py         Semantic cache      router.py     Intent classification
  resilience.py    Circuit breaker     feedback.py  SQLite, hashed identifiers
  ingest.py        Document + Source protocol, markdown chunker
  api_models.py    Request/response shapes: the adapter contract
  channels/        Adapters. Delete freely.
    base.py          The AskFn seam and the three adapter rules
    slack.py         Reference adapter: HMAC, dedup, Block Kit
    http_client.py   An AskFn that calls a remote core over HTTP
  mcp_server.py    MCP tools (search/fetch/ask). Delete freely.
  mcp_auth.py      OAuth 2.1 resource server for MCP
scripts/           build_index, smoke_query, eval_golden
examples/          One runnable file per extension point
docs/              EXTENDING, ARCHITECTURE, adapters, mcp, DEPLOYMENT
```

## Customizing

Every stage is a small Protocol, chosen by an environment variable, with a
zero-dependency local default. Point a `*_CLASS` variable at your own class
and nothing in this repository needs to change:

```bash
EMBEDDER_CLASS=mypkg.embed:E5Embedder
GENERATOR_CLASS=mypkg.gen:ClaudeGenerator
RETRIEVER_CLASS=mypkg.store:PgVectorRetriever
```

| Seam | Swap by | Example |
| --- | --- | --- |
| Embedder | `EMBEDDING_PROVIDER` or `EMBEDDER_CLASS` | `examples/custom_embedder_fastembed.py` |
| Generator | `GENERATION_PROVIDER` or `GENERATOR_CLASS` | `examples/custom_generator_anthropic.py` |
| Reranker | `RERANKER_CLASS` + `RERANK_ENABLED` | `examples/custom_reranker_crossencoder.py` |
| Retriever / store | `RETRIEVER_CLASS` | `examples/custom_store_sqlite.py` |
| Grounding judge | `GROUNDING_JUDGE` or `GROUNDING_JUDGE_CLASS` | `app/grounding.py` |
| Prompts | `PROMPTS_DIR` | `app/prompts/*.md` |
| Ingestion source | `--source` on `build_index.py` | `examples/custom_source_sitemap.py` |
| Channel | one `include_router` line | `examples/adapter_discord.py` |
| MCP tools | `MCP_EXTENSIONS_MODULE` | `examples/mcp_tool_custom.py` |

Full recipes: [docs/EXTENDING.md](docs/EXTENDING.md). Design rationale: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Adapters: [docs/adapters.md](docs/adapters.md). MCP and OAuth: [docs/mcp.md](docs/mcp.md). Deployment: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Design notes

**Hybrid retrieval**, because real questions contain identifiers. Dense vectors capture "how do I send an order back", lexical matching captures `fulfillment_type` and `POST /v1/orders`, and only one of those is a paraphrase problem. Exact identifier matches are force-injected into the candidate pool so precise API questions cannot be ranked away. The measured caveat is above: with a real embedder hybrid beats BM25 on SciFact but does not beat dense alone under the default RRF settings; with the offline hash embedder it is a clear regression.

**RRF over score mixing**, because a cosine similarity and a BM25 score are not on a shared scale. Fusing by rank sidesteps the tuning problem that weighted score mixing creates.

**A grounding gate before delivery**, because a confident wrong answer costs more than a slow one. The judge scores faithfulness against the retrieved chunks; below threshold the pipeline regenerates once with a stricter prompt, and if it still fails the answer ships with an explicit low-confidence note.

**Fail-closed verification.** An unset Slack signing secret rejects every request rather than accepting unsigned ones. An unknown provider name fails at startup, listing what is available, rather than at the first request.

**Keyed feedback digests.** User ids and questions are stored as HMAC-SHA256 digests under a secret key, not plain hashes. A bare SHA-256 of a platform user id or a natural-language question is reversible by enumeration or dictionary attack, so only the key makes the digest unlinkable to whoever later reads the database. Even keyed, this is pseudonymization rather than anonymization: with the key the mapping is recoverable, so the rows remain personal data under GDPR.

**Scoped answer cache.** A cache hit skips retrieval entirely, which makes the cache, not the retriever, the place where an answer would cross an authorization boundary. Entries are therefore partitioned by caller scope, so adding per-document permissions later cannot silently turn the cache into a cross-user disclosure channel.

## Non-goals

No vector database is required (the JSONL index is the default; `RETRIEVER_CLASS` is the seam if you want one). No numpy, no RAG framework, no multi-tenancy, no web UI, no agent loop. New channels, stores, and rerankers belong in `examples/`, never in `requirements.txt`. Keeping the dependency list short is what makes the code readable.

## Acknowledgments

- [RAGFlow](https://github.com/infiniflow/ragflow) inspired the citation-first, grounded pipeline design. This project shares no code with RAGFlow.
- [Model Context Protocol](https://modelcontextprotocol.io) defines the server spec and the OAuth 2.1 authorization model implemented in `app/mcp_server.py` and `app/mcp_auth.py`.

## License

MIT. See [LICENSE](LICENSE).
