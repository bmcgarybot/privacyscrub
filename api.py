"""
PrivacyScrub — REST API Blueprint

Flask blueprint providing programmatic access to PrivacyScrub functionality.
All endpoints return JSON. Authentication is via optional API key header.

Endpoints:
    GET  /api/score          — Privacy DNA Score for a profile
    GET  /api/brokers         — List all brokers (with filters)
    POST /api/scan            — Trigger a new scan
    GET  /api/status          — Scan status
    GET  /api/status/<batch>  — Status of a specific scan batch
    GET  /api/report          — Generate JSON report export
    GET  /api/profiles        — List all profiles
    GET  /api/breaches        — List breaches for a profile
    GET  /api/optouts         — List opt-outs for a profile
"""

import json
import logging
from functools import wraps
from flask import Blueprint, request, jsonify, Response

from models import (
    get_profile, get_all_profiles, get_setting,
    get_scan_results, get_latest_scan_results,
    get_breaches, get_optouts, get_all_settings,
    log_activity,
)
from email_sender import (
    send_batch, get_email_summary, get_email_requests,
    update_email_request_status, get_available_templates,
    get_email_config, save_email_config, test_smtp_connection,
    render_template as render_email_template,
    get_pending_followups,
)
from scanner import (
    start_scan, get_scan_status, load_brokers,
    get_broker_count, get_broker_categories, scan_state,
)
from breach import scan_profile_breaches, get_breach_summary
from utils import calculate_privacy_score, export_full_report_json
from optout import OptOutManager

logger = logging.getLogger("privacyscrub.api")

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

api_bp = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Authentication middleware
# ---------------------------------------------------------------------------

def require_api_key(f):
    """
    Optional API key authentication decorator.

    If an API key is configured in settings (api_key), requests must
    include it in the X-API-Key header. If no key is configured,
    all requests are allowed (open access for self-hosted use).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        configured_key = get_setting("api_key", "")
        if configured_key:
            provided_key = request.headers.get("X-API-Key", "")
            if provided_key != configured_key:
                return jsonify({
                    "error": "Unauthorized",
                    "message": "Invalid or missing API key. Include X-API-Key header.",
                }), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Error handler helper
# ---------------------------------------------------------------------------

def _error(message: str, status_code: int = 400) -> tuple:
    """Return a standardised JSON error response."""
    return jsonify({"error": message}), status_code


def _success(data: dict, status_code: int = 200) -> tuple:
    """Return a standardised JSON success response."""
    return jsonify({"status": "ok", **data}), status_code


# ---------------------------------------------------------------------------
# GET /api/score — Privacy DNA Score
# ---------------------------------------------------------------------------

@api_bp.route("/score", methods=["GET"])
@require_api_key
def api_get_score():
    """
    Get the Privacy DNA Score for a profile.

    Query params:
        profile_id (int, required): Profile to score.

    Returns:
        200: { "status": "ok", "score": {...} }
        400: { "error": "..." }
    """
    profile_id = request.args.get("profile_id", type=int)
    if not profile_id:
        return _error("profile_id query parameter is required")

    profile = get_profile(profile_id)
    if not profile:
        return _error(f"Profile {profile_id} not found", 404)

    scan_results = get_latest_scan_results(profile_id)
    breaches = get_breaches(profile_id)
    broker_count = get_broker_count()

    score = calculate_privacy_score(scan_results, breaches, broker_count)

    return _success({"score": score})


# ---------------------------------------------------------------------------
# GET /api/brokers — List brokers
# ---------------------------------------------------------------------------

@api_bp.route("/brokers", methods=["GET"])
@require_api_key
def api_get_brokers():
    """
    List all brokers in the database.

    Query params:
        category (str, optional): Filter by category slug.
        tier (int, optional): Filter by tier (1, 2, 3).
        search (str, optional): Search broker names.
        limit (int, optional): Max results (default 50, max 500).
        offset (int, optional): Pagination offset.

    Returns:
        200: { "status": "ok", "brokers": [...], "total": int, "categories": [...] }
    """
    category = request.args.get("category")
    tier = request.args.get("tier", type=int)
    search = request.args.get("search", "").strip().lower()
    limit = min(request.args.get("limit", 50, type=int), 500)
    offset = request.args.get("offset", 0, type=int)

    brokers = load_brokers()

    # Apply filters
    if category:
        brokers = [b for b in brokers if b.get("category") == category]
    if tier:
        brokers = [b for b in brokers if b.get("tier") == tier]
    if search:
        brokers = [b for b in brokers if search in b.get("name", "").lower()
                    or search in b.get("id", "").lower()]

    total = len(brokers)
    brokers = brokers[offset:offset + limit]

    return _success({
        "brokers": brokers,
        "total": total,
        "limit": limit,
        "offset": offset,
        "categories": get_broker_categories(),
    })


# ---------------------------------------------------------------------------
# POST /api/scan — Start a scan
# ---------------------------------------------------------------------------

@api_bp.route("/scan", methods=["POST"])
@require_api_key
def api_start_scan():
    """
    Start a new data broker scan for a profile.

    JSON body:
        {
            "profile_id": int (required),
            "categories": ["people_search", ...] (optional),
            "broker_ids": ["whitepages", ...] (optional)
        }

    Returns:
        202: { "status": "ok", "batch_id": "scan-abc123", "message": "..." }
        400: { "error": "..." }
    """
    data = request.get_json(silent=True) or {}
    profile_id = data.get("profile_id")

    if not profile_id:
        return _error("profile_id is required in request body")

    profile = get_profile(profile_id)
    if not profile:
        return _error(f"Profile {profile_id} not found", 404)

    # Check for already running scan
    active = scan_state.get_latest(profile_id)
    if active and active.get("status") == "running":
        return jsonify({
            "status": "already_running",
            "batch_id": active["batch_id"],
            "message": "A scan is already in progress for this profile",
            "progress": active,
        }), 409

    try:
        batch_id = start_scan(
            profile_id=profile_id,
            categories=data.get("categories"),
            broker_ids=data.get("broker_ids"),
        )

        return jsonify({
            "status": "ok",
            "batch_id": batch_id,
            "message": "Scan started. Poll /api/status for progress.",
        }), 202

    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        logger.exception("API scan error: %s", e)
        return _error(f"Internal error: {e}", 500)


# ---------------------------------------------------------------------------
# GET /api/status — Scan status
# ---------------------------------------------------------------------------

@api_bp.route("/status", methods=["GET"])
@api_bp.route("/status/<batch_id>", methods=["GET"])
@require_api_key
def api_get_status(batch_id: str | None = None):
    """
    Get scan status.

    If batch_id is provided, returns that specific scan's status.
    Otherwise, returns the latest scan for the given profile.

    Query params:
        profile_id (int): Required if batch_id not in URL.

    Returns:
        200: { "status": "ok", "scan": {...} }
        404: { "error": "No scan found" }
    """
    if batch_id:
        status = get_scan_status(batch_id)
        if not status:
            return _error(f"Scan batch '{batch_id}' not found", 404)
        return _success({"scan": status})

    profile_id = request.args.get("profile_id", type=int)
    if not profile_id:
        return _error("profile_id query parameter required (or provide batch_id in URL)")

    status = scan_state.get_latest(profile_id)
    if not status:
        return _error("No scans found for this profile", 404)

    return _success({"scan": status})


# ---------------------------------------------------------------------------
# GET /api/report — Full JSON report export
# ---------------------------------------------------------------------------

@api_bp.route("/report", methods=["GET"])
@require_api_key
def api_get_report():
    """
    Generate a full privacy audit report in JSON format.

    Query params:
        profile_id (int, required): Profile to report on.

    Returns:
        200: Full JSON report (Content-Type: application/json)
        400/404: Error
    """
    profile_id = request.args.get("profile_id", type=int)
    if not profile_id:
        return _error("profile_id query parameter is required")

    profile = get_profile(profile_id)
    if not profile:
        return _error(f"Profile {profile_id} not found", 404)

    scan_results = get_latest_scan_results(profile_id)
    breaches = get_breaches(profile_id)
    optouts = get_optouts(profile_id)
    broker_count = get_broker_count()
    score_data = calculate_privacy_score(scan_results, breaches, broker_count)

    report_json = export_full_report_json(profile, score_data, scan_results, breaches, optouts)

    log_activity(
        None, profile_id, "api_report_generated", "export",
        f"JSON report generated via API",
    )

    return Response(
        report_json,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=privacyscrub-report-{profile_id}.json"},
    )


# ---------------------------------------------------------------------------
# GET /api/profiles — List profiles
# ---------------------------------------------------------------------------

@api_bp.route("/profiles", methods=["GET"])
@require_api_key
def api_list_profiles():
    """
    List all profiles.

    Returns:
        200: { "status": "ok", "profiles": [...] }
    """
    profiles = get_all_profiles()
    return _success({"profiles": profiles, "count": len(profiles)})


# ---------------------------------------------------------------------------
# GET /api/breaches — Breach data
# ---------------------------------------------------------------------------

@api_bp.route("/breaches", methods=["GET"])
@require_api_key
def api_get_breaches():
    """
    Get breach data for a profile.

    Query params:
        profile_id (int, required): Profile to check.

    Returns:
        200: { "status": "ok", "breaches": [...], "summary": {...} }
    """
    profile_id = request.args.get("profile_id", type=int)
    if not profile_id:
        return _error("profile_id query parameter is required")

    breaches = get_breaches(profile_id)
    summary = get_breach_summary(profile_id)

    return _success({
        "breaches": breaches,
        "summary": summary,
    })


# ---------------------------------------------------------------------------
# GET /api/optouts — Opt-out status
# ---------------------------------------------------------------------------

@api_bp.route("/optouts", methods=["GET"])
@require_api_key
def api_get_optouts():
    """
    Get opt-out records for a profile.

    Query params:
        profile_id (int, required): Profile ID.
        status (str, optional): Filter by status.

    Returns:
        200: { "status": "ok", "optouts": [...], "summary": {...} }
    """
    profile_id = request.args.get("profile_id", type=int)
    if not profile_id:
        return _error("profile_id query parameter is required")

    status_filter = request.args.get("status")
    optouts = get_optouts(profile_id, status=status_filter)

    manager = OptOutManager()
    summary = manager.get_summary(profile_id)

    return _success({
        "optouts": optouts,
        "summary": summary,
    })


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@api_bp.route("/health", methods=["GET"])
def api_health():
    """Simple health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "PrivacyScrub",
        "version": "1.0.0",
        "broker_count": get_broker_count(),
    })


# ---------------------------------------------------------------------------
# POST /api/email/send — Send removal emails
# ---------------------------------------------------------------------------

@api_bp.route("/email/send", methods=["POST"])
@require_api_key
def api_email_send():
    """
    Send removal request emails to data brokers.

    JSON body:
        {
            "profile_id": int (required),
            "template": "gdpr" | "ccpa" | "cpra" | "generic_us" | "arizona",
            "broker_ids": [...] (optional, defaults to all),
            "dry_run": bool (optional, defaults to false),
            "include_family": bool (optional, defaults to false),
            "priority": "crucial" | "high" | "medium" | "low" (optional filter)
        }

    Returns:
        200: { "status": "ok", "sent": int, "failed": int, ... }
    """
    data = request.get_json(silent=True) or {}
    profile_id = data.get("profile_id")

    if not profile_id:
        return _error("profile_id is required")

    profile = get_profile(profile_id)
    if not profile:
        return _error(f"Profile {profile_id} not found", 404)

    template_key = data.get("template", "generic_us")
    broker_ids = data.get("broker_ids", [])
    dry_run = data.get("dry_run", False)
    include_family = data.get("include_family", False)
    priority_filter = data.get("priority")

    result = send_batch(
        profile_id=profile_id,
        broker_ids=broker_ids,
        template_key=template_key,
        dry_run=dry_run,
        include_family=include_family,
        priority_filter=priority_filter,
    )

    if "error" in result:
        return _error(result["error"])

    return _success(result)


# ---------------------------------------------------------------------------
# GET /api/email/status — Email sending status
# ---------------------------------------------------------------------------

@api_bp.route("/email/status", methods=["GET"])
@require_api_key
def api_email_status():
    """
    Get email sending status and summary.

    Query params:
        profile_id (int, optional): Filter by profile.
        status (str, optional): Filter by status.
        limit (int, optional): Max results.
        offset (int, optional): Pagination offset.

    Returns:
        200: { "status": "ok", "summary": {...}, "requests": [...] }
    """
    profile_id = request.args.get("profile_id", type=int)
    status_filter = request.args.get("status")
    limit = min(request.args.get("limit", 50, type=int), 500)
    offset = request.args.get("offset", 0, type=int)

    summary = get_email_summary(profile_id)
    requests = get_email_requests(
        profile_id=profile_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )

    return _success({
        "summary": summary,
        "requests": requests,
        "limit": limit,
        "offset": offset,
    })


# ---------------------------------------------------------------------------
# PUT /api/email/request/<id> — Update email request status
# ---------------------------------------------------------------------------

@api_bp.route("/email/request/<int:request_id>", methods=["PUT"])
@require_api_key
def api_email_update(request_id: int):
    """
    Update the status of an email request.

    JSON body:
        {
            "status": "delivered" | "replied" | "action_needed" | "completed" | "failed",
            "response_text": "..." (optional)
        }

    Returns:
        200: { "status": "ok", "updated": true }
    """
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    response_text = data.get("response_text", "")

    if not new_status:
        return _error("status is required")

    valid_statuses = ["pending", "sent", "delivered", "replied", "action_needed", "completed", "failed"]
    if new_status not in valid_statuses:
        return _error(f"Invalid status. Must be one of: {', '.join(valid_statuses)}")

    success = update_email_request_status(request_id, new_status, response_text)
    if not success:
        return _error(f"Email request {request_id} not found", 404)

    return _success({"updated": True})


# ---------------------------------------------------------------------------
# GET /api/email/templates — List available templates
# ---------------------------------------------------------------------------

@api_bp.route("/email/templates", methods=["GET"])
@require_api_key
def api_email_templates():
    """List available email templates."""
    templates = get_available_templates()
    return _success({"templates": templates})


# ---------------------------------------------------------------------------
# POST /api/email/preview — Preview a rendered email
# ---------------------------------------------------------------------------

@api_bp.route("/email/preview", methods=["POST"])
@require_api_key
def api_email_preview():
    """
    Preview a rendered email template.

    JSON body:
        {
            "profile_id": int (required),
            "template": "gdpr" | "ccpa" | "cpra" | "generic_us" | "arizona",
            "broker_id": "..." (optional, for context)
        }

    Returns:
        200: { "status": "ok", "subject": "...", "body": "...", "template_name": "..." }
    """
    data = request.get_json(silent=True) or {}
    profile_id = data.get("profile_id")
    template_key = data.get("template", "generic_us")

    if not profile_id:
        return _error("profile_id is required")

    profile = get_profile(profile_id)
    if not profile:
        return _error(f"Profile {profile_id} not found", 404)

    rendered = render_email_template(template_key, profile)
    if "error" in rendered:
        return _error(rendered["error"])

    return _success(rendered)


# ---------------------------------------------------------------------------
# POST /api/email/config — Save SMTP configuration
# ---------------------------------------------------------------------------

@api_bp.route("/email/config", methods=["POST"])
@require_api_key
def api_email_config_save():
    """
    Save SMTP configuration.

    JSON body:
        {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "user@gmail.com",
            "smtp_pass": "app-password-here",
            "from_email": "user@gmail.com",
            "from_name": "Your Name"
        }
    """
    data = request.get_json(silent=True) or {}

    required = ["smtp_host", "smtp_user", "smtp_pass"]
    for field in required:
        if not data.get(field):
            return _error(f"{field} is required")

    save_email_config(data)
    log_activity(None, None, "email_config_updated", "system", "SMTP configuration updated")

    return _success({"message": "SMTP configuration saved"})


# ---------------------------------------------------------------------------
# GET /api/email/config — Get SMTP configuration (masked)
# ---------------------------------------------------------------------------

@api_bp.route("/email/config", methods=["GET"])
@require_api_key
def api_email_config_get():
    """Get current SMTP configuration (password masked)."""
    config = get_email_config()
    if not config:
        return _success({"configured": False, "config": None})

    # Mask password
    masked = dict(config)
    if masked.get("smtp_pass"):
        masked["smtp_pass"] = "••••••••"

    return _success({"configured": True, "config": masked})


# ---------------------------------------------------------------------------
# POST /api/email/test — Test SMTP connection
# ---------------------------------------------------------------------------

@api_bp.route("/email/test", methods=["POST"])
@require_api_key
def api_email_test():
    """
    Test SMTP connection with current or provided configuration.

    If JSON body is provided, tests that config. Otherwise tests saved config.
    """
    data = request.get_json(silent=True)

    if data and data.get("smtp_host"):
        config = data
    else:
        config = get_email_config()
        if not config:
            return _error("No SMTP configuration found. Save config first.")

    result = test_smtp_connection(config)
    if result["success"]:
        return _success({"message": result["message"]})
    else:
        return _error(result["message"])


# ---------------------------------------------------------------------------
# GET /api/email/followups — Get pending follow-ups
# ---------------------------------------------------------------------------

@api_bp.route("/email/followups", methods=["GET"])
@require_api_key
def api_email_followups():
    """Get email requests that need follow-up (past follow-up date)."""
    followups = get_pending_followups()
    return _success({"followups": followups, "count": len(followups)})
