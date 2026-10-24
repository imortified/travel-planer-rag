"""Logging is observable behavior: correlation, privacy, failures and bounded storage."""

import asyncio
import io
import json
import logging
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from cache_helpers import MemoryRedis
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from app.core.config import Settings
from app.core.logging import (
    JsonFormatter,
    SafeStreamHandler,
    configure_logging,
    emit,
    request_id,
    valid_request_id,
)
from app.core.request_logging import RequestLoggingMiddleware, unhandled_error
from app.integrations.http_cache import RedisHttpCache, cached_get
from app.integrations.llama_cpp import LlamaCpp, LlmUnavailable
from app.main import app as production_app
from app.nlp.explanations import explain

SECRET = "PRIVATE_PROMPT_PASSWORD_93821"
logger = logging.getLogger("app.tests.logging")


@pytest.fixture
def records():
    stream = io.StringIO()
    handler = SafeStreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous)


@pytest.fixture
def application():
    api = FastAPI()
    api.add_middleware(RequestLoggingMiddleware)
    api.add_exception_handler(Exception, unhandled_error)

    @api.get("/items/{item_id}")
    def item(item_id: str):
        emit(logger, "test.sync", operation="search")
        return {"id": request_id.get()}

    @api.get("/async/{item_id}")
    async def asynchronous(item_id: str):
        before = request_id.get()
        await asyncio.sleep(0.002)
        assert request_id.get() == before
        emit(logger, "test.async")
        return {"id": before}

    @api.get("/failure")
    def failure():
        raise RuntimeError(SECRET)

    @api.get("/handled")
    def handled():
        raise HTTPException(503, SECRET)

    @api.get("/stream")
    def stream():
        async def content():
            emit(logger, "test.stream")
            yield b"a"
            await asyncio.sleep(0.002)
            yield b"b"

        return StreamingResponse(content(), headers={"X-Request-ID": SECRET})

    return api


def test_sync_endpoint_response_and_log_share_id_without_url_body_headers_or_ip(
    application, records
):
    rid = str(uuid4())
    with TestClient(application) as client:
        response = client.get(
            f"/items/{SECRET}?token={SECRET}",
            headers={"X-Request-ID": rid, "Authorization": SECRET, "Cookie": SECRET},
        )
    assert response.headers["x-request-id"] == response.json()["id"] == rid
    events = records()
    assert {r["request_id"] for r in events} == {rid}
    assert [r["event"] for r in events] == ["test.sync", "http.request.completed"]
    assert events[-1]["route"] == "/items/{item_id}"
    assert events[-1]["status_code"] == 200 and events[-1]["duration_ms"] >= 0
    assert SECRET not in json.dumps(events)
    assert request_id.get() is None


@pytest.mark.parametrize("supplied", ["bad-id", "x" * 2000, "\r\n" + SECRET, "", b"\xff"])
def test_untrusted_request_ids_are_replaced(application, records, supplied):
    with TestClient(application) as client:
        response = client.get("/items/a", headers={"X-Request-ID": supplied})
    rid = response.headers["x-request-id"]
    assert valid_request_id(rid) == rid
    assert all(r["request_id"] == rid for r in records())
    assert SECRET not in json.dumps(records())


def test_duplicate_request_ids_do_not_ambiguously_correlate(application, records):
    ids = [str(uuid4()), str(uuid4())]
    with TestClient(application) as client:
        response = client.get("/items/a", headers=[("X-Request-ID", rid) for rid in ids])
    assert response.headers["x-request-id"] not in ids
    assert len({r["request_id"] for r in records()}) == 1


@pytest.mark.parametrize("endpoint", ["items", "async"])
def test_concurrent_requests_are_isolated_in_async_and_sync_workers(application, records, endpoint):
    ids = [str(uuid4()) for _ in range(12)]

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.get(f"/{endpoint}/{i}", headers={"X-Request-ID": rid})
                    for i, rid in enumerate(ids)
                )
            )
        assert [r.json()["id"] for r in responses] == ids
        assert request_id.get() is None

    asyncio.run(run())
    for rid in ids:
        events = [r for r in records() if r["request_id"] == rid]
        assert len(events) == 2 and events[-1]["event"] == "http.request.completed"


@pytest.mark.parametrize("endpoint,status", [("failure", 500), ("handled", 503), (SECRET, 404)])
def test_errors_keep_header_and_hide_messages_and_unknown_paths(
    application, records, endpoint, status
):
    rid = str(uuid4())
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(f"/{endpoint}", headers={"X-Request-ID": rid})
    assert response.status_code == status and response.headers["x-request-id"] == rid
    event = records()[-1]
    assert event["status_code"] == status and event["request_id"] == rid
    assert event["level"] == ("WARN" if status < 500 else "ERROR")
    assert SECRET not in json.dumps(records())
    if status == 500:
        assert response.json() == {"detail": "Internal server error"}
        assert event["error_type"] == "RuntimeError" and event["frames"]


def test_streaming_finishes_before_completion_record_and_overrides_foreign_header(
    application, records
):
    with TestClient(application) as client:
        response = client.get("/stream")
    assert response.content == b"ab" and valid_request_id(response.headers["x-request-id"])
    assert [r["event"] for r in records()] == ["test.stream", "http.request.completed"]
    assert SECRET not in json.dumps(records())


def test_cancelled_request_resets_context_and_is_not_reported_as_completed(records):
    async def cancel(scope, receive, send):
        raise asyncio.CancelledError()

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await RequestLoggingMiddleware(cancel)(
                {"type": "http", "method": "GET", "headers": []}, None, None
            )
        assert request_id.get() is None

    asyncio.run(run())
    assert records()[-1]["event"] == "http.request.cancelled"
    assert records()[-1]["status_code"] == 499


@pytest.mark.parametrize("scope_type", ["lifespan", "websocket"])
def test_non_http_scopes_pass_through(scope_type, records):
    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    asyncio.run(RequestLoggingMiddleware(downstream)({"type": scope_type}, None, None))
    assert seen == [scope_type] and not records()


def test_production_planner_logs_safe_summary_with_same_request_id(records):
    with TestClient(production_app) as client:
        response = client.post(
            "/api/v1/plans/demo?algorithm=cp-sat",
            json={"participants": [{"id": SECRET, "interests": {"history": 1}}], "budget": 10000},
        )
    assert response.status_code == 200
    event = next(r for r in records() if r["event"] == "planning.solve.completed")
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["algorithm"] == "cp-sat" and event["solver_status"] == "OPTIMAL"
    assert event["feasible"] and event["validation_valid"]
    assert SECRET not in json.dumps(records())


def test_documentation_route_is_named_without_logging_its_query(records):
    with TestClient(production_app) as client:
        response = client.get(f"/docs?token={SECRET}")
    assert response.status_code == 200
    assert records()[-1]["route"] == "/docs" and SECRET not in json.dumps(records())


def test_cache_retry_hit_and_upstream_request_are_correlated_without_url_or_payload(
    records, monkeypatch
):
    rid = str(uuid4())
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout(SECRET, request=request)
        return httpx.Response(200, json={"value": SECRET})

    monkeypatch.setattr("app.integrations.http_cache.time.sleep", lambda *_: None)
    cache = RedisHttpCache(MemoryRedis())
    token = request_id.set(rid)
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            for _ in range(2):
                result, _, _ = cached_get(
                    client,
                    f"https://example.org/{SECRET}",
                    {"token": SECRET},
                    60,
                    cache,
                    "osm",
                    json.loads,
                )
                assert result["value"] == SECRET
    finally:
        request_id.reset(token)
    assert len(requests) == 2 and all(r.headers["x-request-id"] == rid for r in requests)
    events = records()
    assert all(r["request_id"] == rid for r in events)
    failed = next(r for r in events if r["event"] == "upstream.request.failed")
    assert failed["reason"] == "timeout" and failed["will_retry"] and failed["attempt"] == 1
    assert events[-1]["cache_state"] == "hit" and "cache_age_seconds" in events[-1]
    assert SECRET not in json.dumps(events)


def test_llm_repair_logs_only_metadata_and_propagates_id(records):
    class Answer(BaseModel):
        value: int

    requests = []

    def handler(request):
        requests.append(request)
        content = {"value": SECRET if len(requests) == 1 else 2}
        return httpx.Response(
            200,
            json={
                "model": SECRET,
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    rid = str(uuid4())
    token = request_id.set(rid)
    try:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result, _ = LlamaCpp(client).structured(SECRET, {"text": SECRET}, Answer)
    finally:
        request_id.reset(token)
    assert result.value == 2 and all(r.headers["x-request-id"] == rid for r in requests)
    assert [r["event"] for r in records()] == ["llm.output.rejected", "llm.call.completed"]
    assert records()[0]["will_retry"] and records()[-1]["attempt"] == 2
    assert records()[-1]["prompt_tokens"] == 10 and SECRET not in json.dumps(records())


def test_explanation_fallback_is_observable_without_participant_name(records):
    class Down:
        def structured(self, *args):
            raise LlmUnavailable(SECRET)

    route = SimpleNamespace(
        id=1,
        route_set_id=1,
        comparison={"participant_utility": {"1": 1}, "utility_gap": 0},
        result={"visits": [{"place_id": "museum"}], "total_cost": 1500, "data_context": None},
    )
    trip = SimpleNamespace(
        budget=5000,
        participants_snapshot=[{"user_id": 1, "name": SECRET}],
        travel_date=date(2026, 10, 24),
        end_date=None,
        mandatory=["museum"],
        excluded=[],
    )
    output = explain(route, trip, route, 3, Down())
    assert output.mode == "template" and records()[-1]["mode"] == "template"
    assert records()[-1]["reason"] == "llm_unavailable" and SECRET not in json.dumps(records())


def test_formatter_drops_unknown_fields_raw_messages_args_and_invalid_numbers():
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, SECRET, (SECRET,), None)
    record.event_name = "test.fields"
    record.event_fields = {
        "prompt": SECRET,
        "password": SECRET,
        "response": SECRET,
        "operation": SECRET,
        "model": SECRET,
        "duration_ms": float("nan"),
        "days": True,
        "route_count": 3,
    }
    result = JsonFormatter().format(record)
    value = json.loads(result)
    assert SECRET not in result and "duration_ms" not in value and "days" not in value
    assert value["model"] == "custom" and value["route_count"] == 3
    foreign = logging.LogRecord(
        "sqlalchemy.engine", logging.ERROR, __file__, 1, "SQL %s", (SECRET,), None
    )
    assert SECRET not in JsonFormatter().format(foreign)


def test_server_error_after_context_reset_keeps_id_and_safe_cause_codes():
    rid = str(uuid4())
    cause = OSError(10061, SECRET)
    error = RuntimeError(SECRET)
    error.__cause__ = cause
    error._travel_request_id = rid
    record = logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        __file__,
        1,
        "Exception in ASGI application\n",
        (),
        (RuntimeError, error, None),
    )
    rendered = JsonFormatter().format(record)
    value = json.loads(rendered)
    assert value["request_id"] == rid and value["event"] == "server.request.failed"
    assert value["causes"][-1]["errno"] == 10061 and SECRET not in rendered


@pytest.fixture
def isolated_configuration():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    names = (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "httpx",
        "httpcore",
        "sqlalchemy.engine",
        "watchfiles",
    )
    snapshots = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
            logging.getLogger(name).disabled,
        )
        for name in names
    }
    root.handlers = []
    try:
        yield
    finally:
        for handler in root.handlers[:]:
            if getattr(handler, "_travel_handler", False):
                handler.close()
        root.handlers = handlers
        root.setLevel(level)
        for name, (old_handlers, old_level, propagate, disabled) in snapshots.items():
            current = logging.getLogger(name)
            current.handlers = old_handlers
            current.setLevel(old_level)
            current.propagate, current.disabled = propagate, disabled


def config(**overrides):
    return SimpleNamespace(
        **{
            "log_level": "INFO",
            "log_file": None,
            "log_max_bytes": 1024,
            "log_backup_count": 2,
            **overrides,
        }
    )


def test_configuration_is_idempotent_and_uvicorn_has_one_safe_access_event(isolated_configuration):
    stream = io.StringIO()
    configure_logging(config(), stream=stream)
    configure_logging(config(), stream=stream)
    emit(logger, "test.once")
    logging.getLogger("uvicorn.error").info("Started server process [%d]", 123)
    logging.getLogger("uvicorn.access").info(SECRET)
    lines = [json.loads(s) for s in stream.getvalue().splitlines()]
    assert [r["event"] for r in lines] == ["test.once", "server.started"]
    assert (
        sum(bool(getattr(h, "_travel_handler", False)) for h in logging.getLogger().handlers) == 1
    )


def test_json_file_rotates_with_bounded_backups(tmp_path, isolated_configuration):
    path = tmp_path / "events.log"
    configure_logging(config(log_file=str(path)), stream=io.StringIO())
    for _ in range(50):
        emit(logger, "test.rotate", duration_ms=1)
    files = list(tmp_path.glob("events.log*"))
    assert len(files) == 3 and all(p.stat().st_size <= 1024 for p in files)
    for file in files:
        assert all(
            json.loads(line)["event"] == "test.rotate" for line in file.read_text().splitlines()
        )


def test_reload_parent_skips_file_and_cli_has_separate_rotating_file(
    tmp_path, isolated_configuration
):
    path = tmp_path / "backend.log"
    configure_logging(config(log_file=str(path)), stream=io.StringIO(), file_enabled=False)
    emit(logger, "test.parent")
    assert not path.exists()
    configure_logging(config(log_file=str(path)), stream=io.StringIO(), file_tag="llm-evaluation")
    emit(logger, "test.cli")
    assert not path.exists()
    assert json.loads((tmp_path / "backend-llm-evaluation.log").read_text())["event"] == "test.cli"


def test_file_unavailable_leaves_console_working_and_hides_os_error(
    isolated_configuration, monkeypatch
):
    def broken(*args, **kwargs):
        raise PermissionError(SECRET)

    monkeypatch.setattr("app.core.logging.SafeRotatingFileHandler", broken)
    stream = io.StringIO()
    configure_logging(config(log_file=".local/logs/test.log"), stream=stream)
    emit(logger, "test.alive")
    events = [json.loads(s) for s in stream.getvalue().splitlines()]
    assert [r["event"] for r in events] == ["logging.file.unavailable", "test.alive"]
    assert SECRET not in stream.getvalue()


def test_handler_io_error_never_uses_default_message_dump(capsys):
    class BrokenStream:
        def write(self, text):
            raise OSError(SECRET)

        def flush(self):
            pass

    handler = SafeStreamHandler(BrokenStream())
    handler.setFormatter(JsonFormatter())
    handler.handle(logging.LogRecord("httpx", logging.ERROR, __file__, 1, SECRET, (SECRET,), None))
    output = capsys.readouterr().err
    assert json.loads(output)["event"] == "logging.write.failed" and SECRET not in output
    assert json.loads(output)["schema_version"] == 1 and "request_id" in json.loads(output)


def test_log_settings_validate_level_rotation_limits_and_empty_file():
    assert Settings(_env_file=None, log_file="").log_file == ""
    with pytest.raises(ValueError):
        Settings(_env_file=None, log_level="TYPO")
    with pytest.raises(ValueError):
        Settings(_env_file=None, log_max_bytes=0)
