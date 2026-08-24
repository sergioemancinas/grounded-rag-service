"""Audit events must escape the process, not merely be logged.

Every prior audit test used `caplog`, which installs a handler. The runtime
never had one: under uvicorn's logging config the `grounded_rag` tree inherits
level WARNING with no root handlers, so every INFO audit event was discarded
while the suite stayed green and the threat model claimed the control was in
place. These tests assert the configuration, not the call.
"""

from __future__ import annotations

import io
import json
import logging

from fastapi.testclient import TestClient

from app.audit import ROOT_LOGGER_NAME, configure_audit_logging, emit
from app.config import Settings
from app.main import create_app


def reset_tree() -> logging.Logger:
    app_logger = logging.getLogger(ROOT_LOGGER_NAME)
    for handler in list(app_logger.handlers):
        app_logger.removeHandler(handler)
    app_logger.setLevel(logging.NOTSET)
    app_logger.propagate = True
    return app_logger


def test_unconfigured_tree_would_swallow_info(monkeypatch) -> None:
    """The precondition that made the original defect invisible."""
    app_logger = reset_tree()
    logging.getLogger().setLevel(logging.WARNING)

    assert not app_logger.isEnabledFor(logging.INFO)


def test_configure_makes_audit_events_observable() -> None:
    reset_tree()
    logging.getLogger().setLevel(logging.WARNING)
    stream = io.StringIO()

    configure_audit_logging(stream)
    emit("test.event", request_id="abc")

    payload = json.loads(stream.getvalue().strip())
    assert payload["event"] == "test.event"
    assert payload["request_id"] == "abc"


def test_creating_the_app_configures_audit_logging() -> None:
    """Startup must not depend on the operator remembering a config line."""
    app_logger = reset_tree()
    logging.getLogger().setLevel(logging.WARNING)

    create_app(Settings())

    assert app_logger.handlers, "create_app must attach a handler"
    assert app_logger.isEnabledFor(logging.INFO)


def test_operator_configuration_is_not_overridden() -> None:
    app_logger = reset_tree()
    existing = logging.StreamHandler(io.StringIO())
    app_logger.addHandler(existing)

    configure_audit_logging()

    assert app_logger.handlers == [existing]


def test_ask_emits_a_retrieval_audit_event() -> None:
    reset_tree()
    stream = io.StringIO()
    configure_audit_logging(stream)
    app = create_app(Settings())

    with TestClient(app) as client:
        response = client.post("/v1/ask", json={"question": "Which values does fulfillment_type accept?"})

    events = [json.loads(line) for line in stream.getvalue().splitlines() if '"rag.ask"' in line]
    assert len(events) == 1
    event = events[0]
    assert event["request_id"] == response.json()["request_id"]
    assert event["n_chunks"] > 0
    assert event["doc_ids"]
    assert "fulfillment_type" not in stream.getvalue(), "raw question text must never be logged"
