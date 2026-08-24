# Threat model

## Scope and method

This models **grounded-rag-service as deployed**: the HTTP core, the optional channel
adapter, the optional MCP server, and the data they touch. It covers the
software in this repository and the trust boundaries it creates. It does not
model the security of whatever LLM provider, identity provider, or host you
run it against.

Three public frameworks, chosen because a RAG service sits across all three:

| Framework | Why |
| --- | --- |
| [STRIDE](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats) | Structural decomposition per component and data flow |
| [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/) | Risks specific to retrieval and generation |
| [OWASP MCP Top 10 (2025, beta)](https://owasp.org/www-project-mcp-top-10/) | Risks specific to exposing tools to agents |

[MITRE ATLAS](https://atlas.mitre.org) and
[NIST AI 100-2](https://csrc.nist.gov/pubs/ai/100/2/e2025/final) are
referenced where a threat maps cleanly to a named technique.

**How to read the status column.** *Mitigated* means a control exists in this
repository and a test covers it. *Partial* means a control exists but is
known to be incomplete, and the gap is stated. *Not mitigated* means there is
no control, deliberately or otherwise. Nothing is marked mitigated on the
strength of a code comment alone.

## System decomposition

Data-flow diagram, with nodes grouped by trust zone. Squared nodes are
processes, cylinders are data stores, and everything in the untrusted zone is
attacker-controllable input.

```mermaid
flowchart LR
  subgraph untrusted["Untrusted callers (TB1, TB2, TB3)"]
    chat[Chat platform webhook]
    client[HTTP API client / external adapter]
    mcpc[MCP host client]
  end

  subgraph third["Third-party services"]
    idp[Identity provider OIDC]
    llm[LLM and embedding provider]
  end

  subgraph proc["grounded-rag-service process"]
    adapter[Channel adapter]
    core[Core RAG API service]
    mcps[MCP server]
    tools[MCP tools: search, fetch, ask]
    pipe[RAG pipeline and grounding gate]
  end

  subgraph state["State"]
    index[(Corpus index)]
    cache[(Semantic answer cache)]
    fb[(Feedback store)]
    secrets[(Config and secret store)]
  end

  subgraph platform["Deployment"]
    container[Container runtime]
    cicd[CI and CD pipeline]
  end

  chat -->|"HTTPS, HMAC-signed body"| adapter
  client -->|"HTTPS, optional static bearer"| core
  mcpc -->|"HTTPS, OAuth 2.1 bearer JWT"| mcps
  mcps -->|"JWKS fetch, token validation"| idp
  mcps -->|tool dispatch| tools
  adapter -->|in-process| pipe
  core -->|in-process| pipe
  tools -->|search, fetch, ask| pipe
  pipe -->|"scoped read/write (TB7)"| cache
  pipe -->|hybrid retrieval read| index
  pipe -->|"question + retrieved chunks (TB6)"| llm
  core -->|digested verdict write| fb
  secrets -->|"environment at startup (TB5)"| core
  cicd -->|build and deploy image| container
  container -->|hosts process| core

  classDef ext fill:#fde8e8,stroke:#c53030,color:#1a202c
  classDef svc fill:#e6f0fb,stroke:#2b6cb0,color:#1a202c
  classDef ds fill:#e9f6ec,stroke:#2f855a,color:#1a202c
  class chat,client,mcpc,idp,llm ext
  class adapter,core,mcps,tools,pipe,container,cicd svc
  class index,cache,fb,secrets ds
```

### Trust boundaries

| # | Boundary | Crossing | Enforcement |
| --- | --- | --- | --- |
| TB1 | Chat platform → adapter | Untrusted webhook | HMAC-SHA256 over raw bytes, 5-minute replay window, constant-time compare (`app/channels/slack.py`) |
| TB2 | API client → core | Untrusted HTTP | Optional static bearer, constant-time compare (`app/main.py`) |
| TB3 | MCP client → MCP server | Untrusted HTTP | OAuth 2.1 resource server: JWKS signature, `iss`, `aud`, `exp`, algorithm allowlist, scope check (`app/mcp_auth.py`) |
| TB4 | Retrieved corpus → generator | **Untrusted data entering the model's context** | Prompt-level instruction only. See LLM01. |
| TB5 | Operator config → process | Trusted by definition | Dotted-path `*_CLASS` settings execute operator code at startup |
| TB6 | Service → LLM provider | Data leaves the deployment | Retrieved chunks and the question are sent to the configured provider |
| TB7 | Caller identity → cache | Authorization boundary | Cache entries partitioned by scope, derived only from authentication and never from the request body. MCP keys on the verified token `sub`; HTTP keys on the static bearer, which is one shared credential rather than per-user identity, so all authenticated HTTP callers share a partition (`app/main.py:cache_scope_for`) |

TB4 and TB7 are the two that people building RAG systems most often miss.
TB4 because retrieved documents feel like data rather than instructions. TB7
because a cache hit never reaches the retriever, so any authorization check
that lives in retrieval is simply skipped.

## STRIDE

| Category | Threat | Status | Control, or why not |
| --- | --- | --- | --- |
| **S**poofing | Forged platform webhook | Mitigated | Signature verified on raw bytes before parsing; empty secret rejects everything rather than accepting it (`tests/channels/test_slack_signature.py`) |
| **S**poofing | Forged MCP caller | Mitigated | JWT validated against issuer JWKS with `aud` equal to the canonical resource URL (`tests/test_mcp_auth.py`) |
| **S**poofing | Unauthenticated API caller | **Not mitigated by default** | `/v1` is open unless `API_AUTH_TOKEN` is set. Documented in `docs/DEPLOYMENT.md`; a single shared token is not user authentication. |
| **T**ampering | Request body modified in transit | Mitigated | TB1 signature covers the exact bytes; re-serializing before verification would break it, hence verify-then-parse |
| **T**ampering | Corpus index modified on disk | Mitigated | `scripts/build_index.py` writes a sidecar manifest with the index sha256 and build provenance; `app/retrieval.py` reverifies on load. `INDEX_VERIFY=strict` refuses to boot on mismatch, `warn` (default) logs loudly, `off` skips. A missing manifest is tolerated and logged as unverified (`tests/test_index_integrity.py`). |
| **R**epudiation | Retrieval decision cannot be attributed | Mitigated | Both surfaces emit structured audit events: MCP tool calls with caller subject and argument digests, HTTP `/v1/ask` with the retrieved chunk and document ids, cache scope, intent, grounding verdict and a `request_id` that matches the response. This records which documents were surfaced to whom, which a proxy in front of the service cannot observe. |
| **I**nfo disclosure | Raw corpus readable via API | **By design, unauthenticated by default** | `/v1/search` returns verbatim chunks with no grounding gate. This is the endpoint's purpose; authentication is the only control. |
| **I**nfo disclosure | Cross-user answer leakage via cache | Mitigated for MCP, **not applicable to HTTP** | MCP partitions on the verified token subject, so one caller's answer cannot be served to another (`tests/test_cache_scope.py`). HTTP has no per-user identity to partition on: a static bearer authenticates the deployment, not a person, so every authenticated caller shares one partition. That is sound only while all of them may read the whole corpus. Introducing per-document permissions without also introducing per-user credentials would make this a disclosure channel. |
| **I**nfo disclosure | Cached answer served without its evidence | Mitigated | Cache entries carry citations, sources, and the grounding verdict, so a hit replays the same evidence as a miss. Previously only the answer text was cached, leaving `[n]` markers in the prose with an empty citation list (`tests/test_cache_scope.py`). |
| **I**nfo disclosure | Identifiers recoverable from feedback | Partial | HMAC-SHA256 under a secret key, not plain hashing. Pseudonymization, not anonymization: with the key the mapping is recoverable, so rows remain personal data. |
| **D**enial of service | Unbounded request volume or context size | Mitigated | Per-caller token-bucket rate limit on `/v1` returning 429 with `Retry-After` (`app/ratelimit.py`), and `MAX_QUESTION_CHARS` rejects oversized input as 422 before any provider call. The limiter's own key table is bounded so it cannot be used to exhaust memory. |
| **D**enial of service | Cache growth | Mitigated | Bounded entry count with oldest-first eviction. Lookup remains a linear scan, so latency grows with cache size. |
| **E**levation of privilege | Arbitrary code execution via config | Accepted by design | `*_CLASS` settings import and run operator-named code. Environment only; never derived from request data, adapter payloads, or tool arguments. |

## Triage of automated STRIDE findings

The decomposition above was also run through a threat-modeling tool, which
applied its knowledge-base threats per component *type*: 46 annotations
across six components. Roughly a third do not apply to this system.

That is expected behaviour, not a defect in the tool — it knows a component
is "an internal REST API", not what this particular one does. Importing the
output wholesale would produce a document that looks thorough and is partly
false, which is worse than a shorter honest one. Every entry below was
checked against the code.

**Dismissed, with reasons:**

| Flagged threat | Component | Why it does not apply |
| --- | --- | --- |
| SQL Injection | Core API, channel adapter | Neither component touches SQL. The only database in the system is the feedback store, which uses parameterized statements exclusively (`app/feedback.py`). |
| Code Injection | Core API, channel adapter | No `eval`, `exec`, shell invocation, or template rendering over request data. The one dynamic import in the system is operator configuration (TB5), which is never derived from a request. |
| Insecure Direct Object References | Core API, channel adapter | IDOR presupposes an authorization model to bypass. Every document is equally readable by every caller, so there is no object reference that could be *insecure* relative to a permission. If document-level authorization is ever added, this becomes applicable immediately, and `fetch(id)` is where it would bite. |
| Privilege Escalation via IDOR | Core API, channel adapter | Same reason. There are no privilege tiers to escalate between. |
| Cross-Site Request Forgery | Identity provider | CSRF requires ambient credentials, typically a cookie the browser attaches automatically. All authentication here is a bearer token or an HMAC signature, both of which must be constructed deliberately by the caller. No browser session exists. |

**Confirmed and folded into the tables above,** rather than repeated here:
Spoofing Identity, Authentication Bypass, Authorization Bypass, Information
Disclosure, Data Breach, Data Tampering, Configuration File Manipulation,
Insufficient Audit Logging, Supply Chain Code Tampering, JWT Token
Manipulation, OAuth Token Theft, OAuth Misconfiguration Exploitation, Session
Hijacking (as bearer replay), Sensitive Data Exposure in Logs, Unencrypted
Data Transmission, and container Privilege Escalation.

Two of the confirmed ones deserve naming because they are easy to wave away:

- **API Response Data Scraping.** `/v1/search` returns verbatim corpus chunks
  and, unauthenticated, allows an attacker to enumerate the entire corpus
  through repeated queries. Rate limiting raises the cost; only authentication
  removes the capability.
- **OAuth Misconfiguration Exploitation.** This is precisely the canonical-URL
  rule in `docs/mcp.md`. A token minted with the wrong `aud` either fails
  closed (safe) or, in a naive implementation that skips the audience check,
  lets a token issued for a different resource be replayed against this one.
  The audience check is mandatory here and covered by a test.

## OWASP Top 10 for LLM Applications (2025)

| ID | Applies here as | Status | Detail |
| --- | --- | --- | --- |
| LLM01 Prompt Injection | Indirect injection through retrieved documents (TB4) | **Partial, unverified** | The system prompt instructs the model to treat retrieved content and user text as untrusted data and to ignore embedded instructions or authority claims. The literature is clear that prompt-level defense alone is not sufficient (Greshake et al., *Not what you've signed up for*, [arXiv:2302.12173](https://arxiv.org/abs/2302.12173)). There is **no test evidence** in this repository that the defense holds; until an injection suite exists, treat this as an unproven claim. Maps to ATLAS AML.T0051. |
| LLM02 Sensitive Information Disclosure | Corpus content served to unauthenticated callers | Partial | Answers are constrained to retrieved context, but `/v1/search` returns raw chunks and the default deployment has no authentication. There is no document-level authorization: any caller who reaches retrieval reaches the whole corpus. |
| LLM03 Supply Chain | Python and model dependencies | Partial | Small dependency surface, Dependabot, CodeQL. No pinned hashes, no SBOM, no provenance attestation on releases. |
| LLM04 Data and Model Poisoning | Poisoned documents entering the index | Partial | Every chunk now carries its source identifier and source URL, so an answer can be traced back to where its text came from, and the index manifest detects post-build tampering. What remains unmitigated is the ingestion source itself: a `Source` that yields an attacker-controlled `Document` is still trusted completely, and no review step sits between it and the model's context. Maps to ATLAS AML.T0020. |
| LLM05 Improper Output Handling | Model output rendered into a chat client | Partial | Output is markdown converted to Slack `mrkdwn`; there is no HTML or shell sink. A model-emitted link is rendered as a link, so a poisoned corpus could surface an attacker-chosen URL to a user. |
| LLM06 Excessive Agency | Tools available to an agent | **Low by design** | Every MCP tool is read-only with respect to the outside world: `search`, `fetch`, `ask`. None writes to the corpus, spends money on the caller's behalf, or reaches another system. `ask` does populate the answer cache, which is internal state and is partitioned per caller (TB7), so it cannot be used to plant an answer for someone else. Read-only tools are the cheapest strong control here, and it was a design choice rather than an accident. |
| LLM07 System Prompt Leakage | Extraction of the system prompt | **Not applicable** | The prompts are files in this repository (`app/prompts/`). They are public by construction, so there is nothing to leak. Guardrails that depend on prompt secrecy were avoided for this reason. |
| LLM08 Vector and Embedding Weaknesses | Index manipulation, embedding inversion, retrieval-time access gaps | Partial | The cache authorization boundary is enforced (TB7). Index integrity is not (see STRIDE Tampering), and stored embeddings are recoverable to approximate source text by an attacker with file access. |
| LLM09 Misinformation | Confident, wrong answers | Partial | The grounding gate scores faithfulness against retrieved chunks, regenerates once under a stricter prompt, then delivers with an explicit low-confidence note. **The gate's effectiveness is unmeasured**: with the offline extractive generator the judge scores text against itself and always returns ≈1.0, which is a tautology, not evidence. |
| LLM10 Unbounded Consumption | Denial of wallet | Mitigated | Per-caller token-bucket rate limit (60 requests per minute by default) plus a 4,000-character question cap enforced by the request model, so oversized input never reaches a provider. The circuit breaker still only limits damage from provider *failure*; cost control is the rate limiter's job. A shared static bearer means all authenticated callers share one budget, which is a property of static-token auth, not of the limiter. |

## OWASP MCP Top 10 (2025, beta)

Applies only when the MCP server is enabled.

| ID | Status | Detail |
| --- | --- | --- |
| MCP01 Token Mismanagement | Mitigated | The inbound bearer authenticates the caller to this server and is never forwarded upstream. Secrets come from the environment and are not logged. |
| MCP02 Privilege Escalation via Scope Creep | Low | Read-only tools, optional required scopes enforced per request. Scope breadth is an operator decision. |
| MCP03 Tool Poisoning | Accepted by design | `MCP_EXTENSIONS_MODULE` imports exactly the module the operator names, with no plugin scanning. Tool descriptions are env-overridable, which is operator-trusted surface. |
| MCP04 Supply Chain | Partial | Same posture as LLM03. |
| MCP05 Command Injection | Mitigated | No model-supplied argument reaches a shell, a file path, or string-concatenated SQL. `fetch(id)` is an in-memory lookup; the feedback store uses parameterized queries. |
| MCP06 Intent Flow Subversion | Partial, unverified | Same underlying issue as LLM01, and the same lack of test evidence. |
| MCP07 Insufficient AuthN/AuthZ | Mitigated when enabled, **absent by default** | Full resource-server validation with the canonical-URL rule; missing scope returns 403 rather than 401. `MCP_AUTH_MODE` defaults to `off`, which is correct for a local demo and wrong for anything reachable. |
| MCP08 Lack of Audit and Telemetry | Mitigated | Every tool call emits a structured JSON event: caller subject, tool name, per-argument length and keyed digest, outcome, error class, duration. The HTTP surface emits a matching `rag.ask` event carrying the retrieved chunk and document ids, cache scope, grounding verdict, and a correlating `request_id`. Raw question text, secrets, and bearer tokens are never logged, asserted by test. Extension tools registered through `MCP_EXTENSIONS_MODULE` are covered because the wrapper is installed on `server.tool` itself. **This row was previously false**: nothing configured the `grounded_rag` logger, so under uvicorn the tree inherited WARNING with no handlers and every event was discarded at runtime while the tests passed on a `caplog` handler the runtime never had. Startup now configures the sink unconditionally (`tests/test_audit_reaches_sink.py`). Shipping events to tamper-resistant storage remains a deployment concern. |
| MCP09 Shadow MCP Servers | Out of scope | Organizational control, not a property of this software. |
| MCP10 Context Injection and Over-Sharing | Mitigated | Answer cache is partitioned by the verified token subject, so one caller's answer cannot be served to another; no conversation state persists server-side (`history` is client-held, which keeps the core stateless). Covered by `tests/test_mcp_identity.py`, including that claims do not leak between requests. |

## What this system does not protect against

Stated plainly, because a threat model that only lists wins is marketing.

- **Document-level authorization.** There is none. Every caller who can
  retrieve can retrieve everything. Do not point this at a corpus with mixed
  sensitivity and expect per-user filtering.
- **A poisoned ingestion source.** The index is now integrity-checked after
  the fact and every chunk records its provenance, but a source that yields
  attacker-controlled documents is still trusted at build time. Corpus
  integrity remains the highest-value target in the system.
- **Prompt injection, verifiably.** Defenses exist; evidence does not.
- **A compromised host or index file.** Write access to `data/` is equivalent
  to control over every answer.
- **Data residency.** Retrieved chunks are sent to whichever provider is
  configured. With the offline defaults nothing leaves the process; with
  `GENERATION_PROVIDER=openai` your corpus content does.

## Assumptions

1. The operator is trusted. Environment variables, including the dotted-path
   class settings, are operator-controlled and never derived from user input.
2. The corpus is public or non-sensitive in the default configuration.
3. TLS is terminated by a reverse proxy; this service does not terminate TLS.
4. The identity provider behind MCP authorization is correctly configured,
   in particular that it mints `aud` equal to the canonical resource URL.

## Residual risk, prioritized

| Priority | Risk | Cheapest meaningful fix | State |
| --- | --- | --- | --- |
| 1 | Unverified injection resistance (LLM01, MCP06) | An injection suite run against a real model, published as a dated artifact | Open |
| 2 | No document-level authorization | Filter inside the retriever, never after ranking; the cache boundary (TB7) is already in place for it | Open, deliberately |
| 3 | Trusted ingestion source (LLM04) | Signed or reviewed source documents; provenance is recorded but not verified | Open |
| 4 | No authentication by default | A static bearer is not user authentication; real deployments need an authenticating proxy | Documented |
| — | Unbounded consumption (LLM10) | Rate limit and question length cap | **Closed** |
| — | No audit trail (MCP08) | Structured audit events on both surfaces, with a configured sink | **Closed** |
| — | Index tampering | Manifest with sha256 verified on load | **Closed** |

Item 1 is the one worth doing next: it converts the weakest claim in this
document into evidence, and it is the only remaining item whose status rests
on an argument rather than a test. Item 2 is open on purpose. A convincing
implementation needs an identity provider and a permission model to filter
against; building it without one would produce something that looks like
authorization and enforces nothing, which is worse than its absence.

---

Last reviewed 2026-08-17 against the framework versions cited above. This
document describes the code in this repository at that date; re-verify before
relying on it.
