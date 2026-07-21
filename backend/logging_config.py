"""Structured logging helpers, correlation IDs, and canonical events.

Policy: readable structured format for local diagnosis.

Format:
  event=<event_name> correlation_id=<uuid> [key=value ...]

Canonical events:
  app.starting, app.ready
  backend.starting, backend.ready, backend.unhealthy, backend.crashed
  backend.stopping, backend.stopped
  scraper.starting, scraper.running, scraper.cancelled
  scraper.failed, scraper.completed
  playwright.inspect, playwright.installing, playwright.ready
  health.requested
  diagnostics.exported
"""

import logging
import os
import threading
import uuid
from collections.abc import Mapping
from typing import Any

CORRELATION_ID_ENV_VAR = "PROSPECTOS_CORRELATION_ID"

_log_context = threading.local()


def get_correlation_id() -> str:
    cid = getattr(_log_context, "correlation_id", None)
    if cid:
        return cid
    cid = os.environ.get(CORRELATION_ID_ENV_VAR)
    if cid:
        return cid
    return ""


def set_correlation_id(cid: str | None = None) -> str:
    if cid:
        validated = _validate_correlation_id(cid)
    else:
        validated = _generate_correlation_id()
    _log_context.correlation_id = validated
    return validated


def clear_correlation_id() -> None:
    if hasattr(_log_context, "correlation_id"):
        del _log_context.correlation_id


def _generate_correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def _validate_correlation_id(cid: str) -> str:
    if not cid or not isinstance(cid, str):
        return _generate_correlation_id()
    cleaned = cid.strip()
    if len(cleaned) < 4 or len(cleaned) > 64:
        return _generate_correlation_id()
    if not cleaned.replace("-", "").replace("_", "").isalnum():
        return _generate_correlation_id()
    return cleaned


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        cid = get_correlation_id()
        record.correlation_id = cid if cid else "-"
        return True


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        parts = [
            self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            record.levelname,
            record.name,
        ]
        cid = getattr(record, "correlation_id", None) or get_correlation_id()
        if cid:
            parts.append(f"correlation_id={cid}")

        event = getattr(record, "event", None)
        if event:
            parts.append(f"event={event}")

        job_id = getattr(record, "job_id", None)
        if job_id:
            parts.append(f"job_id={job_id}")

        process_type = getattr(record, "process_type", None)
        if process_type:
            parts.append(f"process_type={process_type}")

        runtime_target = getattr(record, "runtime_target", None)
        if runtime_target:
            parts.append(f"runtime_target={runtime_target}")

        parts.append(str(record.msg))
        return " | ".join(parts)


def log_event(
    event_name: str,
    level: int = logging.INFO,
    extra: Mapping[str, Any] | None = None,
    exc_info: bool = False,
) -> None:
    extra_dict = dict(extra or {})
    extra_dict["event"] = event_name
    logger = logging.getLogger(__name__)
    cid = get_correlation_id()
    if cid:
        extra_dict["correlation_id"] = cid
    logger.log(level, "event=%s", event_name, extra=extra_dict, exc_info=exc_info)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    cid_filter = CorrelationIdFilter()
    for handler in root_logger.handlers:
        handler.addFilter(cid_filter)
