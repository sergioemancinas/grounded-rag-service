# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

Only the latest release on `main` receives security fixes.

## Reporting a vulnerability

Email **security@example.com** (placeholder — replace with the maintainer contact)
with a description of the issue, impact, and steps to reproduce. Please do not
open a public GitHub issue for vulnerabilities that could expose corpus data or
enable remote code execution.

You should receive an acknowledgement within a few days. Coordinated disclosure
is preferred; please allow reasonable time for a fix before public discussion.

## Security posture

This repository is a **portfolio RAG service skeleton**. It is intentionally
unsafe to expose on the public internet with the default configuration:

- **No authentication by default.** An empty `API_AUTH_TOKEN` leaves `/v1/*`
  open to anyone who can reach the process.
- **`/v1/search` returns raw corpus chunks** with no grounding gate and no
  generation. Anyone who can call it can read the indexed corpus verbatim.
- **Dotted-path `*_CLASS` settings execute operator-supplied code** at startup
  by design (`EMBEDDER_CLASS`, `GENERATOR_CLASS`, `RERANKER_CLASS`,
  `RETRIEVER_CLASS`, `GROUNDING_JUDGE_CLASS`). Set them only from the
  environment or `.env`, never from request data.
- **The MCP endpoint defaults to unauthenticated** (`MCP_AUTH_MODE=off`). Treat
  that as a local-demo setting only.

Before any network-facing deployment, read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
and enable auth (HTTP bearer and/or MCP JWT) appropriate to your threat model.
