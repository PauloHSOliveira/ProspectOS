"""Health endpoint blueprint for ProspectOS backend.

GET /api/health
  Returns structured health state for all subsystems.
  No side effects — pure inspection.
"""

import logging

from flask import Blueprint, jsonify

import health as health_module
import logging_config

logger = logging.getLogger(__name__)

bp = Blueprint("health", __name__, url_prefix="/api")


@bp.route("/health", methods=["GET"])
def health_check():
    correlation_id = logging_config.set_correlation_id()
    try:
        report = health_module.collect_health()
        http_code = health_module.health_to_http_code(report)
        logging_config.log_event(
            "health.requested",
            extra={
                "status": report.status.value,
                "correlation_id": correlation_id,
            },
        )
        return jsonify(health_module.health_report_to_dict(report)), http_code
    except Exception as exc:
        logging_config.log_event(
            "health.requested",
            level=logging.ERROR,
            extra={
                "error": str(exc),
                "correlation_id": correlation_id,
            },
        )
        return jsonify({
            "status": "unhealthy",
            "service": "prospectos-backend",
            "version": health_module.VERSION,
            "runtimeTarget": health_module._runtime_target(),
            "timestamp": health_module._now_iso(),
            "checks": {},
            "error": "Health check failed internally",
        }), 503
