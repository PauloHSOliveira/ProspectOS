"""Health endpoint and health checks for ProspectOS backend."""

import json
import logging
import os
import platform as _platform
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import runtime_targets
from paths import DIR_DADOS, DIR_LOGS, DIR_TEMP, DIR_CACHE, DIR_RECURSOS
from playwright_runtime import PlaywrightRuntimeManager
from playwright_runtime.models import RuntimeState as PwRuntimeState

logger = logging.getLogger(__name__)

VERSION = "2.0.0"


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    STARTING = "starting"
    UNKNOWN = "unknown"


class HttpStatusCode:
    OK = 200
    SERVICE_UNAVAILABLE = 503


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _runtime_target() -> str:
    try:
        return runtime_targets.current_target()
    except Exception:
        plat = {"darwin": "darwin", "win32": "win32", "linux": "linux"}.get(sys.platform, "unknown")
        arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(
            _platform.machine().lower(), "unknown"
        )
        return f"{plat}-{arch}"


# ── Health check models ─────────────────────────────────────────────────


@dataclass
class CheckResult:
    status: HealthStatus = HealthStatus.OK
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    status: HealthStatus
    service: str
    version: str
    runtimeTarget: str
    timestamp: str
    checks: dict[str, CheckResult]
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Individual checks ───────────────────────────────────────────────────


def _check_database() -> CheckResult:
    try:
        import db as db_module
        import sqlite3
        caminho = db_module.CAMINHO_BANCO
        if not caminho.exists():
            return CheckResult(
                status=HealthStatus.UNHEALTHY,
                detail="Banco de dados nao encontrado",
            )
        conn = sqlite3.connect(str(caminho), timeout=3)
        try:
            cur = conn.execute("SELECT 1")
            cur.fetchone()
            cur = conn.execute("PRAGMA journal_mode")
            row = cur.fetchone()
            journal = row[0] if row else "unknown"
            return CheckResult(
                status=HealthStatus.OK,
                detail="Banco acessivel",
                metadata={"journalMode": journal, "reachable": True},
            )
        finally:
            conn.close()
    except Exception as exc:
        return CheckResult(
            status=HealthStatus.UNHEALTHY,
            detail=f"Banco inacessivel: {exc}",
        )


def _check_filesystem() -> CheckResult:
    errors = []
    writable_dirs = {
        "dataDir": DIR_DADOS,
        "logDir": DIR_LOGS,
        "cacheDir": DIR_CACHE,
        "tempDir": DIR_TEMP,
    }
    for name, d in writable_dirs.items():
        if not d.exists():
            errors.append(f"{name}: nao existe")
            continue
        if not d.is_dir():
            errors.append(f"{name}: nao eh diretorio")
            continue
        try:
            probe = d / f".health_probe_{os.getpid()}"
            probe.write_text("probe", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{name}: nao gravavel ({exc})")

    resource_dir = DIR_RECURSOS
    if not resource_dir.exists():
        errors.append("resourceDir: nao existe")
    elif not resource_dir.is_dir():
        errors.append("resourceDir: nao eh diretorio")

    if errors:
        return CheckResult(
            status=HealthStatus.UNHEALTHY if len(errors) > 2 else HealthStatus.DEGRADED,
            detail="; ".join(errors[:5]),
            metadata={"errors": errors[:10]},
        )
    return CheckResult(
        status=HealthStatus.OK,
        detail="Todos os diretorios acessiveis",
        metadata={
            "dataDir": str(DIR_DADOS),
            "logDir": str(DIR_LOGS),
            "cacheDir": str(DIR_CACHE),
            "tempDir": str(DIR_TEMP),
        },
    )


def _check_manifest() -> CheckResult:
    try:
        from runtime_targets import load_runtime_manifest, _default_manifest_path
        manifest_path = _default_manifest_path()
        if not manifest_path.exists():
            return CheckResult(
                status=HealthStatus.DEGRADED,
                detail="Runtime manifest nao encontrado",
            )
        manifest = load_runtime_manifest(manifest_path)
        target = _runtime_target()
        targets = manifest.get("targets", {})
        if target not in targets:
            return CheckResult(
                status=HealthStatus.DEGRADED,
                detail=f"Target {target} nao encontrado no manifest",
                metadata={"availableTargets": list(targets.keys())},
            )
        return CheckResult(
            status=HealthStatus.OK,
            detail="Manifest valido",
            metadata={
                "schemaVersion": manifest.get("schemaVersion"),
                "target": target,
            },
        )
    except Exception as exc:
        return CheckResult(
            status=HealthStatus.DEGRADED,
            detail=f"Manifest invalido: {exc}",
        )


def _check_playwright_runtime() -> CheckResult:
    try:
        manager = PlaywrightRuntimeManager()
        inspection = manager.inspect()
        state = inspection.state
        mapping = {
            PwRuntimeState.READY: HealthStatus.OK,
            PwRuntimeState.NOT_INSTALLED: HealthStatus.DEGRADED,
            PwRuntimeState.DOWNLOADING: HealthStatus.STARTING,
            PwRuntimeState.INCOMPLETE: HealthStatus.DEGRADED,
            PwRuntimeState.CORRUPTED: HealthStatus.DEGRADED,
            PwRuntimeState.UNSUPPORTED: HealthStatus.DEGRADED,
        }
        status = mapping.get(state, HealthStatus.DEGRADED)
        return CheckResult(
            status=status,
            detail=f"Playwright runtime: {state.value}",
            metadata={
                "state": state.value,
                "runtimeId": inspection.runtime_id,
                "target": inspection.target,
            },
        )
    except Exception as exc:
        return CheckResult(
            status=HealthStatus.DEGRADED,
            detail=f"Playwright runtime check falhou: {exc}",
        )


def _check_scraper() -> CheckResult:
    try:
        from scraper_process_supervisor import ScraperProcessSupervisor, ScraperState
        supervisor = _get_scraper_supervisor()
        if supervisor is None:
            return CheckResult(
                status=HealthStatus.OK,
                detail="Scraper ocioso (sem supervisor ativo)",
                metadata={"available": True, "state": "idle"},
            )
        state = supervisor.get_state()
        state_mapping = {
            ScraperState.IDLE: "idle",
            ScraperState.STARTING: "starting",
            ScraperState.RUNNING: "running",
            ScraperState.CANCELLING: "cancelling",
            ScraperState.TIMED_OUT: "timed_out",
            ScraperState.STOPPING: "stopping",
            ScraperState.STOPPED: "stopped",
            ScraperState.COMPLETED: "completed",
            ScraperState.FAILED: "failed",
            ScraperState.KILLED: "killed",
        }
        state_name = state_mapping.get(state, "unknown")
        diag = supervisor.get_diagnostics()
        is_unhealthy = state in (
            ScraperState.FAILED, ScraperState.KILLED, ScraperState.TIMED_OUT
        )
        return CheckResult(
            status=HealthStatus.UNHEALTHY if is_unhealthy else HealthStatus.OK,
            detail=f"Scraper state: {state_name}",
            metadata={
                "state": state_name,
                "running": supervisor.is_running(),
                "returnCode": diag.return_code,
                "terminationReason": diag.termination_reason,
            },
        )
    except Exception as exc:
        return CheckResult(
            status=HealthStatus.OK,
            detail=f"Scraper state check: {exc}",
            metadata={"available": False},
        )


def _check_jobs() -> CheckResult:
    try:
        import jobs as jobs_module
        running = 1 if jobs_module.estado_busca.get("rodando") else 0
        running += 1 if jobs_module.estado_instagram.get("rodando") else 0
        return CheckResult(
            status=HealthStatus.OK,
            detail="Jobs operacionais",
            metadata={
                "running": running,
                "searchRunning": jobs_module.estado_busca.get("rodando", False),
                "instagramRunning": jobs_module.estado_instagram.get("rodando", False),
            },
        )
    except Exception as exc:
        return CheckResult(
            status=HealthStatus.DEGRADED,
            detail=f"Jobs check falhou: {exc}",
            metadata={"running": 0},
        )


def _check_keychain() -> CheckResult:
    try:
        import keyring
        backend = keyring.get_keyring()
        backend_name = backend.__class__.__name__ if hasattr(backend, "__class__") else str(type(backend).__name__)
        return CheckResult(
            status=HealthStatus.OK,
            detail="Keyring disponivel",
            metadata={
                "backend": backend_name,
            },
        )
    except ImportError:
        return CheckResult(
            status=HealthStatus.DEGRADED,
            detail="keyring nao instalado",
            metadata={"backend": "unavailable"},
        )
    except Exception as exc:
        return CheckResult(
            status=HealthStatus.DEGRADED,
            detail=f"Keyring check: {exc}",
            metadata={"backend": "error"},
        )


# ── Supervisor cache for scraper state ─────────────────────────────────


_scraper_supervisor_instance: object | None = None


def _get_scraper_supervisor():
    global _scraper_supervisor_instance
    if _scraper_supervisor_instance is not None:
        return _scraper_supervisor_instance
    try:
        import jobs as jobs_module
        for attr_name in dir(jobs_module):
            attr = getattr(jobs_module, attr_name)
            if isinstance(attr, object) and attr.__class__.__name__ == "ScraperProcessSupervisor":
                _scraper_supervisor_instance = attr
                return _scraper_supervisor_instance
    except Exception:
        pass
    return None


def register_scraper_supervisor(supervisor) -> None:
    global _scraper_supervisor_instance
    _scraper_supervisor_instance = supervisor


# ── Health aggregation ──────────────────────────────────────────────────


ALL_CHECKS = {
    "database": _check_database,
    "filesystem": _check_filesystem,
    "manifest": _check_manifest,
    "playwrightRuntime": _check_playwright_runtime,
    "scraper": _check_scraper,
    "jobs": _check_jobs,
    "keychain": _check_keychain,
}


def collect_health(starting: bool = False) -> HealthReport:
    status = HealthStatus.STARTING if starting else HealthStatus.OK
    checks: dict[str, CheckResult] = {}

    for name, check_fn in ALL_CHECKS.items():
        try:
            result = check_fn()
            checks[name] = result
        except Exception as exc:
            checks[name] = CheckResult(
                status=HealthStatus.UNKNOWN,
                detail=f"Check falhou com erro: {exc}",
            )

    if status != HealthStatus.STARTING:
        for name, result in checks.items():
            if result.status == HealthStatus.UNHEALTHY:
                status = HealthStatus.UNHEALTHY
            elif result.status == HealthStatus.DEGRADED and status not in (
                HealthStatus.UNHEALTHY, HealthStatus.DEGRADED
            ):
                status = HealthStatus.DEGRADED

    return HealthReport(
        status=status,
        service="prospectos-backend",
        version=VERSION,
        runtimeTarget=_runtime_target(),
        timestamp=_now_iso(),
        checks=checks,
        metadata={},
    )


def check_results_to_dict(checks: dict[str, CheckResult]) -> dict[str, Any]:
    result = {}
    for name, cr in checks.items():
        entry: dict[str, Any] = {"status": cr.status.value}
        if cr.detail:
            entry["detail"] = cr.detail
        if cr.metadata:
            entry = {**entry, **cr.metadata}
        result[name] = entry
    return result


def health_to_http_code(report: HealthReport) -> int:
    if report.status in (HealthStatus.OK, HealthStatus.DEGRADED):
        return HttpStatusCode.OK
    return HttpStatusCode.SERVICE_UNAVAILABLE


def health_report_to_dict(report: HealthReport) -> dict[str, Any]:
    checks = report.checks
    if checks and isinstance(next(iter(checks.values())), CheckResult):
        checks_dict = check_results_to_dict(checks)
    else:
        checks_dict = checks
    return {
        "status": report.status.value,
        "service": report.service,
        "version": report.version,
        "runtimeTarget": report.runtimeTarget,
        "timestamp": report.timestamp,
        "checks": checks_dict,
    }
