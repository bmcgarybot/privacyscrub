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
import os
from functools import wraps
from flask import Blueprint, request, jsonify, Response

from models import (
    get_profile, get_all_profiles, get_setting, set_setting,
    get_scan_results, get_latest_scan_results,
    get_breaches, get_optouts, get_all_settings,
    get_family_members, get_custom_removals, get_activity_log,
    log_activity, init_db, DB_PATH,
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
from breach import scan_profile_breaches, get_breach_summary, HIBPClient, classify_severity
from utils import calculate_privacy_score, export_full_report_json
from optout import OptOutManager, auto_submit_single_optout

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
                # Same-origin browser requests (the built-in dashboard UI)
                # are exempt — modern browsers set Sec-Fetch-Site and it
                # cannot be forged cross-site by a browser. External API
                # clients must still present the key.
                if request.headers.get("Sec-Fetch-Site") == "same-origin":
                    return f(*args, **kwargs)
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


# ===========================================================================
# UI-facing endpoints — these back the dashboard's JavaScript actions and
# complete the API surface documented at /api-docs. Every route wraps real
# application logic; none return placeholder or simulated data.
# ===========================================================================

# ---------------------------------------------------------------------------
# POST /api/scan/start — alias of POST /api/scan (documented path)
# ---------------------------------------------------------------------------

@api_bp.route("/scan/start", methods=["POST"])
@require_api_key
def api_start_scan_alias():
    """Alias for POST /api/scan, matching the documented endpoint path."""
    return api_start_scan()


# ---------------------------------------------------------------------------
# GET /api/scan/<batch_id>/status — scan progress for the dashboard poller
# ---------------------------------------------------------------------------

def _scan_status_payload(scan: dict) -> dict:
    """Translate internal scan state into the flat shape the UI poller reads."""
    total = scan.get("total", 0) or 0
    completed = scan.get("completed", 0) or 0
    progress = int(round((completed / total) * 100)) if total else 0
    status = scan.get("status", "unknown")
    if status in ("completed", "finished"):
        status = "complete"
        progress = 100
    current = scan.get("current_broker") or ""
    status_text = (
        f"Checking {current}… ({completed}/{total})" if status == "running" and current
        else f"Scanning… ({completed}/{total})" if status == "running"
        else f"Scan {status} — {scan.get('found', 0)} exposure(s) found"
    )
    return {
        "batch_id": scan.get("batch_id"),
        "profile_id": scan.get("profile_id"),
        "status": status,
        "progress": progress,
        "brokers_checked": completed,
        "total": total,
        "found": scan.get("found", 0),
        "errors": scan.get("errors", 0),
        "status_text": status_text,
    }


@api_bp.route("/scan/<batch_id>/status", methods=["GET"])
@require_api_key
def api_scan_status(batch_id: str):
    """
    Scan status in the shape the dashboard progress poller expects.

    Use 'latest' as the batch_id with a profile_id query param to get the
    most recent scan for a profile.
    """
    if batch_id == "latest":
        profile_id = request.args.get("profile_id", type=int)
        if not profile_id:
            return _error("profile_id query parameter required with 'latest'")
        scan = scan_state.get_latest(profile_id)
    else:
        scan = get_scan_status(batch_id)

    if not scan:
        return _error("Scan not found", 404)
    return jsonify(_scan_status_payload(scan))


# ---------------------------------------------------------------------------
# POST /api/optout/<broker_id>/submit — submit one opt-out from the UI
# ---------------------------------------------------------------------------

@api_bp.route("/optout/<broker_id>/submit", methods=["POST"])
@require_api_key
def api_optout_submit(broker_id: str):
    """
    Submit an opt-out for a broker on behalf of a profile.

    Finds the profile's existing pending/reappeared opt-out record for the
    broker (creating one if none exists), then attempts auto-submission.
    Brokers without an automated path return submitted=false with the
    manual opt-out URL so the UI can direct the user.

    JSON body: { "profile_id": int (required) }
    """
    data = request.get_json(silent=True) or {}
    profile_id = data.get("profile_id")
    if not profile_id:
        return _error("profile_id is required in request body")

    profile = get_profile(profile_id)
    if not profile:
        return _error(f"Profile {profile_id} not found", 404)

    manager = OptOutManager()
    broker = manager._get_broker(broker_id)
    if not broker:
        return _error(f"Unknown broker: {broker_id}", 404)

    # Reuse an open opt-out record if one exists; otherwise create one.
    existing = [
        o for o in get_optouts(profile_id)
        if o.get("broker_id") == broker_id
        and o.get("status") in ("pending", "reappeared")
    ]
    if existing:
        optout_id = existing[0]["id"]
    else:
        optout_id = manager.create_from_scan(profile_id, broker_id)

    result = auto_submit_single_optout(optout_id)

    if result.get("success"):
        return _success({
            "submitted": True,
            "draft": bool(result.get("draft")),
            "optout_id": optout_id,
            "broker_name": result.get("broker_name", broker.get("name", broker_id)),
            "message": result.get("message", "Opt-out submitted."),
        })

    # No automated path — report honestly and hand back manual instructions.
    return _success({
        "submitted": False,
        "optout_id": optout_id,
        "broker_name": broker.get("name", broker_id),
        "manual_required": True,
        "opt_out_url": broker.get("opt_out_url", ""),
        "message": result.get("message", result.get(
            "error", "This broker requires manual opt-out.")),
    })


# ---------------------------------------------------------------------------
# POST /api/optout/batch — submit many opt-outs (by opt-out record id)
# ---------------------------------------------------------------------------

@api_bp.route("/optout/batch", methods=["POST"])
@require_api_key
def api_optout_batch():
    """
    Attempt auto-submission for a list of existing opt-out records.

    JSON body: { "optout_ids": [int, ...] }
    Returns per-item results plus summary counts. Items whose brokers lack
    an automated path are counted as manual_required, never as submitted.
    """
    data = request.get_json(silent=True) or {}
    optout_ids = data.get("optout_ids") or data.get("broker_ids") or []
    try:
        optout_ids = [int(i) for i in optout_ids]
    except (TypeError, ValueError):
        return _error("optout_ids must be a list of integers")
    if not optout_ids:
        return _error("optout_ids is required and must be non-empty")

    submitted, drafts, manual, failed, items = 0, 0, 0, 0, []
    for oid in optout_ids:
        try:
            result = auto_submit_single_optout(oid)
        except Exception as e:  # pragma: no cover — defensive per-item guard
            logger.exception("Batch auto-submit error for optout %s", oid)
            result = {"success": False, "error": str(e)}

        if result.get("success"):
            submitted += 1
            if result.get("draft"):
                drafts += 1
        elif result.get("manual_required") or "manual" in str(
                result.get("message", "")).lower():
            manual += 1
        else:
            failed += 1

        items.append({
            "optout_id": oid,
            "success": bool(result.get("success")),
            "broker_name": result.get("broker_name", ""),
            "message": result.get("message", result.get("error", "")),
        })

    return _success({
        "count": submitted,
        "submitted": submitted,
        "drafts": drafts,
        "manual_required": manual,
        "failed": failed,
        "results": items,
    })


# ---------------------------------------------------------------------------
# POST /api/breaches/check — check a single email against HIBP
# ---------------------------------------------------------------------------

@api_bp.route("/breaches/check", methods=["POST"])
@require_api_key
def api_breaches_check():
    """
    Check one email address against Have I Been Pwned.

    JSON body: { "email": str }

    Requires a HIBP API key (Settings → API Keys). Without one this returns
    a clear 400 — it never fabricates results.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email or "@" not in email:
        return _error("A valid email address is required")

    client = HIBPClient()
    if not client.api_key:
        return _error(
            "Breach lookups require a Have I Been Pwned API key. "
            "Add one under Settings → API Keys (hibp_api_key).", 400)

    try:
        raw = client.check_email(email)
    except Exception as e:
        logger.exception("HIBP check failed for email lookup")
        return _error(f"Breach check failed: {e}", 502)

    breaches = [{
        "name": b.get("Title") or b.get("Name", "Unknown"),
        "date": b.get("BreachDate", ""),
        "records": f"{b.get('PwnCount', 0):,}",
        "severity": classify_severity(b),
        "data_types": b.get("DataClasses", []),
    } for b in (raw or [])]

    return _success({"email": email, "count": len(breaches), "breaches": breaches})


# ---------------------------------------------------------------------------
# POST /api/reports/generate — validate inputs, hand back a download URL
# ---------------------------------------------------------------------------

_REPORT_FORMAT_MAP = {
    "pdf": "pdf",
    "json": "json",
    "csv": "csv-scans",
    "csv-scans": "csv-scans",
    "csv-breaches": "csv-breaches",
    "csv-optouts": "csv-optouts",
}


@api_bp.route("/reports/generate", methods=["POST"])
@require_api_key
def api_reports_generate():
    """
    Prepare a report download.

    JSON body: { "profile_id": int, "format": "pdf"|"json"|"csv[-…]", "type": str }
    Validates the profile and format, then returns the concrete
    /reports/download URL for the browser to fetch.
    """
    data = request.get_json(silent=True) or {}
    profile_id = data.get("profile_id")
    if not profile_id:
        return _error("profile_id is required")
    if not get_profile(profile_id):
        return _error(f"Profile {profile_id} not found", 404)

    fmt = _REPORT_FORMAT_MAP.get(str(data.get("format", "pdf")).lower())
    if not fmt:
        return _error(f"Unsupported format: {data.get('format')}")

    return _success({
        "download_url": f"/reports/download/{fmt}?profile_id={profile_id}",
        "format": fmt,
        "profile_id": profile_id,
    })


# ---------------------------------------------------------------------------
# POST /api/settings/<section> — save a settings form section
# ---------------------------------------------------------------------------

_ALLOWED_SETTINGS_SECTIONS = {"general", "scanning", "notifications", "privacy", "api"}


@api_bp.route("/settings/<section>", methods=["POST"])
@require_api_key
def api_settings_save(section: str):
    """
    Persist key/value settings from a dashboard settings form.

    JSON body: flat { key: value } mapping. Keys prefixed with 'setting_'
    have the prefix stripped, mirroring the form-based /settings/update.
    """
    if section not in _ALLOWED_SETTINGS_SECTIONS:
        return _error(f"Unknown settings section: {section}", 404)

    data = request.get_json(silent=True) or {}
    if not data:
        return _error("No settings provided")

    saved = 0
    for key, value in data.items():
        clean_key = key[len("setting_"):] if key.startswith("setting_") else key
        set_setting(clean_key, str(value).strip(), category=section)
        saved += 1

    log_activity(None, None, "settings_updated", "system",
                 f"Settings updated via API ({section}: {saved} value(s))")
    return _success({"saved": saved, "section": section})


# ---------------------------------------------------------------------------
# GET /api/data/export — full JSON backup (all profiles + settings + log)
# ---------------------------------------------------------------------------

@api_bp.route("/data/export", methods=["GET"])
@require_api_key
def api_data_export():
    """Export all application data as a downloadable JSON backup."""
    from datetime import datetime, timezone

    all_data = {
        "export_date": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "profiles": [],
    }
    for profile in get_all_profiles():
        pid = profile["id"]
        all_data["profiles"].append({
            "profile": profile,
            "family_members": get_family_members(pid),
            "scan_results": get_scan_results(pid),
            "optouts": get_optouts(pid),
            "breaches": get_breaches(pid),
            "custom_removals": get_custom_removals(pid),
        })
    all_data["settings"] = get_all_settings()
    all_data["activity_log"] = get_activity_log(limit=1000)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        json.dumps(all_data, indent=2, default=str),
        mimetype="application/json",
        headers={"Content-Disposition":
                 f"attachment; filename=privacyscrub-backup-{stamp}.json"},
    )


# ---------------------------------------------------------------------------
# DELETE /api/data/all — wipe the database (requires typed confirmation)
# ---------------------------------------------------------------------------

@api_bp.route("/data/all", methods=["DELETE"])
@require_api_key
def api_data_delete_all():
    """
    Permanently delete all application data and reset the database.

    JSON body must include { "confirm": "DELETE" } — the same typed
    confirmation the settings form requires. Anything else is rejected.
    """
    data = request.get_json(silent=True) or {}
    if data.get("confirm") != "DELETE":
        return _error('Confirmation required: send {"confirm": "DELETE"}', 400)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

    logger.warning("All application data deleted via API")
    return _success({"deleted": True, "message": "All data deleted. Database reset."})


# ---------------------------------------------------------------------------
# POST /api/displacement/activate | /deactivate — emergency lockdown mode
# ---------------------------------------------------------------------------

def _set_displacement(active: bool):
    set_setting("displacement_mode", "1" if active else "0")
    action = "activated" if active else "deactivated"
    log_activity(None, None, f"displacement_{action}", "system",
                 f"Displacement mode {action}")
    return _success({"active": active,
                     "message": f"Displacement mode {action}."})


@api_bp.route("/displacement/activate", methods=["POST"])
@require_api_key
def api_displacement_activate():
    """Activate emergency privacy lockdown (displacement mode)."""
    return _set_displacement(True)


@api_bp.route("/displacement/deactivate", methods=["POST"])
@require_api_key
def api_displacement_deactivate():
    """Deactivate emergency privacy lockdown (displacement mode)."""
    return _set_displacement(False)


# ---------------------------------------------------------------------------
# POST /api/data/import — restore a JSON backup produced by /api/data/export
# ---------------------------------------------------------------------------

@api_bp.route("/data/import", methods=["POST"])
@require_api_key
def api_data_import():
    """
    Import a PrivacyScrub JSON backup (the file produced by Export All Data).

    Profiles in the backup are created as new profiles; their scan results,
    opt-outs, breaches, family members, and custom removals are re-linked to
    the new profile IDs. Duplicate opt-outs (same profile+broker) and
    breaches (same profile+breach) are merged by the model layer.

    Settings are NOT imported unless "include_settings": true is passed,
    to avoid clobbering this installation's SMTP/API-key configuration.
    The activity log is never imported.
    """
    from models import (
        create_profile, add_family_member, save_scan_result,
        save_optout, save_breach, add_custom_removal,
    )

    data = request.get_json(silent=True) or {}
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        return _error("Not a PrivacyScrub backup: missing 'profiles' list")

    counts = {"profiles": 0, "family_members": 0, "scan_results": 0,
              "optouts": 0, "breaches": 0, "custom_removals": 0,
              "settings": 0, "skipped": 0}

    def _clean(row: dict) -> dict:
        row = dict(row)
        row.pop("id", None)
        return row

    for entry in profiles:
        prof = entry.get("profile")
        if not isinstance(prof, dict) or not prof.get("first_name"):
            counts["skipped"] += 1
            continue

        new_pid = create_profile(_clean(prof))
        counts["profiles"] += 1

        for fm in entry.get("family_members", []) or []:
            row = _clean(fm)
            row["profile_id"] = new_pid
            try:
                add_family_member(row)
                counts["family_members"] += 1
            except Exception:
                logger.exception("Import: family member skipped")
                counts["skipped"] += 1

        for sr in entry.get("scan_results", []) or []:
            row = _clean(sr)
            row["profile_id"] = new_pid
            try:
                save_scan_result(row)
                counts["scan_results"] += 1
            except Exception:
                logger.exception("Import: scan result skipped")
                counts["skipped"] += 1

        for oo in entry.get("optouts", []) or []:
            row = _clean(oo)
            row["profile_id"] = new_pid
            try:
                save_optout(row)
                counts["optouts"] += 1
            except Exception:
                logger.exception("Import: optout skipped")
                counts["skipped"] += 1

        for br in entry.get("breaches", []) or []:
            row = _clean(br)
            row["profile_id"] = new_pid
            if not row.get("breach_name"):
                counts["skipped"] += 1
                continue
            try:
                save_breach(row)
                counts["breaches"] += 1
            except Exception:
                logger.exception("Import: breach skipped")
                counts["skipped"] += 1

        for cr in entry.get("custom_removals", []) or []:
            row = _clean(cr)
            row["profile_id"] = new_pid
            if not row.get("url"):
                counts["skipped"] += 1
                continue
            try:
                add_custom_removal(row)
                counts["custom_removals"] += 1
            except Exception:
                logger.exception("Import: custom removal skipped")
                counts["skipped"] += 1

    if data.get("include_settings") and isinstance(data.get("settings"), dict):
        for key, value in data["settings"].items():
            set_setting(key, str(value))
            counts["settings"] += 1

    log_activity(None, None, "data_imported", "system",
                 f"Backup imported: {counts['profiles']} profile(s)")
    return _success({"imported": counts})


# ---------------------------------------------------------------------------
# Webhooks — register, list, delete, test
# ---------------------------------------------------------------------------

@api_bp.route("/webhooks", methods=["POST"])
@require_api_key
def api_webhooks_register():
    """
    Register a webhook.

    JSON body:
        {
            "url": "https://your-server.com/webhook",   (required, http/https)
            "events": ["scan.complete", ...],           (required, non-empty)
            "active": true                              (optional, default true)
        }

    Deliveries are signed with X-PrivacyScrub-Signature when a
    webhook_secret is configured in settings.
    """
    from webhooks import KNOWN_EVENTS
    from models import add_webhook, get_webhook

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    events = data.get("events") or []

    if not url.startswith(("http://", "https://")):
        return _error("url is required and must start with http:// or https://")
    if not isinstance(events, list) or not events:
        return _error("events is required and must be a non-empty list")
    unknown = sorted(set(events) - KNOWN_EVENTS)
    if unknown:
        return _error(
            f"Unknown event(s): {', '.join(unknown)}. "
            f"Valid events: {', '.join(sorted(KNOWN_EVENTS))}")

    webhook_id = add_webhook(url, events, bool(data.get("active", True)))
    log_activity(None, None, "webhook_registered", "system",
                 f"Webhook #{webhook_id} registered for {', '.join(sorted(set(events)))}")
    return _success({"webhook": get_webhook(webhook_id)}, 201)


@api_bp.route("/webhooks", methods=["GET"])
@require_api_key
def api_webhooks_list():
    """List registered webhooks with their delivery status."""
    from models import get_webhooks
    hooks = get_webhooks()
    return _success({"webhooks": hooks, "count": len(hooks)})


@api_bp.route("/webhooks/<int:webhook_id>", methods=["DELETE"])
@require_api_key
def api_webhooks_delete(webhook_id: int):
    """Delete a registered webhook."""
    from models import delete_webhook
    if not delete_webhook(webhook_id):
        return _error(f"Webhook {webhook_id} not found", 404)
    log_activity(None, None, "webhook_deleted", "system",
                 f"Webhook #{webhook_id} deleted")
    return _success({"deleted": webhook_id})


@api_bp.route("/webhooks/<int:webhook_id>/test", methods=["POST"])
@require_api_key
def api_webhooks_test(webhook_id: int):
    """Send a synchronous test delivery to one webhook and report the result."""
    from models import get_webhook
    from webhooks import send_test

    hook = get_webhook(webhook_id)
    if not hook:
        return _error(f"Webhook {webhook_id} not found", 404)
    return _success({"test": send_test(hook)})
