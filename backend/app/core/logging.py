"""One JSON record per line; diagnostic metadata only, never arbitrary log messages."""

import json
import logging
import math
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from uuid import UUID

from app.core.config import ROOT, settings

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
ENUMS = {
    "provider": {"osm", "osrm", "redis", "llama_cpp", "embeddings", "qdrant"},
    "cache_state": {"hit", "miss", "invalid", "unavailable", "stored", "skipped", "bypassed"},
    "method": {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "OTHER"},
    "operation": {
        "parse",
        "explain",
        "structured",
        "search",
        "index",
        "single",
        "alternatives",
        "demo",
        "catalog",
        "query",
        "documents",
    },
    "algorithm": {"greedy", "beam", "cp-sat"},
    "solver_status": {"OPTIMAL", "FEASIBLE", "UNKNOWN", "INFEASIBLE", "MODEL_INVALID"},
    "mode": {"llm", "template"},
    "reason": {
        "timeout",
        "connect_error",
        "http_status_error",
        "transport_error",
        "invalid_response",
        "llm_unavailable",
        "unverified_output",
        "cancelled",
        "response_incomplete",
        "invalid_cache",
        "cache_unavailable",
    },
    "data_source": {"synthetic-demo-v1", "osm-osrm-lisbon-v2"},
}
COUNTS = {
    "attempt",
    "status_code",
    "days",
    "candidate_count",
    "poi_count",
    "eligible_count",
    "route_count",
    "visit_count",
    "participant_count",
    "violation_count",
    "question_count",
    "warning_count",
    "fact_count",
    "batch_size",
    "vector_count",
    "dimensions",
    "trip_id",
    "route_id",
    "route_set_id",
    "ttl_seconds",
    "prompt_tokens",
    "completion_tokens",
    "version_id",
}
FLAGS = {"feasible", "validation_valid", "will_retry", "reused", "query"}
NUMBERS = {"duration_ms", "cache_age_seconds"}
SERVER_EVENTS = {
    "Started server process [%d]": "server.started",
    "Waiting for application startup.": "server.starting",
    "Application startup complete.": "server.ready",
    "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)": "server.listening",
    "Shutting down": "server.stopping",
    "Waiting for application shutdown.": "server.shutdown.waiting",
    "Application shutdown complete.": "server.shutdown.completed",
    "Finished server process [%d]": "server.stopped",
    "Will watch for changes in these directories: %s": "server.reload.watching",
    "Started reloader process [%d] using %s": "server.reload.started",
    "Stopping reloader process [%d]": "server.reload.stopped",
    "Exception in ASGI application\n": "server.request.failed",
}


def valid_request_id(value):
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def safe_fields(fields):
    """Whitelist field names AND enum values; do not serialize unknown objects or repr()."""
    result = {}
    if not isinstance(fields, dict):
        return result
    for key, value in fields.items():
        if key in ENUMS and isinstance(value, str) and value in ENUMS[key]:
            result[key] = value
        elif key in COUNTS and type(value) is int and 0 <= value <= 10**12:
            result[key] = value
        elif key in FLAGS and type(value) is bool:
            result[key] = value
        elif (
            key in NUMBERS
            and type(value) in (int, float)
            and math.isfinite(value)
            and 0 <= value <= 10**12
        ):
            result[key] = round(value, 3)
        elif key == "model" and isinstance(value, str):
            result[key] = (
                value if value in {"Qwen3-4B-Q4_K_M", "Qwen3-Embedding-0.6B-Q8_0"} else "custom"
            )
        elif (
            key == "route"
            and isinstance(value, str)
            and len(value) <= 200
            and re.fullmatch(r"/[a-zA-Z0-9_/{\}:.-]*|<unmatched>", value)
        ):
            result[key] = value
    return result


def error_kind(exc):
    # Imports stay out of configuration/bootstrap, where no HTTP call is made.
    import httpx

    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, httpx.HTTPStatusError):
        return "http_status_error"
    return "transport_error" if isinstance(exc, httpx.HTTPError) else "invalid_response"


def exception_metadata(exc):
    """No exception text, source lines, locals, URLs, SQL parameters or chained messages."""
    frames, tb = [], exc.__traceback__
    while tb is not None and len(frames) < 24:
        filename = Path(tb.tb_frame.f_code.co_filename)
        try:
            filename = filename.resolve().relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            filename = "external/" + filename.name
        frames.append(
            {"file": filename, "line": tb.tb_lineno, "function": tb.tb_frame.f_code.co_name}
        )
        tb = tb.tb_next
    causes, current, seen = [], exc, set()
    while current is not None and id(current) not in seen and len(causes) < 5:
        seen.add(id(current))
        item = {"error_type": type(current).__name__}
        if isinstance(current, OSError) and type(current.errno) is int:
            item["errno"] = current.errno
        causes.append(item)
        # Include hidden contexts by TYPE/code only (HTTP adapters often suppress them).
        current = current.__cause__ or current.__context__
    return {"error_type": type(exc).__name__, "frames": frames, "causes": causes}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        event_name = getattr(record, "event_name", None)
        application_event = (
            record.name.startswith("app.")
            and isinstance(event_name, str)
            and re.fullmatch(r"[a-z][a-z0-9_.]{0,79}", event_name)
        )
        rid = valid_request_id(getattr(record, "request_id", None)) or request_id.get()
        if not rid and record.exc_info:
            rid = valid_request_id(getattr(record.exc_info[1], "_travel_request_id", None))
        data = {
            "schema_version": 1,
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": "WARN" if record.levelno == logging.WARNING else record.levelname,
            "service": "travel-planner-backend",
            "process_id": record.process,
            "logger": record.name,
            "event": event_name if application_event else "library.event",
            "request_id": rid,
        }
        if application_event:
            data.update(safe_fields(getattr(record, "event_fields", {})))
        elif record.name.startswith("uvicorn") and isinstance(record.msg, str):
            data["event"] = SERVER_EVENTS.get(record.msg, "server.event")
            if re.fullmatch(
                r"Started reloader process \[\d+\] using (WatchFiles|StatReload)", record.msg
            ):
                data["event"] = "server.reload.started"
            elif re.fullmatch(r"Stopping reloader process \[\d+\]", record.msg):
                data["event"] = "server.reload.stopped"
        if record.exc_info and isinstance(record.exc_info[1], BaseException):
            data.update(exception_metadata(record.exc_info[1]))
        return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class SafeHandlerErrors:
    def handleError(self, record):
        # logging's default handleError dumps msg/args; those may contain private data.
        try:
            emergency = {
                "schema_version": 1,
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "level": "ERROR",
                "service": "travel-planner-backend",
                "process_id": record.process,
                "logger": "app.core.logging",
                "event": "logging.write.failed",
                "request_id": valid_request_id(getattr(record, "request_id", None))
                or request_id.get(),
            }
            sys.stderr.write(json.dumps(emergency, separators=(",", ":")) + "\n")
        except Exception:
            pass


class SafeStreamHandler(SafeHandlerErrors, logging.StreamHandler):
    pass


class SafeRotatingFileHandler(SafeHandlerErrors, RotatingFileHandler):
    pass


def emit(logger, name, level=logging.INFO, exception=None, **fields):
    logger.log(
        level,
        name,
        extra={"event_name": name, "event_fields": fields, "request_id": request_id.get()},
        exc_info=(type(exception), exception, exception.__traceback__) if exception else None,
    )


@contextmanager
def span(logger, name, **fields):
    started = perf_counter()
    result = {}
    try:
        yield result
    except Exception as exc:
        emit(
            logger,
            name + ".failed",
            logging.ERROR,
            exception=exc,
            duration_ms=(perf_counter() - started) * 1000,
            **fields,
        )
        raise
    else:
        emit(
            logger,
            name + ".completed",
            duration_ms=(perf_counter() - started) * 1000,
            **{**fields, **result},
        )


def configure_logging(config=settings, *, stream=None, file_enabled=True, file_tag=None):
    """Idempotent bootstrap. Preserve test-capture handlers; own handler errors are safe."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if getattr(handler, "_travel_handler", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(config.log_level)
    handlers = [SafeStreamHandler(stream or sys.stderr)]
    file_error = None
    if file_enabled and config.log_file:
        path = Path(config.log_file)
        if file_tag is not None:
            if file_tag not in {
                "llm-evaluation",
                "retrieval-evaluation",
                "rag-evaluation",
                "catalog-index",
                "catalog-refresh",
            }:
                raise ValueError("Unknown log file tag")
            path = path.with_name(f"{path.stem}-{file_tag}{path.suffix}")
        if not path.is_absolute():
            path = ROOT / path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                SafeRotatingFileHandler(
                    path,
                    maxBytes=config.log_max_bytes,
                    backupCount=config.log_backup_count,
                    encoding="utf-8",
                )
            )
        except OSError as exc:
            file_error = exc
    for handler in handlers:
        handler._travel_handler = True
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    # Uvicorn's access line includes raw URL/IP. Our ASGI middleware logs route templates.
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "httpx",
        "httpx2",
        "httpcore",
        "sqlalchemy.engine",
        "watchfiles",
    ):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.disabled = False
        logger.setLevel(config.log_level if name.startswith("uvicorn") else logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
    if file_error:
        emit(
            logging.getLogger(__name__),
            "logging.file.unavailable",
            logging.WARNING,
            exception=file_error,
        )
