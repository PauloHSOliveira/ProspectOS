"""Structured diagnostics collector, sanitization, and ZIP export.

Best-effort: a failing subsystem does not block the complete report.
"""

import io
import json
import logging
import os
import platform as _platform
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import health
import runtime_targets

logger = logging.getLogger(__name__)

# ── Limits ──────────────────────────────────────────────────────────────

MAX_LOG_BYTES_PER_FILE = 5 * 1024 * 1024
MAX_LOG_LINES_PER_FILE = 10_000
MAX_LOG_AGE_DAYS = 7
MAX_ZIP_TOTAL_BYTES = 20 * 1024 * 1024
MAX_DIAGNOSTIC_LINE_LENGTH = 2000

# ── Sanitization patterns ───────────────────────────────────────────────

SENSITIVE_KEYS = re.compile(
    r"(token|secret|password|passwd|api_key|apikey|authorization|"
    r"cookie|session|proxy|credential)",
    re.IGNORECASE,
)

SENSITIVE_VALUES = re.compile(
    r"(eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+|"  # JWT
    r"sk-[a-zA-Z0-9_-]{20,}|"  # OpenAI key
    r"AIza[a-zA-Z0-9_-]{35,}|"  # Google API key
    r"ghp_[a-zA-Z0-9]{36,}|"  # GitHub PAT
    r"xox[bpras]-[a-zA-Z0-9-]{10,})",  # Slack tokens
)

SECRET_PLACEHOLDER = "***REDACTED***"

# Path aliases for shared reports
PATH_ALIASES: list[tuple[Path, str]] = []


def _init_path_aliases():
    if PATH_ALIASES:
        return
    try:
        from paths import DIR_DADOS, DIR_LOGS, DIR_TEMP, DIR_CACHE, DIR_RECURSOS
        for p, alias in (
            (DIR_DADOS, "<DATA_DIR>"),
            (DIR_LOGS, "<LOG_DIR>"),
            (DIR_TEMP, "<TEMP_DIR>"),
            (DIR_CACHE, "<CACHE_DIR>"),
            (DIR_RECURSOS, "<RESOURCE_DIR>"),
        ):
            if p:
                resolved = p.resolve()
                PATH_ALIASES.append((resolved, alias))
    except Exception:
        pass


def alias_path(path_str: str) -> str:
    _init_path_aliases()
    for resolved, alias in PATH_ALIASES:
        resolved_str = str(resolved)
        if path_str.startswith(resolved_str):
            return path_str.replace(resolved_str, alias, 1)
    return path_str


def sanitize_dict(d: dict, depth: int = 0) -> dict:
    if depth > 8:
        return {"_truncated": True}
    result = {}
    for key, value in d.items():
        if SENSITIVE_KEYS.search(key):
            result[key] = SECRET_PLACEHOLDER
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value, depth + 1)
        elif isinstance(value, str) and SENSITIVE_VALUES.search(value):
            result[key] = SECRET_PLACEHOLDER
        elif isinstance(value, str) and len(value) > MAX_DIAGNOSTIC_LINE_LENGTH:
            result[key] = value[:MAX_DIAGNOSTIC_LINE_LENGTH] + "...[truncated]"
        else:
            result[key] = value
    return result


def sanitize_for_export(data: Any) -> Any:
    if isinstance(data, dict):
        return sanitize_dict(data)
    if isinstance(data, list):
        return [sanitize_for_export(item) for item in data]
    return data


# ── Data models ─────────────────────────────────────────────────────────


@dataclass
class DiagnosticsReport:
    appVersion: str = "2.0.0"
    runtimeTarget: str = ""
    os: str = ""
    architecture: str = ""
    pythonVersion: str = ""
    pythonRuntime: str = ""
    isBundled: bool = False
    electronVersion: str = ""
    timestamp: str = ""
    paths: dict[str, str] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    database: dict[str, Any] = field(default_factory=dict)
    diskSpace: dict[str, Any] = field(default_factory=dict)
    playwrightRuntime: dict[str, Any] = field(default_factory=dict)
    manifestVersions: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ── Collectors ──────────────────────────────────────────────────────────


def _collect_disk_space() -> dict[str, Any]:
    try:
        from paths import DIR_DADOS, DIR_CACHE, DIR_LOGS
        result = {}
        for name, d in (("data", DIR_DADOS), ("cache", DIR_CACHE), ("logs", DIR_LOGS)):
            try:
                usage = shutil.disk_usage(d)
                result[name] = {
                    "free_gb": round(usage.free / (1024**3), 2),
                    "total_gb": round(usage.total / (1024**3), 2),
                    "used_gb": round(usage.used / (1024**3), 2),
                }
            except OSError:
                result[name] = {"free_gb": 0, "total_gb": 0, "used_gb": 0}
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _collect_log_metadata() -> dict[str, Any]:
    try:
        from paths import DIR_LOGS
        result = {}
        if not DIR_LOGS.exists():
            return {"logDir": str(DIR_LOGS), "files": []}
        files = []
        total_bytes = 0
        for f in sorted(DIR_LOGS.glob("*.log*"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
                total_bytes += stat.st_size
            except OSError:
                pass
        result["logDir"] = str(DIR_LOGS)
        result["files"] = files[:20]
        result["totalBytes"] = total_bytes
        return result
    except Exception as exc:
        return {"error": str(exc)}


def _collect_manifest_versions() -> dict[str, Any]:
    try:
        manifest_path = runtime_targets._default_manifest_path()
        if manifest_path.exists():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return {
                "schemaVersion": data.get("schemaVersion"),
                "targets": list(data.get("targets", {}).keys()),
            }
    except Exception:
        pass
    return {"error": "manifest not available"}


def _collect_playwright_diagnostics() -> dict[str, Any]:
    try:
        manager = __import__("playwright_runtime", fromlist=["PlaywrightRuntimeManager"])
        mgr = manager.PlaywrightRuntimeManager()
        diag = mgr.get_diagnostics()
        return asdict(diag)
    except Exception as exc:
        return {"error": str(exc)}


def _collect_database_metadata() -> dict[str, Any]:
    try:
        import sqlite3
        import db as db_module
        caminho = db_module.CAMINHO_BANCO
        if not caminho.exists():
            return {"exists": False}
        stat = caminho.stat()
        conn = sqlite3.connect(str(caminho), timeout=3)
        try:
            cur = conn.execute("PRAGMA page_count")
            page_count = cur.fetchone()[0]
            cur = conn.execute("PRAGMA page_size")
            page_size = cur.fetchone()[0]
            cur = conn.execute("PRAGMA journal_mode")
            journal = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM leads")
            lead_count = cur.fetchone()[0]
            return {
                "exists": True,
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "journalMode": journal,
                "pageCount": page_count,
                "pageSize": page_size,
                "leadCount": lead_count,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            }
        finally:
            conn.close()
    except Exception as exc:
        return {"error": str(exc)}


# ── Main collector ──────────────────────────────────────────────────────


def collect_diagnostics(electron_version: str = "") -> DiagnosticsReport:
    errors: list[str] = []

    try:
        health_report = health.collect_health()
        health_dict = health.health_report_to_dict(health_report)
    except Exception as exc:
        health_dict = {"error": str(exc)}
        errors.append(f"health: {exc}")

    try:
        disk = _collect_disk_space()
    except Exception as exc:
        disk = {"error": str(exc)}
        errors.append(f"disk: {exc}")

    try:
        log_meta = _collect_log_metadata()
    except Exception as exc:
        log_meta = {"error": str(exc)}
        errors.append(f"logs: {exc}")

    try:
        manifest_versions = _collect_manifest_versions()
    except Exception as exc:
        manifest_versions = {"error": str(exc)}
        errors.append(f"manifest: {exc}")

    try:
        playwright = _collect_playwright_diagnostics()
    except Exception as exc:
        playwright = {"error": str(exc)}
        errors.append(f"playwright: {exc}")

    try:
        db_meta = _collect_database_metadata()
    except Exception as exc:
        db_meta = {"error": str(exc)}
        errors.append(f"database: {exc}")

    try:
        from paths import DIR_DADOS, DIR_LOGS, DIR_TEMP, DIR_CACHE, DIR_RECURSOS
        paths_dict = {
            "dataDir": alias_path(str(DIR_DADOS)),
            "logDir": alias_path(str(DIR_LOGS)),
            "tempDir": alias_path(str(DIR_TEMP)),
            "cacheDir": alias_path(str(DIR_CACHE)),
            "resourceDir": alias_path(str(DIR_RECURSOS)),
        }
    except Exception as exc:
        paths_dict = {}
        errors.append(f"paths: {exc}")

    report = DiagnosticsReport(
        runtimeTarget=health._runtime_target(),
        os=sys.platform,
        architecture=_platform.machine(),
        pythonVersion=sys.version,
        pythonRuntime="bundled" if getattr(sys, "frozen", False) else "system",
        isBundled=bool(getattr(sys, "frozen", False)),
        electronVersion=electron_version,
        timestamp=health._now_iso(),
        paths=sanitize_for_export(paths_dict),
        health=sanitize_for_export(health_dict),
        database=sanitize_for_export(db_meta),
        diskSpace=disk,
        playwrightRuntime=sanitize_for_export(playwright),
        manifestVersions=manifest_versions,
        errors=errors,
    )
    return report


def diagnostics_to_dict(report: DiagnosticsReport) -> dict[str, Any]:
    return sanitize_for_export(asdict(report))


# ── Log tail reader ────────────────────────────────────────────────────


def _tail_log(log_path: Path, max_lines: int = MAX_LOG_LINES_PER_FILE,
              max_bytes: int = MAX_LOG_BYTES_PER_FILE) -> str:
    if not log_path.exists():
        return ""
    try:
        total_size = log_path.stat().st_size
        read_size = min(total_size, max_bytes)
        if read_size < total_size:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(total_size - read_size)
                f.readline()
                rest = f.read(MAX_LOG_BYTES_PER_FILE)
        else:
            rest = log_path.read_text(encoding="utf-8", errors="replace")
        lines = rest.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "\n".join(lines)
    except OSError:
        return ""


# ── ZIP export ──────────────────────────────────────────────────────────


def export_diagnostics_zip(
    output_path: Path | str,
    electron_version: str = "",
    force: bool = False,
) -> Path:
    output = Path(output_path)
    if output.exists() and not force:
        raise FileExistsError(
            f"Arquivo ja existe: {output}. Use force=True para sobrescrever."
        )

    staging_dir = output.parent / f".{output.name}.staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        report = collect_diagnostics(electron_version)
        report_dict = diagnostics_to_dict(report)

        (staging_dir / "diagnostics.json").write_text(
            json.dumps(report_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        health_report = health.collect_health()
        (staging_dir / "health.json").write_text(
            json.dumps(health.health_report_to_dict(health_report), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        try:
            manifest_path = runtime_targets._default_manifest_path()
            if manifest_path.exists():
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                summary = {
                    "schemaVersion": manifest_data.get("schemaVersion"),
                    "targets": list(manifest_data.get("targets", {}).keys()),
                }
                (staging_dir / "manifest-summary.json").write_text(
                    json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        except Exception as exc:
            (staging_dir / "manifest-summary.json").write_text(
                json.dumps({"error": str(exc)}), encoding="utf-8"
            )

        try:
            from paths import DIR_LOGS
            logs_dir = staging_dir / "logs"
            logs_dir.mkdir(exist_ok=True)
            if DIR_LOGS.exists():
                log_files = sorted(DIR_LOGS.glob("*.log*"), key=lambda p: p.stat().st_mtime, reverse=True)
                for lf in log_files[:5]:
                    content = _tail_log(lf)
                    if content:
                        (logs_dir / lf.name).write_text(content, encoding="utf-8")
        except Exception as exc:
            (logs_dir / "error.txt").write_text(f"Log collection failed: {exc}", encoding="utf-8")

        readme = """ProspectOS Diagnostics Export
===============================
Generated: {timestamp}

Contents:
  diagnostics.json     - Full structured diagnostics report (sanitized)
  health.json          - Current health check state
  manifest-summary.json - Runtime targets manifest summary
  logs/                - Recent log files (truncated)

Excluded from this export:
  - SQLite database and WAL/SHM files
  - Playwright runtime binaries
  - Go scraper binary
  - Node.js runtime
  - CSV files
  - PDF files
  - Instagram sessions
  - API keys, tokens, passwords
  - Credential Manager / Keychain secrets
""".format(timestamp=health._now_iso())

        (staging_dir / "README.txt").write_text(readme, encoding="utf-8")

        # Create ZIP from staging
        zip_path = staging_dir / "bundle.zip"
        total_written = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in staging_dir.iterdir():
                if item.name == "bundle.zip":
                    continue
                if item.is_file():
                    data = item.read_bytes()
                    if total_written + len(data) > MAX_ZIP_TOTAL_BYTES:
                        break
                    zf.writestr(item.name, data)
                    total_written += len(data)

        if output.exists():
            output.unlink()
        shutil.move(str(zip_path), str(output))

        return output.resolve()
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
