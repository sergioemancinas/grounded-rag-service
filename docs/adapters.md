# Channel adapters

An adapter connects one chat surface to the pipeline. It has exactly two
jobs: turn an inbound platform event into an `AskRequest`, and render the
returned `AskResponse` into whatever the platform speaks.

The core never learns the platform exists. Adapters never import
`PipelineDeps`, the retriever, or anything else from inside the pipeline.
The entire contract between them is:

```python
AskFn = Callable[[AskRequest], Awaitable[AskResponse]]
```

## Two ways to build one

**In-process** (Python, mounted on the same service). You get an `AskFn`
injected, and you export a router:

```python
def create_router(ask: AskFn, settings: Settings) -> APIRouter: ...
```

Add one line to `mount_channels()` in `app/main.py` and it mounts on
startup. Reference implementation: `app/channels/slack.py`. Minimal
implementation: `examples/adapter_discord.py`.

**Out-of-process** (any language). Receive the platform webhook wherever
you like, call `POST /v1/ask`, post the answer back. Nothing in this
repository needs to change, and the adapter can be a serverless function, an
existing bot, or a workflow tool.

`app/channels/http_client.py` is the proof these are the same thing: it
builds an `AskFn` that calls a remote `/v1/ask`, so an adapter written
against the seam works either way. `examples/adapter_cli.py` switches
between them with one environment variable.

## The contract

Request:

```json
{
  "question": "How do refunds work?",
  "history": ["earlier turn", "..."],
  "conversation_id": "C123:1712345678.9",
  "user_id": "U123",
  "channel": "slack",
  "metadata": {}
}
```

`history` is client-held, which keeps the core stateless: the adapter owns
thread state because only the adapter knows what a thread means on its
platform. `conversation_id` is opaque; the Slack adapter builds
`channel:thread_ts`.

Response:

```json
{
  "answer": "markdown with [1] style citations",
  "citations": [{"number": "1", "title": "...", "url": "..."}],
  "sources": [{"id": "c1", "title": "...", "url": "...", "heading_path": [], "score": 0.82}],
  "grounding": {"score": 0.91, "passed": true},
  "intent": "knowledge",
  "cached": false,
  "followups": [],
  "timings": {"retrieve": 0.02, "generate": 1.4},
  "request_id": "0f0c..."
}
```

`answer` is always markdown. Keep `request_id`: it is what
`POST /v1/feedback` references, and it appears in logs for correlation.

## The three rules

**1. Verify signatures on the raw request bytes, before parsing.**

Every platform signs the exact bytes it sent. If you parse JSON and
re-serialize it, whitespace and key order change and your recomputed digest
will not match, so read the body first:

```python
body = await request.body()
if not verify_slack_signature(secret, timestamp, body, signature):
    raise HTTPException(status_code=401, detail="invalid signature")
payload = json.loads(body)
```

Fail closed. An unset signing secret must reject everything, not accept
everything: a misconfigured deployment answering nothing is recoverable,
one accepting forged requests is not.

**2. Acknowledge fast, answer in the background.**

Slack retries after 3 seconds, Discord after 3, Teams after 15. A pipeline
run takes longer than that, and a retried webhook becomes a duplicate
answer. Ack immediately, post a placeholder, then edit it:

```python
background_tasks.add_task(process_event, ask, settings, breaker, event)
return JSONResponse({"ok": True})
```

The placeholder-then-edit pattern is also why the core does not stream:
chat surfaces want one message that updates, not a token feed.

**3. Deduplicate retries locally.**

Retry semantics differ per platform (Slack sends `X-Slack-Retry-Num` plus a
stable `event_id`), so dedup belongs in the adapter. `SeenEventSet` in
`app/channels/slack.py` is a TTL set of event ids; on a retry of an event
already seen, return early.

A fourth rule that only bites in production: **never answer your own
messages.** An assistant posting into a channel it also listens to will
answer itself forever. The Slack adapter drops events carrying `bot_id` or
a `subtype`.

## Walkthrough: the Slack adapter

`app/channels/slack.py`, in order:

1. `verify_slack_signature` recomputes `v0:timestamp:body` HMAC-SHA256,
   compares with `hmac.compare_digest`, and rejects anything older than five
   minutes (replay window).
2. `url_verification` challenges are echoed, so Slack can validate the
   endpoint.
3. `SeenEventSet` drops retries of events already handled.
4. Bot messages and message subtypes are ignored.
5. `ALLOWED_CHANNEL_IDS`, when set, restricts which channels get answers.
6. The route returns `{"ok": true}` immediately and queues `process_event`.
7. `process_event` posts the placeholder, awaits `ask(...)`, renders Block
   Kit via `slack_render.py`, and edits the placeholder with `chat.update`.
8. Feedback buttons post to `/slack/interactions`, which verifies the
   signature again and forwards a verdict to the core's feedback store.

Outbound Slack calls use an adapter-local circuit breaker, deliberately
separate from the pipeline's, so a Slack outage does not trip the breaker
protecting the model provider.

The adapter mounts only when `SLACK_SIGNING_SECRET` is set. With it unset
the service logs `slack adapter: not mounted` at startup and runs as a plain
HTTP API. Deleting Slack support entirely means deleting `slack.py`,
`slack_render.py`, `requirements-slack.txt`, and one line in `main.py`.
