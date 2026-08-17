# Extending citespine

Every recipe below should take **under 30 minutes from clone to running**. If
one of them takes longer, that is a bug in this document or in the seam it
describes, not a failure on your part.

Three rules hold across all of them:

1. **Every slot keeps a zero-dependency local default.** Your custom
   embedder must be swappable back out, and the offline test suite must
   still pass with an empty `.env`. That is what makes any single component
   testable in isolation.
2. **Copy, don't mutate.** Stages receive shared objects (chunk lists,
   `ScoredChunk` instances). Return new lists and new objects; never edit
   your input.
3. **Dotted paths execute your code.** `EMBEDDER_CLASS` and its siblings
   import and run whatever they name, at startup. They are operator
   configuration: read them from the environment or `.env` only. Never build
   one from a request body, an adapter payload, or an MCP tool argument.

There are three tiers of swapping, in increasing order of intrusiveness:

| Tier | Use when | How |
| --- | --- | --- |
| Registry name | You want a built-in | `EMBEDDING_PROVIDER=openai` |
| Dotted path | You wrote your own class | `EMBEDDER_CLASS=mypkg.embed:MyEmbedder` |
| Direct construction | You are writing a test | build `PipelineDeps(...)` yourself |

---

## Swap the embedder

**Protocol** (`app/interfaces.py`):

```python
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

**Example**: [`examples/custom_embedder_fastembed.py`](../examples/custom_embedder_fastembed.py)

**Wire it**: `EMBEDDER_CLASS=examples.custom_embedder_fastembed:FastEmbedEmbedder`

Your class may take `Settings` as its single constructor argument, or no
arguments at all; `app/registry.py` inspects the signature and calls it
accordingly.

**Rebuild the index after switching.** Query vectors and stored vectors must
come from the same model, and nothing checks this for you:

```bash
python scripts/build_index.py --docs data/sample_docs --out data/index.jsonl
```

---

## Swap the generator

**Protocol**:

```python
class Generator(Protocol):
    def generate(self, system: str, user: str, max_tokens: int) -> str: ...
```

**Example**: [`examples/custom_generator_anthropic.py`](../examples/custom_generator_anthropic.py)

**Wire it**: `GENERATOR_CLASS=examples.custom_generator_anthropic:ClaudeGenerator`

Lazy-import your provider SDK inside the method body so it stays an optional
dependency. The circuit breaker in `app/pipeline.py` already wraps whatever
you plug in, so you do not need your own retry logic.

---

## Swap the reranker

**Protocol**:

```python
class Reranker(Protocol):
    def rerank(self, query: str, chunks: Sequence[ScoredChunk], top_k: int) -> list[ScoredChunk]: ...
```

**Example**: [`examples/custom_reranker_crossencoder.py`](../examples/custom_reranker_crossencoder.py)

**Wire it**: `RERANKER_CLASS=...` plus `RERANK_ENABLED=true`

Build new `ScoredChunk` objects (`dataclasses.replace`) rather than editing
the ones retrieval handed you. `RERANK_POOL` controls how many candidates
reach the reranker, which is the main cost/quality dial.

---

## Swap the retriever or vector store

**Protocol**:

```python
class Retriever(Protocol):
    def retrieve(self, query: str, query_embedding: Sequence[float], k: int) -> list[ScoredChunk]: ...
```

**Example**: [`examples/custom_store_sqlite.py`](../examples/custom_store_sqlite.py)

**Wire it**: `RETRIEVER_CLASS=examples.custom_store_sqlite:SqliteRetriever`

Return chunks carrying **stable ids**: fusion, feedback, and the MCP `fetch`
tool all reference them across calls. RRF fusion, identifier injection, and
MMR stay in the pipeline, so swapping the store never means reimplementing
them. Expose a `chunks` attribute (or `chunk_count`) if you want `/health`
and MCP `fetch` to keep working.

---

## Swap the grounding judge

**Protocol**:

```python
class GroundingJudge(Protocol):
    def judge(self, answer: str, chunks: Sequence[ScoredChunk]) -> GroundingResult: ...
```

**Wire it**: `GROUNDING_JUDGE=heuristic|llm`, or `GROUNDING_JUDGE_CLASS=...`

Never raise from `judge`. Return a low score with a `judge_error` verdict
instead, so the regenerate-then-caveat flow stays deterministic. To change
only the wording of the LLM judge, override its prompt rather than the class
(below).

---

## Change the prompts

Prompts are `.md` files in `app/prompts/`, rendered with
`string.Template.safe_substitute` (so literal JSON braces are safe).

```bash
mkdir my_prompts
cp app/prompts/answer_system.md my_prompts/
$EDITOR my_prompts/answer_system.md
export PROMPTS_DIR=./my_prompts
```

Files in `PROMPTS_DIR` shadow packaged ones by filename; anything you do not
override falls back to the default. No fork required.

---

## Add an ingestion source

**Protocol** (`app/ingest.py`):

```python
class Source(Protocol):
    def load(self) -> Iterator[Document]: ...
```

**Example**: [`examples/custom_source_sitemap.py`](../examples/custom_source_sitemap.py)

**Wire it**:

```bash
python scripts/build_index.py --source examples.custom_source_sitemap:SitemapSource --out data/index.jsonl
```

Yield `Document` objects with stable ids. Chunking, embedding, and index
writing are separate stages that consume `Document`s, so a new source never
touches them.

---

## Add a channel adapter

**Seam** (`app/channels/base.py`):

```python
AskFn = Callable[[AskRequest], Awaitable[AskResponse]]

def create_router(ask: AskFn, settings: Settings) -> APIRouter: ...
```

**Examples**: [`examples/adapter_discord.py`](../examples/adapter_discord.py),
[`examples/adapter_cli.py`](../examples/adapter_cli.py), and the reference
implementation in [`app/channels/slack.py`](../app/channels/slack.py).

**Wire it**: one line in `mount_channels()` in `app/main.py`.

Or skip Python entirely: receive the platform webhook wherever you like,
`POST /v1/ask`, and post the answer back. See [docs/adapters.md](adapters.md)
for both paths and the reasoning behind the three adapter rules.

---

## Add an MCP tool

**Hook**: a module exposing `register(server, deps_provider, settings)`.

**Example**: [`examples/mcp_tool_custom.py`](../examples/mcp_tool_custom.py)

**Wire it**: `MCP_EXTENSIONS_MODULE=examples.mcp_tool_custom`

```python
def register(server, deps_provider, settings):
    @server.tool(description="What this tool does.")
    def acme_ping() -> str:
        """Type hints become the input schema."""
        return "pong"
```

Your tools appear in both the standalone server and the one mounted at
`/mcp`. Details and the OAuth setup: [docs/mcp.md](mcp.md).

---

## Add a setting

Three steps, in order:

1. Add the field to `Settings` in `app/config.py` (the env var is its
   upper-cased name).
2. Add a commented line to `.env.example` under the right section.
3. Mention it wherever it is relevant in `docs/`.

Cross-field validation belongs in the `@model_validator` in `app/config.py`,
so misconfiguration fails at startup rather than on the first request.

## Testing your extension

```bash
python -m pytest
```

`tests/test_examples.py` imports every file in `examples/` and duck-checks it
against its protocol, which is what stops these recipes from rotting. Copy
`tests/test_pipeline_offline.py` as the template for an end-to-end test of
your own component, and use the `fake_deps` fixture in `tests/conftest.py`
for a wired, fully local `PipelineDeps`.
