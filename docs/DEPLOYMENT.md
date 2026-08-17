# Deployment

## Docker

```bash
docker build -t citespine .
docker run --env-file .env -p 8000:8000 citespine
```

The image builds the index from `data/sample_docs` at build time, so the
container answers questions immediately with no volume and no credentials.
Swap in your own corpus by replacing `data/sample_docs/` (or by mounting an
index and setting `INDEX_PATH`).

Run as a non-root user, keep the container read-only except for the feedback
database path, and put a reverse proxy in front for TLS.

## Configuration

Every setting is one field on `Settings` in `app/config.py`, exposed as its
upper-cased name. `.env.example` is the annotated reference, organized in
sections. **An empty `.env` boots the full offline stack**, which is what
makes the defaults safe to trust.

| Section | Key variables |
| --- | --- |
| Core | `APP_ENV`, `INDEX_PATH`, `FEEDBACK_DB_PATH` |
| Providers | `EMBEDDING_PROVIDER`, `GENERATION_PROVIDER`, `OPENAI_API_KEY`, `*_CLASS` hatches |
| Retrieval | `RERANK_ENABLED`, `RERANK_POOL`, `LEXICAL_SCORER`, `MMR_LAMBDA`, `CONTEXT_MAX_PER_DOC` |
| Grounding | `GROUNDING_CHECK_ENABLED`, `GROUNDING_MIN_SCORE`, `GROUNDING_JUDGE` |
| Prompts | `PROMPTS_DIR` |
| API auth | `API_AUTH_TOKEN` |
| Slack adapter | `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN`, `ALLOWED_CHANNEL_IDS` |
| MCP | `MCP_AUTH_MODE`, `MCP_AUTH_ISSUER`, `MCP_RESOURCE_URL`, `MCP_REQUIRED_SCOPES` |

Misconfiguration fails at startup, not on the first request:
`GENERATION_PROVIDER=openai` without `OPENAI_API_KEY` refuses to boot, and
an unknown provider name raises listing the valid ones.

### Adding a setting

1. Add the field to `Settings` in `app/config.py`.
2. Add a commented line to `.env.example` under the matching section.
3. Document it where it is relevant in `docs/`.

## Before you expose it

**`/v1/search` returns raw corpus chunks with no grounding gate and no
generation.** That is the point of the endpoint, and it means anyone who can
reach it can read your corpus verbatim. The skeleton ships with **no
authentication**, so before it leaves localhost:

```bash
API_AUTH_TOKEN=$(openssl rand -hex 32)
```

That requires a static bearer on all `/v1/*` routes. It is deliberately the
simplest thing that works; for real multi-user access put an authenticating
proxy in front, or implement per-user authorization at retrieval time, which
this skeleton does not attempt.

Other pre-flight items:

- Set `MCP_AUTH_MODE=jwt` if the MCP endpoint is reachable. The default
  `off` means unauthenticated access to the same corpus. See
  [docs/mcp.md](mcp.md).
- Keep secrets in your platform's secret manager, not in the image.
- The Slack adapter mounts only with `SLACK_SIGNING_SECRET` set; confirm the
  startup log line says `mounted` when you expect it to.
- The semantic cache and feedback database are per-instance. Multiple
  replicas each keep their own; use one instance or move both to shared
  storage.

## Scaling notes

The JSONL index loads into memory at startup, which is fine into the tens of
thousands of chunks and is not a vector database. Past that, implement the
`Retriever` protocol against pgvector, Qdrant, or similar
(`examples/custom_store_sqlite.py` shows the shape) and set
`RETRIEVER_CLASS`. Nothing else changes.

Generation dominates latency; retrieval is milliseconds. The cheapest wins
are the semantic cache, then `RERANK_POOL`, then a smaller expansion model.

## Make it yours

This is a template. The rename checklist:

1. `citespine` appears in `README.md`, `Dockerfile`, `app/main.py`
   (the FastAPI title), and the loggers in `app/main.py`,
   `app/channels/slack.py`, `app/mcp_server.py`. Replace all of them.
2. Replace `data/sample_docs/` with your corpus and rebuild the index.
3. Replace `data/golden_questions.example.jsonl` with questions from your
   own domain, and make `scripts/eval_golden.py` a CI gate.
4. Rewrite `app/prompts/*.md` in your voice, or point `PROMPTS_DIR`
   elsewhere.

Deleting what you do not need, each a one-commit change touching no core
module:

- **Slack**: delete `app/channels/slack.py`, `app/channels/slack_render.py`,
  `requirements-slack.txt`, `tests/channels/`, and the `mount_channels`
  branch in `app/main.py`.
- **MCP**: delete `app/mcp_server.py`, `app/mcp_auth.py`,
  `requirements-mcp.txt`, the MCP tests, and the `mount_mcp` call.
- **Examples**: delete `examples/` and `tests/test_examples.py`.
