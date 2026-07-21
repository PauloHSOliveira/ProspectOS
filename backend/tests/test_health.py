"""Tests for health endpoint — state, HTTP codes, schema, side effects."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import health as health_module
from health import (
    HealthStatus, HealthReport, CheckResult,
    _check_database, _check_filesystem, _check_manifest,
    _check_playwright_runtime, _check_scraper, _check_jobs, _check_keychain,
    collect_health, health_to_http_code, health_report_to_dict,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_health_state():
    health_module._scraper_supervisor_instance = None


# ── HealthStatus enum ───────────────────────────────────────────────────


class TestHealthStatus:
    def test_values_are_closed(self):
        expected = {"ok", "degraded", "unhealthy", "starting", "unknown"}
        actual = {s.value for s in HealthStatus}
        assert actual == expected


# ── HTTP status codes ───────────────────────────────────────────────────


class TestHttpStatusCodes:
    def test_ok_returns_200(self):
        report = HealthReport(
            status=HealthStatus.OK, service="test", version="1",
            runtimeTarget="test", timestamp="now", checks={},
        )
        assert health_to_http_code(report) == 200

    def test_degraded_returns_200(self):
        report = HealthReport(
            status=HealthStatus.DEGRADED, service="test", version="1",
            runtimeTarget="test", timestamp="now", checks={},
        )
        assert health_to_http_code(report) == 200

    def test_unhealthy_returns_503(self):
        report = HealthReport(
            status=HealthStatus.UNHEALTHY, service="test", version="1",
            runtimeTarget="test", timestamp="now", checks={},
        )
        assert health_to_http_code(report) == 503

    def test_starting_returns_503(self):
        report = HealthReport(
            status=HealthStatus.STARTING, service="test", version="1",
            runtimeTarget="test", timestamp="now", checks={},
        )
        assert health_to_http_code(report) == 503


# ── HealthReport schema ────────────────────────────────────────────────


class TestHealthReportSchema:
    def test_minimal_report(self):
        report = HealthReport(
            status=HealthStatus.OK, service="prospectos-backend",
            version="2.0.0", runtimeTarget="darwin-arm64",
            timestamp="2026-07-21T00:00:00Z", checks={},
        )
        d = health_report_to_dict(report)
        assert d["status"] == "ok"
        assert d["service"] == "prospectos-backend"
        assert d["version"] == "2.0.0"
        assert d["runtimeTarget"] == "darwin-arm64"
        assert "timestamp" in d
        assert d["checks"] == {}

    def test_report_with_checks(self):
        checks = {
            "database": CheckResult(status=HealthStatus.OK, detail="ok"),
            "scraper": CheckResult(
                status=HealthStatus.OK, detail="idle",
                metadata={"state": "idle", "running": False},
            ),
        }
        report = HealthReport(
            status=HealthStatus.OK, service="prospectos-backend",
            version="2.0.0", runtimeTarget="test", timestamp="now",
            checks=checks,
        )
        d = health_report_to_dict(report)
        assert d["checks"]["database"]["status"] == "ok"
        assert d["checks"]["scraper"]["state"] == "idle"
        assert d["checks"]["scraper"]["running"] is False

    def test_no_secrets_in_report(self):
        report = collect_health()
        d = health_report_to_dict(report)
        d_str = json.dumps(d)
        sensitive = ["api_key", "apikey", "token", "secret", "password",
                     "authorization", "cookie", "session"]
        for s in sensitive:
            assert s not in d_str.lower(), f"Found sensitive key in health: {s}"


# ── individual checks ──────────────────────────────────────────────────


class TestDatabaseCheck:
    def test_check_without_db_returns_unhealthy(self, tmp_path, monkeypatch):
        monkeypatch.setattr("db.CAMINHO_BANCO", tmp_path / "nonexistent.db")
        result = _check_database()
        assert result.status == HealthStatus.UNHEALTHY


class TestFilesystemCheck:
    def test_check_valid_dirs(self, monkeypatch):
        import health as health_module
        tmp = tempfile.mkdtemp()
        monkeypatch.setattr(health_module, "DIR_DADOS", Path(tmp))
        monkeypatch.setattr(health_module, "DIR_LOGS", Path(tmp))
        monkeypatch.setattr(health_module, "DIR_CACHE", Path(tmp))
        monkeypatch.setattr(health_module, "DIR_TEMP", Path(tmp))
        monkeypatch.setattr(health_module, "DIR_RECURSOS", Path(tmp))
        result = _check_filesystem()
        assert result.status == HealthStatus.OK

    def test_check_nonexistent_resource_dir(self, monkeypatch):
        tmp = tempfile.mkdtemp()
        missing = Path(tmp) / "missing"
        monkeypatch.setattr("paths.DIR_DADOS", Path(tmp))
        monkeypatch.setattr("paths.DIR_LOGS", Path(tmp))
        monkeypatch.setattr("paths.DIR_CACHE", Path(tmp))
        monkeypatch.setattr("paths.DIR_TEMP", Path(tmp))
        monkeypatch.setattr("paths.DIR_RECURSOS", missing)
        result = _check_filesystem()
        assert result.status in (HealthStatus.DEGRADED, HealthStatus.UNHEALTHY)


class TestManifestCheck:
    def test_check_missing_manifest(self, monkeypatch):
        def mock_path():
            return Path("/nonexistent/manifest.json")
        monkeypatch.setattr("runtime_targets._default_manifest_path", mock_path)
        result = _check_manifest()
        assert result.status == HealthStatus.DEGRADED


class TestJobsCheck:
    def test_jobs_idle(self):
        result = _check_jobs()
        assert result.status == HealthStatus.OK
        assert "running" in result.metadata

    def test_jobs_running(self, monkeypatch):
        import jobs
        monkeypatch.setitem(jobs.estado_busca, "rodando", True)
        result = _check_jobs()
        assert result.status == HealthStatus.OK
        assert result.metadata["searchRunning"] is True


class TestKeychainCheck:
    def test_keychain_available(self):
        result = _check_keychain()
        assert result.status in (HealthStatus.OK, HealthStatus.DEGRADED)


# ── collect_health ─────────────────────────────────────────────────────


class TestCollectHealth:
    def test_returns_valid_report(self):
        report = collect_health()
        assert isinstance(report, HealthReport)
        assert report.service == "prospectos-backend"
        assert report.version == "2.0.0"
        assert "timestamp" in health_report_to_dict(report)
        assert "database" in report.checks
        assert "filesystem" in report.checks
        assert "manifest" in report.checks
        assert "playwrightRuntime" in report.checks
        assert "scraper" in report.checks
        assert "jobs" in report.checks

    def test_starting_flag(self):
        report = collect_health(starting=True)
        assert report.status == HealthStatus.STARTING

    def test_json_serializable(self):
        report = collect_health()
        d = health_report_to_dict(report)
        json_str = json.dumps(d)
        parsed = json.loads(json_str)
        assert parsed["status"] == d["status"]


# ── Side effects ────────────────────────────────────────────────────────


class TestHealthSideEffects:
    def test_health_does_not_create_db(self, tmp_path, monkeypatch):
        fake_db = tmp_path / "leads_test.db"
        assert not fake_db.exists()
        monkeypatch.setattr("db.CAMINHO_BANCO", fake_db)
        report = collect_health()
        assert not fake_db.exists()

    def test_health_does_not_write_files(self, tmp_path, monkeypatch):
        import health as health_module
        monkeypatch.setattr(health_module, "DIR_DADOS", tmp_path)
        monkeypatch.setattr(health_module, "DIR_LOGS", tmp_path)
        monkeypatch.setattr(health_module, "DIR_CACHE", tmp_path)
        monkeypatch.setattr(health_module, "DIR_TEMP", tmp_path)
        monkeypatch.setattr(health_module, "DIR_RECURSOS", Path(__file__).parent)
        before = {p for p in tmp_path.rglob("*") if not p.name.startswith(".health_probe")}
        collect_health()
        after = {p for p in tmp_path.rglob("*") if not p.name.startswith(".health_probe")}
        new_files = after - before
        non_playwright = [f for f in new_files if "playwright" not in str(f).lower()]
        assert len(non_playwright) == 0

    def test_health_does_not_start_scraper(self):
        report = collect_health()
        scraper = report.checks.get("scraper")
        if isinstance(scraper, CheckResult):
            state = scraper.metadata.get("state", "")
        elif isinstance(scraper, dict):
            state = scraper.get("state", "")
        else:
            state = ""
        assert state != "running"

    def test_health_does_not_install_playwright(self, monkeypatch):
        called = []
        original_inspect = None
        import playwright_runtime.manager as mgr
        original_inspect = mgr.PlaywrightRuntimeManager.inspect
        def fake_inspect(self):
            called.append("inspect")
            from playwright_runtime.models import RuntimeInspection, RuntimeState
            return RuntimeInspection(
                state=RuntimeState.NOT_INSTALLED,
                runtime_id="test",
                target="test",
                root="/tmp",
            )
        monkeypatch.setattr(mgr.PlaywrightRuntimeManager, "inspect", fake_inspect)
        monkeypatch.setattr(mgr.PlaywrightRuntimeManager, "ensure_ready",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("should not be called")))
        collect_health()
        assert "inspect" in called


# ── overall health aggregation ────────────────────────────────────────


class TestHealthAggregation:
    def test_all_ok_results_in_ok(self):
        report = collect_health()
        if all(c.status == HealthStatus.OK for c in report.checks.values()):
            assert report.status == HealthStatus.OK

    def test_any_unhealthy_results_in_unhealthy(self, monkeypatch):
        checks_before = dict(health_module.ALL_CHECKS)
        try:
            health_module.ALL_CHECKS["test_fail"] = lambda: CheckResult(
                status=HealthStatus.UNHEALTHY, detail="forced fail"
            )
            report = collect_health()
            assert report.status == HealthStatus.UNHEALTHY
        finally:
            health_module.ALL_CHECKS.clear()
            health_module.ALL_CHECKS.update(checks_before)

    def test_degraded_without_unhealthy_results_in_degraded(self, monkeypatch):
        checks_before = dict(health_module.ALL_CHECKS)
        try:
            health_module.ALL_CHECKS.clear()
            health_module.ALL_CHECKS["ok_check"] = lambda: CheckResult(status=HealthStatus.OK)
            health_module.ALL_CHECKS["degraded_check"] = lambda: CheckResult(
                status=HealthStatus.DEGRADED, detail="degraded"
            )
            report = collect_health()
            assert report.status == HealthStatus.DEGRADED
        finally:
            health_module.ALL_CHECKS.clear()
            health_module.ALL_CHECKS.update(checks_before)
