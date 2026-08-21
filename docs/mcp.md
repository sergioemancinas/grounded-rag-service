# MCP server

The pipeline is exposed to Model Context Protocol clients as three tools,
either standalone or mounted inside the core service.

```bash
pip install -r requirements-mcp.txt
python -m app.mcp_server          # standalone, on MCP_PORT (default 8090)
```

Mounted instead, sharing one process and one index with the HTTP API:

```bash
uvicorn app.main:app --port 8000  # MCP served at /mcp when `mcp` is installed
```

The mount is automatic and guarded: with the optional `mcp` package absent,
the service logs `mcp server: not mounted` and everything else runs
normally.

Client configuration:

```json
{
  "mcpServers": {
    "grounded-rag-service": { "url": "http://localhost:8090/mcp" }
  }
}
```

## Tools

| Tool | Signature | Purpose |
| --- | --- | --- |
| `search` | `(query: str, k: int = 5)` | Ranked chunks with ids, titles, urls, snippets. No generation. |
| `fetch` | `(id: str)` | One chunk in full, by an id `search` returned. |
| `ask` | `(question: str)` | The full grounded pipeline: cited answer plus grounding score. |

`search` and `fetch` are a deliberate pair. Clients that do their own
reasoning want to search, choose, then read the winner in full, and the
search-then-fetch shape is what connector-style clients expect.
`search_docs` remains as a deprecated alias of `search`.

Rename or re-describe the built-ins without touching code:

```bash
MCP_TOOL_SEARCH_DESCRIPTION="Search the Acme Storefront corpus."
MCP_TOOL_FETCH_DESCRIPTION="Read one Acme Storefront doc section in full."
MCP_TOOL_ASK_DESCRIPTION="Answer an Acme Storefront question with citations."
```

Tool descriptions are prompt surface: they are how a model decides which
tool to call, so making them domain-specific measurably improves selection.

## Adding your own tools

Write a module exposing `register(server, deps_provider, settings)`:

```python
def register(server, deps_provider, settings):
    @server.tool(description="List every indexed document with its chunk count.")
    def list_documents() -> list[dict]:
        """Type hints become the input schema; this docstring documents it."""
        ...
```

Then `MCP_EXTENSIONS_MODULE=mypkg.mytools`. Your tools appear in both the
standalone and mounted servers. Full example:
[`examples/mcp_tool_custom.py`](../examples/mcp_tool_custom.py).

Call `deps_provider()` inside the tool body rather than at registration
time; when mounted, dependencies are built during the host's startup.

This is one explicit import, not a plugin scan. What runs is exactly what
you named.

## Bring your own authorization server

With `MCP_AUTH_MODE=jwt` the server becomes an OAuth 2.1 **protected
resource**. It is not an authorization server and never issues tokens: you
point it at whichever identity provider you already run.

```bash
MCP_AUTH_MODE=jwt
MCP_AUTH_ISSUER=https://idp.example.com/oauth2/default
MCP_RESOURCE_URL=https://rag.example.com/mcp
MCP_AUTH_AUDIENCE=https://rag.example.com/mcp   # defaults to MCP_RESOURCE_URL
MCP_REQUIRED_SCOPES=grounded-rag-service.read              # optional
MCP_AUTH_JWKS_URL=                              # optional; discovered from the issuer
MCP_AUTH_ALGORITHMS=RS256
```

What the server then does:

- Publishes RFC 9728 protected-resource metadata, so clients discover your
  authorization server automatically.
- Answers unauthenticated requests with `401` and a `WWW-Authenticate`
  challenge pointing at that metadata.
- Validates each bearer JWT against the issuer's JWKS: signature, `iss`,
  `aud`, `exp`, and an algorithm allowlist. Any failure rejects.
- Answers a valid token missing a required scope with `403` and
  `error="insufficient_scope"` (as opposed to `401`, which means "get a
  token"; `403` means "your token is not enough").

### The one rule that breaks real deployments

**One canonical URL, everywhere, including the `/mcp` path.** These three
must be byte-identical:

1. the `resource` field in the metadata document,
2. the `resource_metadata` pointer in the `WWW-Authenticate` challenge,
3. the `aud` claim your identity provider mints into tokens (RFC 8707).

Set `MCP_RESOURCE_URL` to the full public URL including `/mcp` and the
server derives all three from it. Get this wrong and every local test still
passes while every real token is rejected, which is why
`tests/test_mcp_auth.py` asserts the equality explicitly.

Metadata is served at both paths, because the spec inserts the resource
path and humans try the bare one:

```
/.well-known/oauth-protected-resource        (bare)
/.well-known/oauth-protected-resource/mcp    (path-inserted; what clients use)
```

When mounted inside the core service, the host serves those documents at its
own root, since well-known URLs cannot live under `/mcp`.

### Provider setup

Whatever your identity provider is called, the steps are the same:

1. Register this server as an API / resource / audience, with the identifier
   set to your canonical URL (`https://rag.example.com/mcp`).
2. Define scopes if you want them, and set `MCP_REQUIRED_SCOPES` to match.
3. Grant client applications access to that resource.
4. Confirm issued tokens carry `aud` equal to the canonical URL. Many
   providers default the audience to something else, and that mismatch is
   the single most common cause of `401`s here.

### Security notes

- `MCP_AUTH_MODE=off` (the default) means no authentication at all. It is
  for local demos. Never expose it beyond localhost.
- The inbound bearer token authenticates the caller to *this* server. Never
  forward it to OpenAI, your vector store, or any other upstream.
- MCP tool arguments are untrusted input. Never let one select a
  `*_CLASS` dotted path or any other code-loading setting.
- `ask` runs the grounding gate; `search` and `fetch` return raw corpus
  content. If your corpus is sensitive, authentication is what stands
  between it and a caller.
