"""Tests for correlation ID propagation and validation."""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_config import (
    get_correlation_id,
    set_correlation_id,
    clear_correlation_id,
    _generate_correlation_id,
    _validate_correlation_id,
    CorrelationIdFilter,
)


class TestCorrelationId:
    def test_generates_valid_id(self):
        cid = _generate_correlation_id()
        assert len(cid) == 16
        assert cid.isalnum()

    def test_set_and_get(self):
        clear_correlation_id()
        cid = set_correlation_id()
        assert get_correlation_id() == cid

    def test_custom_id(self):
        clear_correlation_id()
        result = set_correlation_id("my-custom-id-123")
        assert result == "my-custom-id-123"
        assert get_correlation_id() == "my-custom-id-123"

    def test_clear(self):
        set_correlation_id("test-id")
        clear_correlation_id()
        assert get_correlation_id() == ""

    def test_thread_isolation(self):
        clear_correlation_id()
        set_correlation_id("main-thread")
        results = {}

        def worker():
            set_correlation_id("worker-thread")
            results["worker"] = get_correlation_id()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert get_correlation_id() == "main-thread"
        assert results["worker"] == "worker-thread"


class TestValidateCorrelationId:
    def test_accepts_valid_id(self):
        assert _validate_correlation_id("abc-123_def") == "abc-123_def"

    def test_rejects_empty(self):
        result = _validate_correlation_id("")
        assert len(result) == 16
        assert result.isalnum()

    def test_rejects_too_short(self):
        result = _validate_correlation_id("ab")
        assert len(result) == 16

    def test_rejects_too_long(self):
        result = _validate_correlation_id("a" * 65)
        assert len(result) == 16

    def test_rejects_special_chars(self):
        result = _validate_correlation_id("hello world!")
        assert len(result) == 16
        assert result.isalnum()

    def test_none_replaced(self):
        result = _validate_correlation_id(None)
        assert len(result) == 16
        assert result.isalnum()


class TestCorrelationIdFilter:
    def test_filter_adds_correlation_id(self):
        import logging
        set_correlation_id("test-cid")
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        f = CorrelationIdFilter()
        assert f.filter(record)
        assert record.correlation_id == "test-cid"

    def test_filter_uses_dash_when_empty(self):
        import logging
        clear_correlation_id()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        f = CorrelationIdFilter()
        assert f.filter(record)
        assert record.correlation_id == "-"


class TestNoSensitiveDataInIds:
    def test_id_contains_no_pii(self):
        for _ in range(100):
            cid = _generate_correlation_id()
            assert cid.isalnum()
            assert len(cid) == 16
            assert "@" not in cid
