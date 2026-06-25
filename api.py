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
