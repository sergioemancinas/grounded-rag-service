# Examples

One complete file per extension point. Each is under 100 lines, carries its
run instructions in the module docstring, and imports its heavy dependencies
inside functions so the file stays importable without them.

`tests/test_examples.py` imports every file here and checks it still matches
its protocol, so these cannot silently rot when the interfaces change.

| File | Seam | Extra deps | Run |
| --- | --- | --- | --- |
| `custom_embedder_fastembed.py` | `Embedder` | `fastembed` | `EMBEDDER_CLASS=examples.custom_embedder_fastembed:FastEmbedEmbedder` |
| `custom_generator_anthropic.py` | `Generator` | `anthropic` | `GENERATOR_CLASS=examples.custom_generator_anthropic:ClaudeGenerator` |
| `custom_reranker_crossencoder.py` | `Reranker` | `sentence-transformers` | `RERANKER_CLASS=examples.custom_reranker_crossencoder:CrossEncoderReranker` |
| `custom_store_sqlite.py` | `Retriever` | none | `RETRIEVER_CLASS=examples.custom_store_sqlite:SqliteRetriever` |
| `custom_source_sitemap.py` | `Source` (ingestion) | `beautifulsoup4` | `scripts/build_index.py --source examples.custom_source_sitemap:SitemapSource` |
| `adapter_cli.py` | Channel adapter | none | `python examples/adapter_cli.py` |
| `adapter_discord.py` | Channel adapter | `PyNaCl` | add one `include_router` line, see the docstring |
| `mcp_tool_custom.py` | MCP tools | `mcp` | `MCP_EXTENSIONS_MODULE=examples.mcp_tool_custom` |

Two of these run with no extra dependencies at all: `adapter_cli.py` answers
questions in your terminal, and `custom_store_sqlite.py` converts the JSONL
index to SQLite and serves from it.
