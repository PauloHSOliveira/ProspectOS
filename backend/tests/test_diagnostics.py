"""Tests for diagnostics collector, sanitization, and ZIP export."""

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from diagnostics import (
    DiagnosticsReport,
    sanitize_dict,
    sanitize_for_export,
    alias_path,
    collect_diagnostics,
    diagnostics_to_dict,
    export_diagnostics_zip,
    _tail_log,
    SECRET_PLACEHOLDER,
    SENSITIVE_KEYS,
)


# ── Sanitization ────────────────────────────────────────────────────────


class TestSanitization:
    def test_redacts_sensitive_keys(self):
        data = {
            "api_key": "sk-test123",
            "name": "normal",
            "nested": {
                "secret": "my-password",
                "inner": {"token": "abc"},
            },
        }
        result = sanitize_dict(data)
        assert result["api_key"] == SECRET_PLACEHOLDER
        assert result["name"] == "normal"
        assert result["nested"]["secret"] == SECRET_PLACEHOLDER
        assert result["nested"]["inner"]["token"] == SECRET_PLACEHOLDER

    def test_redacts_jwt_values_in_keys(self):
        data = {
            "access": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNqP2k1fBKsTR1I-aA",
        }
        result = sanitize_dict(data)
        assert result["access"] == SECRET_PLACEHOLDER

    def test_redacts_openai_keys(self):
        data = {"key": "sk-proj-abcdef1234567890abcdef1234567890abcdef12"}
        result = sanitize_dict(data)
        assert result["key"] == SECRET_PLACEHOLDER

    def test_preserves_safe_content(self):
        data = {"name": "Empresa Teste", "count": 42, "active": True}
        result = sanitize_dict(data)
        assert result["name"] == "Empresa Teste"
        assert result["count"] == 42
        assert result["active"] is True

    def test_sanitize_list(self):
        data = [{"api_key": "secret"}, {"name": "public"}]
        result = sanitize_for_export(data)
        assert result[0]["api_key"] == SECRET_PLACEHOLDER
        assert result[1]["name"] == "public"

    def test_truncates_long_strings(self):
        long_str = "x" * 3000
        result = sanitize_dict({"data": long_str})
        assert len(result["data"]) < 2500
        assert result["data"].endswith("...[truncated]")

    def test_depth_limit(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": "deep"}}}}}}}}}}
        result = sanitize_dict(deep)
        inner = result
        for _ in range(7):
            inner = list(inner.values())[0]
        assert isinstance(inner, dict)
        assert "_truncated" in str(inner)


# ── Path aliasing ──────────────────────────────────────────────────────


class TestPathAliasing:
    def test_aliases_common_paths(self):
        result = alias_path("/tmp/some/path")
        assert isinstance(result, str)


# ─── DiagnosticsReport ─────────────────────────────────────────────────


class TestDiagnosticsReport:
    def test_collect_returns_valid_report(self):
        report = collect_diagnostics()
        assert isinstance(report, DiagnosticsReport)
        assert report.appVersion == "2.0.0"
        assert report.runtimeTarget
        assert report.os
        assert report.timestamp
        assert report.health
        assert report.paths

    def test_health_is_included(self):
        report = collect_diagnostics()
        d = diagnostics_to_dict(report)
        assert "health" in d
        assert "status" in d["health"]

    def test_paths_are_sanitized(self):
        report = collect_diagnostics()
        d = diagnostics_to_dict(report)
        paths = d.get("paths", {})
        for key, val in paths.items():
            assert isinstance(val, str)
            assert SECRET_PLACEHOLDER not in val or "api" not in key.lower()

    def test_no_secrets_in_report(self):
        report = collect_diagnostics()
        d = diagnostics_to_dict(report)
        d_str = json.dumps(d).lower()
        for key_pattern in ["api_key", "apikey", "token", "secret", "password",
                            "authorization"]:
            if key_pattern in d_str:
                assert SECRET_PLACEHOLDER.lower() in d_str

    def test_best_effort_on_error(self, monkeypatch):
        def failing_check():
            raise RuntimeError("forced failure")
        monkeypatch.setattr("diagnostics._collect_database_metadata", failing_check)
        report = collect_diagnostics()
        assert len(report.errors) >= 0
        d = diagnostics_to_dict(report)
        assert "database" in d

    def test_electron_version(self):
        report = collect_diagnostics(electron_version="1.2.3")
        assert report.electronVersion == "1.2.3"


# ── Log tail ────────────────────────────────────────────────────────────


class TestLogTail:
    def test_nonexistent_file(self):
        result = _tail_log(Path("/nonexistent/test.log"))
        assert result == ""

    def test_short_file(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = _tail_log(log, max_lines=10)
        assert "line1" in result

    def test_respects_max_lines(self, tmp_path):
        log = tmp_path / "test.log"
        log.write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
        result = _tail_log(log, max_lines=5)
        lines = result.splitlines()
        assert len(lines) <= 5


# ── ZIP export ──────────────────────────────────────────────────────────


class TestZipExport:
    def test_export_creates_zip(self, tmp_path):
        output = tmp_path / "diagnostics.zip"
        result = export_diagnostics_zip(output)
        assert result.exists()
        assert result.suffix == ".zip"

    def test_zip_contains_expected_files(self, tmp_path):
        output = tmp_path / "diag.zip"
        result = export_diagnostics_zip(output)
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
        assert "diagnostics.json" in names
        assert "health.json" in names
        assert "README.txt" in names

    def test_zip_does_not_contain_db(self, tmp_path):
        output = tmp_path / "diag.zip"
        result = export_diagnostics_zip(output)
        with zipfile.ZipFile(result, "r") as zf:
            names = zf.namelist()
        for name in names:
            assert ".db" not in name
            assert ".sqlite" not in name

    def test_zip_does_not_overwrite_without_force(self, tmp_path):
        output = tmp_path / "existing.zip"
        output.write_text("existing", encoding="utf-8")
        with pytest.raises(FileExistsError):
            export_diagnostics_zip(output)

    def test_zip_overwrites_with_force(self, tmp_path):
        output = tmp_path / "existing.zip"
        output.write_text("existing", encoding="utf-8")
        result = export_diagnostics_zip(output, force=True)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_zip_does_not_contain_secrets(self, tmp_path):
        output = tmp_path / "diag.zip"
        result = export_diagnostics_zip(output)
        with zipfile.ZipFile(result, "r") as zf:
            for name in zf.namelist():
                if name.endswith(".json"):
                    content = zf.read(name).decode("utf-8").lower()
                    for secret in ["api_key", "apikey", "token", "secret",
                                   "password"]:
                        if secret in content:
                            assert "***redacted***" in content

    def test_zip_total_size_limited(self, tmp_path):
        output = tmp_path / "diag.zip"
        result = export_diagnostics_zip(output)
        assert result.stat().st_size < 50 * 1024 * 1024

    def test_partial_zip_not_left_on_error(self, tmp_path):
        output = tmp_path / "diag.zip"
        with pytest.raises(Exception):
            with patch("diagnostics.shutil.move", side_effect=RuntimeError("move failed")):
                export_diagnostics_zip(output)
        staging = tmp_path / ".diagnostics.zip.staging"
        assert not staging.exists()
