# citespine

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
python scripts/eval_golden.py                           # score against the golden set
python -m pytest                                        # full suite, offline
```

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

**Hybrid retrieval**, because real questions contain identifiers. Dense vectors capture "how do I send an order back", lexical matching captures `fulfillment_type` and `POST /v1/orders`, and only one of those is a paraphrase problem. Exact identifier matches are force-injected into the candidate pool so precise API questions cannot be ranked away.

**RRF over score mixing**, because a cosine similarity and a BM25 score are not on a shared scale. Fusing by rank sidesteps the tuning problem that weighted score mixing creates.

**A grounding gate before delivery**, because a confident wrong answer costs more than a slow one. The judge scores faithfulness against the retrieved chunks; below threshold the pipeline regenerates once with a stricter prompt, and if it still fails the answer ships with an explicit low-confidence note.

**Fail-closed verification.** An unset Slack signing secret rejects every request rather than accepting unsigned ones. An unknown provider name fails at startup, listing what is available, rather than at the first request.

**Hashed feedback.** User ids and questions are stored as SHA-256 digests, so verdict counts survive without accumulating a log of who asked what.

## Non-goals

No vector database is required (the JSONL index is the default; `RETRIEVER_CLASS` is the seam if you want one). No numpy, no RAG framework, no multi-tenancy, no web UI, no agent loop. New channels, stores, and rerankers belong in `examples/`, never in `requirements.txt`. Keeping the dependency list short is what makes the code readable.

## Acknowledgments

- [RAGFlow](https://github.com/infiniflow/ragflow) inspired the citation-first, grounded pipeline design. This project shares no code with RAGFlow.
- [Model Context Protocol](https://modelcontextprotocol.io) defines the server spec and the OAuth 2.1 authorization model implemented in `app/mcp_server.py` and `app/mcp_auth.py`.

## License

MIT. See [LICENSE](LICENSE).
