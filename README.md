# slack-rag-skeleton

`slack-rag-skeleton` is a production-shaped skeleton of a grounded Slack RAG assistant: every stage is a small, swappable Python module, and the project runs end-to-end locally without cloud credentials. The offline demo uses fictional Acme Storefront documentation, deterministic local embeddings, hybrid retrieval, extractive generation, and a grounding gate before delivery.

## Pipeline

```text
Slack event
  -> verify signature
  -> semantic cache
  -> router
  -> query expansion
  -> hybrid retrieve (dense + BM25, RRF)
  -> rerank
  -> MMR diversity
  -> generate answer
  -> grounding gate
  -> Block Kit reply
```

## Quickstart

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/build_index.py --docs data/sample_docs --out data/index.jsonl
python scripts/smoke_query.py "How do refunds work?"
python scripts/eval_golden.py
python -m pytest
```

The default configuration is offline demo mode. No environment variables are required for tests, indexing, smoke queries, or evaluation.

## Going Live

Create a Slack app with event subscriptions for messages your app should answer, interactivity enabled for feedback buttons, and a bot token scoped for posting messages. Copy `.env.example` to `.env`, fill in the token and signing secret, and set `APP_ENV=prod` so startup fails if required credentials are missing.

Provider swaps are controlled by environment variables:

```bash
EMBEDDING_PROVIDER=openai
GENERATION_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_GENERATION_MODEL=gpt-4o
OPENAI_EXPANSION_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

Docker build:

```bash
docker build -t slack-rag-skeleton .
docker run --env-file .env -p 8000:8000 slack-rag-skeleton
```

## MCP Server

The same pipeline is exposed to Model Context Protocol clients as a standalone streamable-HTTP server with two tools: `search_docs` (hybrid retrieval only) and `ask` (the full grounded pipeline with citations and grounding score).

```bash
pip install -r requirements-mcp.txt
python -m app.mcp_server   # serves on MCP_PORT, default 8090
```

Point any MCP client at `http://localhost:8090/mcp`. Example client entry:

```json
{
  "mcpServers": {
    "rag-docs": {
      "url": "http://localhost:8090/mcp"
    }
  }
}
```

### Bring your own authorization server

With `MCP_AUTH_MODE=jwt` the server runs as an OAuth 2.1 protected resource for any standards-compliant identity provider. It publishes RFC 9728 protected-resource metadata at `/.well-known/oauth-protected-resource` so clients can discover your authorization server, rejects unauthenticated requests with a `WWW-Authenticate` challenge, and validates bearer JWTs against your issuer's JWKS (signature, `iss`, `aud`, `exp`). Verification fails closed.

```bash
MCP_AUTH_MODE=jwt
MCP_AUTH_ISSUER=https://idp.example.com/oauth2/default
MCP_AUTH_AUDIENCE=api://slack-rag-skeleton
MCP_RESOURCE_URL=https://rag.example.com
# Optional: set MCP_AUTH_JWKS_URL explicitly for non-OIDC authorization servers
```

Register this server as an API/resource in your identity provider, set the audience to match `MCP_AUTH_AUDIENCE`, and grant client applications a scope for it. The default `MCP_AUTH_MODE=off` skips auth entirely and is for local demos only.

## Design Notes

Hybrid retrieval is used because support and operations questions often include identifiers, paths, fields, and error codes where lexical matches matter as much as dense similarity. RRF combines ranked lanes without pretending their raw scores are comparable. MMR keeps the final context from being dominated by near-duplicate chunks from one document.

The grounding gate runs before delivery. If the first answer is weakly supported, the pipeline retries once with a stricter prompt and then labels the answer low confidence if support is still insufficient.

Slack signature verification fails closed, including empty signing secrets, stale timestamps, and tampered bodies. Feedback storage hashes user IDs and questions before writing them to SQLite. Provider and Slack calls are intended to be wrapped with a circuit breaker so transient failures do not cascade.

## What This Is Not

This repository does not include a scraped corpus, private knowledge, or a vendor lock. The bundled Acme Storefront docs are fictional and exist only to make the offline demo useful.

## Acknowledgments

- [RAGFlow](https://github.com/infiniflow/ragflow) inspired the citation-first, grounded pipeline design. This project shares no code with RAGFlow.
- [Model Context Protocol](https://modelcontextprotocol.io) defines the server spec and the OAuth 2.1 authorization model implemented in `app/mcp_server.py` and `app/mcp_auth.py`.
