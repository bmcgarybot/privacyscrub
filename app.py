"""
PrivacyScrub — Main Flask Application

Self-hosted privacy removal platform with 800+ data brokers.
Dark-themed web interface with 13 pages covering scanning, opt-outs,
legal requests, breach monitoring, and comprehensive reporting.

Pages:
    1.  /                   — Dashboard (Privacy DNA score, threat timeline)
    2.  /profiles           — Profile management (add/edit family members)
    3.  /scanner            — Run scans, view results
    4.  /optouts            — Opt-out center with status tracking
    5.  /legal              — Legal request generators (GDPR/CCPA/state)
    6.  /breaches           — Breach monitor (HIBP integration)
    7.  /accounts           — Account cleanup & deletion templates
    8.  /credit             — Credit freeze links & financial protection
    9.  /displacement       — Emergency privacy lockdown mode
    10. /reports            — PDF/CSV/JSON report generation
    11. /api-docs           — REST API documentation
    12. /settings           — Application settings
    13. /activity           — Full audit trail
"""

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from io import BytesIO

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_file, Response,
)

from models import (
    init_db, create_profile, get_profile, get_all_profiles,
    update_profile, delete_profile, add_family_member, get_family_members,
    delete_family_member, get_scan_results, get_latest_scan_results,
    get_optouts, get_all_optouts, update_optout_status,
    get_breaches, get_breach_count, get_exposure_count,
    get_activity_log, log_activity,
    get_setting, set_setting, get_all_settings, get_settings_by_category,
    add_custom_removal, get_custom_removals, save_optout,
)
from scanner import (
    start_scan, get_scan_status, load_brokers, get_broker_count,
    get_broker_categories, get_brokers_by_category, scan_state,
)
from optout import (
    OptOutManager, AutoSubmitter, batch_update_status,
    auto_submit_optouts, auto_submit_single_optout,
    get_credit_freeze_links, get_optout_prescreen,
    CREDIT_FREEZE_LINKS, OPT_OUT_PRESCREEN,
)
from legal import (
    generate_gdpr_erasure, generate_ccpa_delete, generate_state_request,
    generate_cease_desist, generate_account_deletion,
    generate_unsubscribe_request, get_legal_request_types, get_available_states,
)
from breach import (
    scan_profile_breaches, check_passwords, get_breach_summary, HIBPClient,
    schedule_breach_checks, get_last_breach_check,
)
from utils import (
    calculate_privacy_score, generate_pdf_report,
    export_scan_results_csv, export_breaches_csv, export_optouts_csv,
    export_full_report_json, format_timestamp,
)
from api import api_bp
from email_sender import (
    get_email_config, save_email_config, test_smtp_connection,
    send_batch, get_email_requests, get_email_summary,
    update_email_request_status, get_available_templates,
    render_template as render_email_template,
    TEMPLATES as EMAIL_TEMPLATES,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("privacyscrub")

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

    # Initialise database
    init_db()

    # Start the recurring breach check scheduler (weekly, background thread)
    schedule_breach_checks()

    # Start the reappearance monitor (daily, background thread)
    from reappearance import schedule_reappearance_checks
    schedule_reappearance_checks()

    # Register API blueprint
    app.register_blueprint(api_bp)

    # Register template filters
    @app.template_filter("timestamp")
    def timestamp_filter(value):
        return format_timestamp(value)

    @app.template_filter("json_loads")
    def json_loads_filter(value):
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value

    @app.template_filter("comma")
    def comma_filter(value):
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return value

    # Context processor — inject common data into all templates
    @app.context_processor
    def inject_globals():
        profiles = get_all_profiles()
        displacement_mode = get_setting("displacement_mode", "0") == "1"
        return {
            "all_profiles": profiles,
            "displacement_active": displacement_mode,
            "broker_total": get_broker_count(),
            "app_version": "1.0.0",
            "current_year": datetime.now(timezone.utc).year,
        }

    # Register all routes
    _register_routes(app)

    return app


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def _register_routes(app: Flask) -> None:
    """Register all page routes on the Flask app."""

    # ===================================================================
    # 1. DASHBOARD — /
    # ===================================================================

    @app.route("/")
    def dashboard():
        """Main dashboard — Privacy DNA score, exposure summary, recent activity."""
        profiles = get_all_profiles()
        profile = None
        score_data = None
        scan_results = []
        breaches_summary = {}
        optout_summary = {}
        recent_activity = []
        email_summary = {}
        broker_count = get_broker_count()
        found_count = 0
        removed_count = 0
        pending_count = 0

        profile_id = request.args.get("profile_id", type=int)
        if profiles:
            if profile_id:
                profile = get_profile(profile_id)
            if not profile:
                profile = profiles[0]

            if profile:
                scan_results = get_latest_scan_results(profile["id"])
                breaches = get_breaches(profile["id"])
                score_data = calculate_privacy_score(scan_results, breaches, broker_count)
                breaches_summary = get_breach_summary(profile["id"])

                manager = OptOutManager()
                optout_summary = manager.get_summary(profile["id"])
                recent_activity = get_activity_log(profile_id=profile["id"], limit=10)
                email_summary = get_email_summary(profile["id"])

                found_count = sum(1 for r in scan_results if r.get("found"))
                removed_count = optout_summary.get("confirmed", 0) if optout_summary else 0
                pending_count = optout_summary.get("pending", 0) if optout_summary else 0

        has_scans = bool(scan_results)

        return render_template(
            "dashboard.html",
            profiles=profiles,
            profile=profile,
            privacy_score=score_data.get("score", 0) if (score_data and has_scans) else -1,
            brokers_checked=broker_count if has_scans else 0,
            exposures_found=found_count,
            removed_count=removed_count,
            pending_count=pending_count,
            score=score_data,
            scan_results=scan_results,
            breaches_summary=breaches_summary,
            optout_summary=optout_summary,
            recent_activity=recent_activity,
            email_summary=email_summary,
            displacement_active=get_setting("displacement_active", "") == "1",
        )

    # ===================================================================
    # 2. PROFILES — /profiles
    # ===================================================================

    @app.route("/profiles")
    @app.route("/profiles/<int:profile_id>")
    def profiles_page(profile_id=None):
        """Profile management — list, add, edit profiles and family members."""
        profiles = get_all_profiles()
        family_data = {}
        for p in profiles:
            family_data[p["id"]] = get_family_members(p["id"])
            # Build display-friendly properties
            p["display_name"] = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "Unnamed"

            # Count emails (comma-separated in DB)
            email_str = p.get("email", "")
            p["email_count"] = len([e for e in email_str.split(",") if e.strip()]) if email_str else 0

            # Count phones (comma-separated in DB)
            phone_str = p.get("phone", "")
            p["phone_count"] = len([ph for ph in phone_str.split(",") if ph.strip()]) if phone_str else 0

            # Count addresses — stored as JSON array string
            addr_raw = p.get("addresses", "[]")
            try:
                import json as _json
                addr_list = _json.loads(addr_raw) if isinstance(addr_raw, str) else addr_raw
                p["address_count"] = len(addr_list) if isinstance(addr_list, list) else (1 if addr_raw else 0)
            except (ValueError, TypeError):
                p["address_count"] = 1 if addr_raw and addr_raw != "[]" else 0

            # Real scan stats
            scan_results = get_latest_scan_results(p["id"])
            if scan_results:
                found = sum(1 for r in scan_results if r.get("found"))
                breaches = get_breaches(p["id"])
                score = calculate_privacy_score(scan_results, breaches, get_broker_count())
                p["score"] = score.get("score", 0)
                p["exposures"] = found
                p["has_been_scanned"] = True
            else:
                p["score"] = None  # Not scanned yet
                p["exposures"] = 0
                p["has_been_scanned"] = False

        # If a specific profile ID was requested, find it
        selected_profile = None
        if profile_id:
            selected_profile = next((p for p in profiles if p["id"] == profile_id), None)

        return render_template("profiles.html", profiles=profiles, family_data=family_data, selected_profile=selected_profile)

    @app.route("/profiles/add", methods=["POST"])
    def profiles_add():
        """Add a new profile."""
        # Collect all emails (primary + extras)
        emails = [request.form.get("email", "").strip()]
        emails += [e.strip() for e in request.form.getlist("email_extra") if e.strip()]
        emails = [e for e in emails if e]

        # Collect all phones
        phones = [request.form.get("phone", "").strip()]
        phones += [p.strip() for p in request.form.getlist("phone_extra") if p.strip()]
        phones = [p for p in phones if p]

        # Collect all addresses
        addrs = [request.form.get("addresses", "").strip()]
        addrs += [a.strip() for a in request.form.getlist("address_extra") if a.strip()]
        addrs = [a for a in addrs if a]

        # Collect social accounts
        platforms = request.form.getlist("social_platform")
        urls = request.form.getlist("social_url")
        socials = []
        for plat, url in zip(platforms, urls):
            if url.strip():
                socials.append({"platform": plat, "url": url.strip()})

        data = {
            "first_name": request.form.get("first_name", "").strip(),
            "last_name": request.form.get("last_name", "").strip(),
            "middle_name": request.form.get("middle_name", "").strip(),
            "email": ",".join(emails),
            "phone": ",".join(phones),
            "date_of_birth": request.form.get("date_of_birth", "").strip(),
            "city": request.form.get("city", "").strip(),
            "state": request.form.get("state", "").strip(),
            "zip_code": request.form.get("zip_code", "").strip(),
            "addresses": addrs,
            "aliases": [a.strip() for a in request.form.get("aliases", "").split(",") if a.strip()],
            "social_accounts": socials,
            "is_primary": 1 if request.form.get("is_primary") else 0,
        }

        if not data["first_name"] or not data["last_name"]:
            flash("First name and last name are required.", "error")
            return redirect(url_for("profiles_page"))

        create_profile(data)
        flash(f"Profile created for {data['first_name']} {data['last_name']}.", "success")
        return redirect(url_for("profiles_page"))

    @app.route("/profiles/edit/<int:profile_id>", methods=["POST"])
    def profiles_edit(profile_id):
        """Edit an existing profile."""
        # Collect all emails (primary + extras)
        emails = [request.form.get("email", "").strip()]
        emails += [e.strip() for e in request.form.getlist("email_extra") if e.strip()]
        emails = [e for e in emails if e]

        # Collect all phones
        phones = [request.form.get("phone", "").strip()]
        phones += [p.strip() for p in request.form.getlist("phone_extra") if p.strip()]
        phones = [p for p in phones if p]

        # Collect all addresses
        addrs = [request.form.get("addresses", "").strip()]
        addrs += [a.strip() for a in request.form.getlist("address_extra") if a.strip()]
        addrs = [a for a in addrs if a]

        # Collect social accounts
        platforms = request.form.getlist("social_platform")
        urls = request.form.getlist("social_url")
        socials = []
        for plat, url in zip(platforms, urls):
            if url.strip():
                socials.append({"platform": plat, "url": url.strip()})

        data = {
            "first_name": request.form.get("first_name", "").strip(),
            "last_name": request.form.get("last_name", "").strip(),
            "middle_name": request.form.get("middle_name", "").strip(),
            "email": ",".join(emails),
            "phone": ",".join(phones),
            "date_of_birth": request.form.get("date_of_birth", "").strip(),
            "city": request.form.get("city", "").strip(),
            "state": request.form.get("state", "").strip(),
            "zip_code": request.form.get("zip_code", "").strip(),
            "addresses": addrs,
            "aliases": [a.strip() for a in request.form.get("aliases", "").split(",") if a.strip()],
            "social_accounts": socials,
        }

        update_profile(profile_id, data)
        flash("Profile updated.", "success")
        return redirect(url_for("profiles_page"))

    @app.route("/profiles/delete/<int:profile_id>", methods=["POST"])
    def profiles_delete(profile_id):
        """Delete a profile and all related data."""
        profile = get_profile(profile_id)
        if profile:
            delete_profile(profile_id)
            flash(f"Profile for {profile['first_name']} {profile['last_name']} deleted.", "success")
        return redirect(url_for("profiles_page"))

    @app.route("/profiles/<int:profile_id>/family/add", methods=["POST"])
    def family_add(profile_id):
        """Add a family member to a profile."""
        data = {
            "profile_id": profile_id,
            "first_name": request.form.get("first_name", "").strip(),
            "last_name": request.form.get("last_name", "").strip(),
            "relationship": request.form.get("relationship", "").strip(),
            "date_of_birth": request.form.get("date_of_birth", "").strip(),
            "email": request.form.get("email", "").strip(),
            "phone": request.form.get("phone", "").strip(),
            "is_minor": 1 if request.form.get("is_minor") else 0,
        }

        if not data["first_name"] or not data["last_name"]:
            flash("Family member name is required.", "error")
            return redirect(url_for("profiles_page"))

        add_family_member(data)
        flash(f"Family member {data['first_name']} added.", "success")
        return redirect(url_for("profiles_page"))

    @app.route("/profiles/family/delete/<int:member_id>", methods=["POST"])
    def family_delete(member_id):
        """Delete a family member."""
        delete_family_member(member_id)
        flash("Family member removed.", "success")
        return redirect(url_for("profiles_page"))

    # ===================================================================
    # 3. SCANNER — /scanner
    # ===================================================================

    @app.route("/scanner")
    def scanner_page():
        """Scanner — run scans and view results."""
        profiles = get_all_profiles()
        profile_id = request.args.get("profile_id", type=int)
        profile = None
        scan_results = []
        active_scan = None
        categories = get_broker_categories()
        broker_count = get_broker_count()

        if profile_id:
            profile = get_profile(profile_id)
        elif profiles:
            profile = profiles[0]

        if profile:
            scan_results = get_latest_scan_results(profile["id"])
            active_scan = scan_state.get_latest(profile["id"])

        # Group results by category
        results_by_category = {}
        for r in scan_results:
            cat = r.get("broker_category", "unknown")
            if cat not in results_by_category:
                results_by_category[cat] = {"found": [], "not_found": []}
            if r.get("found"):
                results_by_category[cat]["found"].append(r)
            else:
                results_by_category[cat]["not_found"].append(r)

        return render_template(
            "scanner.html",
            profile=profile,
            scan_results=scan_results,
            results_by_category=results_by_category,
            active_scan=active_scan,
            categories=categories,
            broker_count=broker_count,
        )

    @app.route("/scanner/start", methods=["POST"])
    def scanner_start():
        """Trigger a new scan."""
        profile_id = request.form.get("profile_id", type=int)
        if not profile_id:
            flash("Select a profile to scan.", "error")
            return redirect(url_for("scanner_page"))

        categories = request.form.getlist("categories")
        categories = categories if categories else None

        try:
            batch_id = start_scan(profile_id, categories=categories)
            flash(f"Scan started (batch: {batch_id}). Results will appear as brokers are checked.", "success")
        except ValueError as e:
            flash(str(e), "error")
        except Exception as e:
            logger.exception("Scan start error")
            flash(f"Error starting scan: {e}", "error")

        return redirect(url_for("scanner_page", profile_id=profile_id))

    @app.route("/scanner/status/<batch_id>")
    def scanner_status(batch_id):
        """AJAX endpoint — get live scan status."""
        status = get_scan_status(batch_id)
        if not status:
            return jsonify({"error": "Scan not found"}), 404
        return jsonify(status)

    # ===================================================================
    # 4. OPT-OUT CENTER — /optouts
    # ===================================================================

    @app.route("/optouts")
    def optouts_page():
        """Opt-out center — track and manage opt-out requests."""
        profiles = get_all_profiles()
        profile_id = request.args.get("profile_id", type=int)
        status_filter = request.args.get("status")
        profile = None
        optouts = []
        summary = {}
        auto_removable_count = 0
        auto_removable_pending = 0

        if profile_id:
            profile = get_profile(profile_id)
        elif profiles:
            profile = profiles[0]

        if profile:
            optouts = get_optouts(profile["id"], status=status_filter)
            manager = OptOutManager()
            summary = manager.get_summary(profile["id"])

            # Count auto-removable brokers among pending opt-outs
            all_brokers = load_brokers()
            broker_map = {b["id"]: b for b in all_brokers}
            pending_optouts = get_optouts(profile["id"], status="pending")
            for o in pending_optouts:
                broker = broker_map.get(o.get("broker_id", ""))
                if broker and broker.get("auto_removable", False):
                    auto_removable_pending += 1

            # Total auto-removable from all brokers
            auto_removable_count = sum(1 for b in all_brokers if b.get("auto_removable", False))

            # Add auto_removable flag to each optout for template use
            for o in optouts:
                broker = broker_map.get(o.get("broker_id", ""))
                o["auto_removable"] = broker.get("auto_removable", False) if broker else False

        # Confirmed opt-outs whose reappearance window has elapsed
        reappearance_due = []
        if profile:
            try:
                from reappearance import get_due_optouts
                reappearance_due = get_due_optouts(profile["id"])
            except Exception:
                app.logger.exception("Reappearance due lookup failed")

        return render_template(
            "optouts.html",
            profile=profile,
            optouts=optouts,
            summary=summary,
            status_filter=status_filter,
            auto_removable_count=auto_removable_count,
            auto_removable_pending=auto_removable_pending,
            reappearance_due=reappearance_due,
        )

    @app.route("/optouts/create", methods=["POST"])
    def optouts_create():
        """Create opt-out records from scan results."""
        profile_id = request.form.get("profile_id", type=int)
        broker_ids = request.form.getlist("broker_ids")

        if not profile_id:
            flash("Profile is required.", "error")
            return redirect(url_for("optouts_page"))

        manager = OptOutManager()
        scan_results = get_latest_scan_results(profile_id)

        if broker_ids:
            count = 0
            for bid in broker_ids:
                matching = [r for r in scan_results if r.get("broker_id") == bid and r.get("found")]
                for r in matching:
                    manager.create_from_scan(profile_id, bid, r.get("listing_url", ""))
                    count += 1
            flash(f"Created {count} opt-out request(s).", "success")
        else:
            ids = manager.create_batch_from_scan(profile_id, scan_results)
            flash(f"Created {len(ids)} opt-out request(s) from scan results.", "success")

        return redirect(url_for("optouts_page", profile_id=profile_id))

    @app.route("/optouts/update/<int:optout_id>", methods=["POST"])
    def optouts_update(optout_id):
        """Update opt-out status."""
        status = request.form.get("status", "").strip()
        notes = request.form.get("notes", "").strip()
        profile_id = request.form.get("profile_id", type=int)

        try:
            manager = OptOutManager()
            manager.update_status(optout_id, status, notes)
            flash(f"Opt-out status updated to '{status}'.", "success")
        except ValueError as e:
            flash(str(e), "error")

        return redirect(url_for("optouts_page", profile_id=profile_id))

    @app.route("/optouts/batch-update", methods=["POST"])
    def optouts_batch_update():
        """Batch update multiple opt-outs."""
        optout_ids = request.form.getlist("optout_ids", type=int)
        status = request.form.get("status", "").strip()
        notes = request.form.get("notes", "").strip()
        profile_id = request.form.get("profile_id", type=int)

        if not optout_ids or not status:
            flash("Select opt-outs and a status.", "error")
            return redirect(url_for("optouts_page", profile_id=profile_id))

        result = batch_update_status(optout_ids, status, notes)
        flash(f"Updated {result['updated']} opt-outs. {result['failed']} failed.", "success")
        return redirect(url_for("optouts_page", profile_id=profile_id))

    @app.route("/optouts/auto-submit", methods=["POST"])
    def optouts_auto_submit():
        """Trigger automated opt-out submissions."""
        profile_id = request.form.get("profile_id", type=int)
        if not profile_id:
            flash("Profile is required.", "error")
            return redirect(url_for("optouts_page"))

        result = auto_submit_optouts(profile_id)
        drafts = result.get("drafts", 0)
        msg = (
            f"Auto-submit complete: {result.get('submitted', 0)} submitted, "
            f"{result.get('failed', 0)} failed, {result.get('skipped', 0)} skipped."
        )
        if drafts:
            msg += f" {drafts} saved as drafts (SMTP not configured)."
        flash(msg, "success")
        return redirect(url_for("optouts_page", profile_id=profile_id))

    @app.route("/optouts/auto-submit/<int:profile_id>", methods=["POST"])
    def auto_submit(profile_id):
        """Trigger auto opt-out submission for all eligible pending brokers."""
        result = auto_submit_optouts(profile_id)
        drafts = result.get("drafts", 0)
        msg = (
            f"Auto-submit complete: {result.get('submitted', 0)} submitted, "
            f"{result.get('failed', 0)} failed, {result.get('skipped', 0)} skipped."
        )
        if drafts:
            msg += f" {drafts} saved as drafts (SMTP not configured)."
        flash(msg, "success")
        return redirect(url_for("optouts_page", profile_id=profile_id))

    @app.route("/optouts/auto-submit-single/<int:optout_id>", methods=["POST"])
    def auto_submit_single(optout_id):
        """Auto-submit a single opt-out."""
        result = auto_submit_single_optout(optout_id)
        profile_id = request.form.get("profile_id", type=int)
        if result.get("success"):
            msg = f"Auto-submitted opt-out for {result.get('broker_name', 'broker')}."
            if result.get("draft"):
                msg += " (Saved as draft — SMTP not configured)"
            flash(msg, "success")
        else:
            flash(f"Auto-submit failed: {result.get('message', result.get('error', 'Unknown error'))}", "error")
        return redirect(url_for("optouts_page", profile_id=profile_id))

    @app.route("/optouts/instructions/<broker_id>")
    def optouts_instructions(broker_id):
        """Get opt-out instructions for a broker (JSON for AJAX)."""
        manager = OptOutManager()
        instructions = manager.get_instructions(broker_id)
        return jsonify(instructions)

    @app.route("/scan-and-remove/<int:profile_id>", methods=["POST"])
    def scan_and_remove(profile_id):
        """Full automation: scan all brokers, then auto-submit opt-outs for all found."""
        profile = get_profile(profile_id)
        if not profile:
            flash("Profile not found.", "error")
            return redirect(url_for("dashboard"))

        # Step 1: Start scan and wait for results (synchronous for simplicity)
        try:
            batch_id = start_scan(profile_id)
        except Exception as e:
            flash(f"Scan failed: {e}", "error")
            return redirect(url_for("dashboard"))

        # Step 2: Create opt-outs from scan results
        scan_results = get_latest_scan_results(profile_id)
        manager = OptOutManager()
        created_ids = manager.create_batch_from_scan(profile_id, scan_results)

        # Step 3: Auto-submit eligible opt-outs
        submit_result = auto_submit_optouts(profile_id)

        drafts = submit_result.get("drafts", 0)
        found = sum(1 for r in scan_results if r.get("found"))
        msg = (
            f"Scan & Auto-Remove complete: Found on {found} brokers, "
            f"created {len(created_ids)} opt-outs, "
            f"{submit_result.get('submitted', 0)} auto-submitted, "
            f"{submit_result.get('skipped', 0)} require manual action."
        )
        if drafts:
            msg += f" {drafts} saved as email drafts."
        flash(msg, "success")

        log_activity(
            None, profile_id, "scan_and_remove", "optout",
            msg,
        )

        return redirect(url_for("optouts_page", profile_id=profile_id))

    @app.route("/optouts/custom/add", methods=["POST"])
    def optouts_custom_add():
        """Add a custom removal request."""
        profile_id = request.form.get("profile_id", type=int)
        url_val = request.form.get("url", "").strip()
        site_name = request.form.get("site_name", "").strip()
        notes = request.form.get("notes", "").strip()

        if not profile_id or not url_val:
            flash("Profile and URL are required.", "error")
            return redirect(url_for("optouts_page"))

        add_custom_removal({
            "profile_id": profile_id,
            "url": url_val,
            "site_name": site_name,
            "notes": notes,
        })
        flash(f"Custom removal request added for {site_name or url_val}.", "success")
        return redirect(url_for("optouts_page", profile_id=profile_id))

    # ===================================================================
    # 5. LEGAL CENTER — /legal
    # ===================================================================

    @app.route("/legal")
    def legal_page():
        """Legal center — GDPR/CCPA/state-specific request generators."""
        profiles = get_all_profiles()
        profile_id = request.args.get("profile_id", type=int)
        profile = None

        if profile_id:
            profile = get_profile(profile_id)
        elif profiles:
            profile = profiles[0]

        request_types = get_legal_request_types()
        available_states = get_available_states()
        user_state = get_setting("state_of_residence", "")

        return render_template(
            "legal.html",
            profile=profile,
            request_types=request_types,
            available_states=available_states,
            user_state=user_state,
        )

    @app.route("/legal/generate", methods=["POST"])
    def legal_generate():
        """Generate a legal request document."""
        profile_id = request.form.get("profile_id", type=int)
        request_type = request.form.get("request_type", "").strip()
        broker_name = request.form.get("broker_name", "").strip()
        broker_email = request.form.get("broker_email", "").strip()
        state_code = request.form.get("state_code", "").strip()
        listing_url = request.form.get("listing_url", "").strip()
        additional = request.form.get("additional_context", "").strip()

        if not profile_id:
            flash("Select a profile.", "error")
            return redirect(url_for("legal_page"))

        generators = {
            "gdpr_erasure": lambda: generate_gdpr_erasure(profile_id, broker_name, broker_email, additional),
            "ccpa_delete": lambda: generate_ccpa_delete(profile_id, broker_name, broker_email, additional),
            "state_specific": lambda: generate_state_request(profile_id, state_code, broker_name, broker_email),
            "cease_desist": lambda: generate_cease_desist(profile_id, broker_name, broker_email, listing_url, additional),
            "account_deletion": lambda: generate_account_deletion(profile_id, broker_name, broker_email),
            "unsubscribe": lambda: generate_unsubscribe_request(profile_id, broker_name, broker_email),
        }

        gen_func = generators.get(request_type)
        if not gen_func:
            flash(f"Unknown request type: {request_type}", "error")
            return redirect(url_for("legal_page"))

        result = gen_func()

        if "error" in result:
            flash(result["error"], "error")
            return redirect(url_for("legal_page", profile_id=profile_id))

        profile = get_profile(profile_id)
        user_state = get_setting("state_of_residence", "")
        return render_template(
            "legal.html",
            profile=profile,
            request_types=get_legal_request_types(),
            available_states=get_available_states(),
            generated=result,
            selected_type=request_type,
            user_state=user_state,
        )

    # ===================================================================
    # 6. BREACH MONITOR — /breaches
    # ===================================================================

    @app.route("/breaches")
    def breaches_page():
        """Breach monitor — HIBP breach lookup, timeline, and password audit."""
        profiles = get_all_profiles()
        profile_id = request.args.get("profile_id", type=int)
        filter_member = request.args.get("member", "")
        profile = None
        breaches = []
        summary = {}
        family = []
        last_check = get_last_breach_check()

        if profile_id:
            profile = get_profile(profile_id)
        elif profiles:
            profile = profiles[0]

        if profile:
            breaches = get_breaches(profile["id"])
            summary = get_breach_summary(profile["id"])
            family = get_family_members(profile["id"])

        return render_template(
            "breaches.html",
            profile=profile,
            breaches=breaches,
            summary=summary,
            family=family,
            filter_member=filter_member,
            last_check=last_check,
        )

    @app.route("/breaches/scan", methods=["POST"])
    def breaches_scan():
        """Run a breach scan for a profile."""
        profile_id = request.form.get("profile_id", type=int)
        if not profile_id:
            flash("Select a profile.", "error")
            return redirect(url_for("breaches_page"))

        result = scan_profile_breaches(profile_id)

        if result.get("error"):
            flash(result["error"], "error")
        else:
            flash(
                f"Breach scan complete: {result['total_breaches']} breach(es) found "
                f"({result['new_breaches']} new).",
                "success",
            )

        return redirect(url_for("breaches_page", profile_id=profile_id))

    @app.route("/breaches/password-check", methods=["POST"])
    def breaches_password_check():
        """Check a password against the HIBP database (k-anonymity — safe)."""
        password = request.form.get("password", "")
        if not password:
            return jsonify({"error": "No password provided"}), 400

        results = check_passwords([password])
        return jsonify(results[0])

    # ===================================================================
    # 7. ACCOUNT CLEANUP — /accounts
    # ===================================================================

    @app.route("/accounts")
    def accounts_page():
        """Account cleanup — old account finder and deletion templates."""
        profiles = get_all_profiles()
        profile_id = request.args.get("profile_id", type=int)
        profile = None

        if profile_id:
            profile = get_profile(profile_id)
        elif profiles:
            profile = profiles[0]

        common_services = [
            {"name": "Facebook", "email": "support@facebook.com",
             "url": "https://www.facebook.com/help/delete_account"},
            {"name": "Instagram", "email": "support@instagram.com",
             "url": "https://www.instagram.com/accounts/remove/request/permanent/"},
            {"name": "Twitter/X", "email": "privacy@twitter.com",
             "url": "https://twitter.com/settings/deactivate"},
            {"name": "LinkedIn", "email": "privacy@linkedin.com",
             "url": "https://www.linkedin.com/help/linkedin/answer/63"},
            {"name": "TikTok", "email": "privacy@tiktok.com",
             "url": "https://support.tiktok.com/en/account-and-privacy/deleting-an-account"},
            {"name": "Reddit", "email": "contact@reddit.com",
             "url": "https://www.reddit.com/settings"},
            {"name": "Pinterest", "email": "privacy@pinterest.com",
             "url": "https://help.pinterest.com/en/article/deactivate-or-close-your-account"},
            {"name": "Snapchat", "email": "support@snapchat.com",
             "url": "https://accounts.snapchat.com/accounts/delete_account"},
            {"name": "Discord", "email": "privacy@discord.com",
             "url": "https://support.discord.com/hc/en-us/articles/212500837"},
            {"name": "Spotify", "email": "privacy@spotify.com",
             "url": "https://www.spotify.com/account/close/"},
            {"name": "Amazon", "email": "privacy@amazon.com",
             "url": "https://www.amazon.com/gp/help/customer/display.html?nodeId=GDK92DNLSGWTV6MP"},
            {"name": "Google", "email": "privacy@google.com",
             "url": "https://myaccount.google.com/delete-services-or-account"},
        ]

        return render_template(
            "accounts.html",
            profile=profile,
            common_services=common_services,
        )

    @app.route("/accounts/generate-email", methods=["POST"])
    def accounts_generate_email():
        """Generate an account deletion email (AJAX)."""
        profile_id = request.form.get("profile_id", type=int)
        service_name = request.form.get("service_name", "").strip()
        service_email = request.form.get("service_email", "").strip()

        if not profile_id or not service_name:
            return jsonify({"error": "Profile and service name required"}), 400

        result = generate_account_deletion(profile_id, service_name, service_email)
        return jsonify(result)

    # ===================================================================
    # 8. CREDIT & FINANCIAL — /credit
    # ===================================================================

    @app.route("/credit")
    def credit_page():
        """Credit and financial protection — freeze links, monitoring setup."""
        return render_template(
            "credit.html",
            freeze_links=CREDIT_FREEZE_LINKS,
            optout_prescreen=OPT_OUT_PRESCREEN,
        )

    # ===================================================================
    # 9. DISPLACEMENT MODE — /displacement
    # ===================================================================

    @app.route("/email-center")
    def email_center_page():
        """Email Center — configure SMTP, send removal emails, track responses."""
        profiles = get_all_profiles()
        profile_id = request.args.get("profile_id", type=int)
        profile = None
        family = []
        email_config = get_email_config()
        summary = {}
        recent_requests = []

        if profile_id:
            profile = get_profile(profile_id)
        elif profiles:
            profile = profiles[0]

        if profile:
            family = get_family_members(profile["id"])
            summary = get_email_summary(profile["id"])
            recent_requests = get_email_requests(profile_id=profile["id"], limit=50)

        templates = get_available_templates()
        broker_count = get_broker_count()

        # Get priority breakdown from brokers
        brokers = load_brokers()
        priority_counts = {}
        for b in brokers:
            p = b.get("priority", "medium")
            priority_counts[p] = priority_counts.get(p, 0) + 1

        return render_template(
            "email_center.html",
            profile=profile,
            family=family,
            email_config=email_config,
            summary=summary,
            recent_requests=recent_requests,
            templates=templates,
            broker_count=broker_count,
            priority_counts=priority_counts,
        )

    @app.route("/email-center/config", methods=["POST"])
    def email_center_config():
        """Save SMTP configuration."""
        config = {
            "smtp_host": request.form.get("smtp_host", "").strip(),
            "smtp_port": request.form.get("smtp_port", "587").strip(),
            "smtp_user": request.form.get("smtp_user", "").strip(),
            "smtp_pass": request.form.get("smtp_pass", "").strip(),
            "from_email": request.form.get("from_email", "").strip(),
            "from_name": request.form.get("from_name", "").strip(),
        }

        if not config["smtp_host"] or not config["smtp_user"] or not config["smtp_pass"]:
            flash("SMTP host, username, and password are required.", "error")
            return redirect(url_for("email_center_page"))

        if not config["from_email"]:
            config["from_email"] = config["smtp_user"]

        save_email_config(config)
        log_activity(None, None, "email_config_saved", "system", "SMTP configuration saved")
        flash("SMTP configuration saved.", "success")
        return redirect(url_for("email_center_page"))

    @app.route("/email-center/test", methods=["POST"])
    def email_center_test():
        """Test SMTP connection."""
        config = get_email_config()
        if not config:
            flash("Configure SMTP settings first.", "error")
            return redirect(url_for("email_center_page"))

        result = test_smtp_connection(config)
        if result["success"]:
            flash("SMTP connection successful!", "success")
        else:
            flash(f"SMTP test failed: {result['message']}", "error")
        return redirect(url_for("email_center_page"))

    @app.route("/email-center/send", methods=["POST"])
    def email_center_send():
        """Send removal emails to brokers."""
        profile_id = request.form.get("profile_id", type=int)
        template_key = request.form.get("template", "generic_us")
        dry_run = request.form.get("dry_run") == "1"
        include_family = request.form.get("include_family") == "1"
        priority_filter = request.form.get("priority", "").strip() or None

        if not profile_id:
            flash("Select a profile.", "error")
            return redirect(url_for("email_center_page"))

        result = send_batch(
            profile_id=profile_id,
            broker_ids=[],
            template_key=template_key,
            dry_run=dry_run,
            include_family=include_family,
            priority_filter=priority_filter,
        )

        if "error" in result:
            flash(result["error"], "error")
        elif dry_run:
            flash(
                f"Dry run complete: {result['sent']} emails previewed, "
                f"{result['skipped']} skipped.",
                "success",
            )
        else:
            flash(
                f"Batch complete: {result['sent']} sent, "
                f"{result['failed']} failed, {result['skipped']} skipped. "
                f"{result['remaining_today']} remaining today.",
                "success",
            )

        return redirect(url_for("email_center_page", profile_id=profile_id))

    @app.route("/email-center/update/<int:request_id>", methods=["POST"])
    def email_center_update(request_id):
        """Update an email request status."""
        status = request.form.get("status", "").strip()
        response_text = request.form.get("response_text", "").strip()
        profile_id = request.form.get("profile_id", type=int)

        if status:
            update_email_request_status(request_id, status, response_text)
            flash(f"Email request status updated to '{status}'.", "success")
        return redirect(url_for("email_center_page", profile_id=profile_id))

    @app.route("/displacement")
    def displacement_page():
        """Displacement mode — emergency privacy lockdown."""
        is_active = get_setting("displacement_mode", "0") == "1"
        profiles = get_all_profiles()

        checklist = [
            {"step": "Freeze credit at all 4 bureaus (Equifax, Experian, TransUnion, Innovis)",
             "category": "credit"},
            {"step": "Freeze ChexSystems and NCTUE",
             "category": "credit"},
            {"step": "Place fraud alert at one bureau (propagates to all three)",
             "category": "credit"},
            {"step": "File Identity Theft Report at IdentityTheft.gov",
             "category": "legal"},
            {"step": "File a police report (needed for extended fraud alerts)",
             "category": "legal"},
            {"step": "Request free credit reports from AnnualCreditReport.com",
             "category": "credit"},
            {"step": "Opt out of pre-screened offers at OptOutPrescreen.com",
             "category": "credit"},
            {"step": "Change all passwords and enable 2FA everywhere",
             "category": "security"},
            {"step": "Submit opt-outs to all data brokers (use Scanner + Opt-Out Center)",
             "category": "privacy"},
            {"step": "Set up USPS Informed Delivery to monitor mail",
             "category": "mail"},
            {"step": "Review bank/credit statements for unauthorized activity",
             "category": "financial"},
            {"step": "Contact phone carrier to add SIM lock / port-out PIN",
             "category": "security"},
        ]

        return render_template(
            "displacement.html",
            is_active=is_active,
            checklist=checklist,
            freeze_links=CREDIT_FREEZE_LINKS,
        )

    @app.route("/displacement/toggle", methods=["POST"])
    def displacement_toggle():
        """Toggle displacement mode on/off."""
        current = get_setting("displacement_mode", "0")
        new_value = "0" if current == "1" else "1"
        set_setting("displacement_mode", new_value)

        action = "activated" if new_value == "1" else "deactivated"
        log_activity(None, None, f"displacement_{action}", "system",
                      f"Displacement mode {action}")
        flash(f"Displacement mode {action}.", "success")
        return redirect(url_for("displacement_page"))

    # ===================================================================
    # 10. REPORTS — /reports
    # ===================================================================

    @app.route("/reports")
    def reports_page():
        """Reports — generate PDF/CSV/JSON exports."""
        profiles = get_all_profiles()
        profile_id = request.args.get("profile_id", type=int)
        profile = None

        if profile_id:
            profile = get_profile(profile_id)
        elif profiles:
            profile = profiles[0]

        return render_template("reports.html", profile=profile)

    @app.route("/reports/download/<report_type>")
    def reports_download(report_type):
        """Download a report in the specified format."""
        profile_id = request.args.get("profile_id", type=int)
        if not profile_id:
            flash("Select a profile.", "error")
            return redirect(url_for("reports_page"))

        profile = get_profile(profile_id)
        if not profile:
            flash("Profile not found.", "error")
            return redirect(url_for("reports_page"))

        full_name = f"{profile.get('first_name', '')}_{profile.get('last_name', '')}".strip("_")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")

        scan_results = get_latest_scan_results(profile_id)
        breaches = get_breaches(profile_id)
        optouts = get_optouts(profile_id)
        broker_count = get_broker_count()
        score_data = calculate_privacy_score(scan_results, breaches, broker_count)

        try:
            if report_type == "pdf":
                pdf_bytes = generate_pdf_report(
                    profile, score_data, scan_results, breaches, optouts
                )
                return send_file(
                    BytesIO(pdf_bytes),
                    mimetype="application/pdf",
                    as_attachment=True,
                    download_name=f"privacyscrub-{full_name}-{timestamp}.pdf",
                )

            elif report_type == "json":
                json_str = export_full_report_json(
                    profile, score_data, scan_results, breaches, optouts
                )
                return Response(
                    json_str,
                    mimetype="application/json",
                    headers={
                        "Content-Disposition":
                            f"attachment; filename=privacyscrub-{full_name}-{timestamp}.json"
                    },
                )

            elif report_type == "csv-scans":
                csv_str = export_scan_results_csv(scan_results)
                return Response(
                    csv_str,
                    mimetype="text/csv",
                    headers={
                        "Content-Disposition":
                            f"attachment; filename=privacyscrub-scans-{full_name}-{timestamp}.csv"
                    },
                )

            elif report_type == "csv-breaches":
                csv_str = export_breaches_csv(breaches)
                return Response(
                    csv_str,
                    mimetype="text/csv",
                    headers={
                        "Content-Disposition":
                            f"attachment; filename=privacyscrub-breaches-{full_name}-{timestamp}.csv"
                    },
                )

            elif report_type == "csv-optouts":
                csv_str = export_optouts_csv(optouts)
                return Response(
                    csv_str,
                    mimetype="text/csv",
                    headers={
                        "Content-Disposition":
                            f"attachment; filename=privacyscrub-optouts-{full_name}-{timestamp}.csv"
                    },
                )

            else:
                flash(f"Unknown report type: {report_type}", "error")
                return redirect(url_for("reports_page", profile_id=profile_id))

        except ImportError as e:
            flash(f"Missing dependency for report generation: {e}", "error")
            return redirect(url_for("reports_page", profile_id=profile_id))
        except Exception as e:
            logger.exception("Report generation error")
            flash(f"Error generating report: {e}", "error")
            return redirect(url_for("reports_page", profile_id=profile_id))

    # ===================================================================
    # 11. API DOCS — /api-docs
    # ===================================================================

    @app.route("/api-docs")
    def api_docs_page():
        """REST API documentation page."""
        api_endpoints = [
            {
                "method": "GET",
                "path": "/api/health",
                "description": "Health check — returns service status and broker count.",
                "auth": False,
                "params": [],
            },
            {
                "method": "GET",
                "path": "/api/score",
                "description": "Get the Privacy DNA Score for a profile.",
                "auth": True,
                "params": [{"name": "profile_id", "type": "int", "required": True}],
            },
            {
                "method": "GET",
                "path": "/api/brokers",
                "description": "List all data brokers. Supports filtering by category, tier, and search.",
                "auth": True,
                "params": [
                    {"name": "category", "type": "string", "required": False},
                    {"name": "tier", "type": "int", "required": False},
                    {"name": "search", "type": "string", "required": False},
                    {"name": "limit", "type": "int", "required": False},
                    {"name": "offset", "type": "int", "required": False},
                ],
            },
            {
                "method": "POST",
                "path": "/api/scan",
                "description": "Start a new data broker scan. Returns batch_id for status polling.",
                "auth": True,
                "params": [
                    {"name": "profile_id", "type": "int", "required": True},
                    {"name": "categories", "type": "array", "required": False},
                    {"name": "broker_ids", "type": "array", "required": False},
                ],
            },
            {
                "method": "GET",
                "path": "/api/status",
                "description": "Get scan status. Use profile_id or batch_id in URL path.",
                "auth": True,
                "params": [
                    {"name": "profile_id", "type": "int", "required": False},
                ],
            },
            {
                "method": "GET",
                "path": "/api/status/<batch_id>",
                "description": "Get status of a specific scan batch.",
                "auth": True,
                "params": [],
            },
            {
                "method": "GET",
                "path": "/api/report",
                "description": "Generate a full JSON privacy audit report.",
                "auth": True,
                "params": [{"name": "profile_id", "type": "int", "required": True}],
            },
            {
                "method": "GET",
                "path": "/api/profiles",
                "description": "List all profiles.",
                "auth": True,
                "params": [],
            },
            {
                "method": "GET",
                "path": "/api/breaches",
                "description": "Get breach data and summary for a profile.",
                "auth": True,
                "params": [{"name": "profile_id", "type": "int", "required": True}],
            },
            {
                "method": "GET",
                "path": "/api/optouts",
                "description": "Get opt-out records for a profile.",
                "auth": True,
                "params": [
                    {"name": "profile_id", "type": "int", "required": True},
                    {"name": "status", "type": "string", "required": False},
                ],
            },
        ]

        return render_template("api_docs.html", endpoints=api_endpoints)

    # ===================================================================
    # 12. SETTINGS — /settings
    # ===================================================================

    @app.route("/settings")
    def settings_page():
        """Application settings."""
        all_settings = get_all_settings()
        categories = {
            "general": get_settings_by_category("general"),
            "scan": get_settings_by_category("scan"),
            "webhook": get_settings_by_category("webhook"),
            "notification": get_settings_by_category("notification"),
            "display": get_settings_by_category("display"),
        }
        # State of residence
        current_state = get_setting("state_of_residence", "")
        available_states = get_available_states()
        current_state_info = None
        if current_state:
            for st in available_states:
                if st["code"] == current_state:
                    current_state_info = st
                    break
        return render_template(
            "settings.html",
            settings=all_settings,
            categories=categories,
            all_states=available_states,
            current_state=current_state,
            current_state_info=current_state_info,
            broker_count=get_broker_count(),
            profile_count=len(get_all_profiles()),
            scan_count=len(get_activity_log(category="scan", limit=10000)),
        )

    @app.route("/settings/state", methods=["POST"])
    def settings_state():
        """Save state of residence setting."""
        state_code = request.form.get("state_of_residence", "").strip().upper()
        set_setting("state_of_residence", state_code, "general", "User state of residence for legal templates")
        log_activity(None, None, "settings_updated", "system", f"State of residence set to {state_code}")
        flash(f"State of residence saved: {state_code}" if state_code else "State of residence cleared.", "success")
        return redirect(url_for("settings_page"))

    @app.route("/settings/scan-frequency", methods=["POST"])
    def settings_scan_frequency():
        """Save scan frequency setting."""
        freq = request.form.get("frequency", "monthly").strip()
        set_setting("scan_frequency", freq, "scan", "How often to re-scan data brokers")
        log_activity(None, None, "settings_updated", "system", f"Scan frequency set to {freq}")
        flash(f"Scan frequency saved: {freq}.", "success")
        return redirect(url_for("settings_page"))

    @app.route("/settings/api-keys", methods=["POST"])
    def settings_api_keys():
        """Save API key settings."""
        hibp_key = request.form.get("hibp_key", "").strip()
        webhook_url = request.form.get("webhook_url", "").strip()
        set_setting("hibp_api_key", hibp_key, "webhook", "Have I Been Pwned API key")
        set_setting("webhook_url", webhook_url, "webhook", "Webhook notification URL")
        log_activity(None, None, "settings_updated", "system", "API keys updated")
        flash("API settings saved.", "success")
        return redirect(url_for("settings_page"))

    @app.route("/settings/update", methods=["POST"])
    def settings_update():
        """Update application settings."""
        for key in request.form:
            if key.startswith("setting_"):
                setting_key = key.replace("setting_", "", 1)
                value = request.form[key].strip()
                set_setting(setting_key, value)

        log_activity(None, None, "settings_updated", "system", "Settings updated")
        flash("Settings saved.", "success")
        return redirect(url_for("settings_page"))

    @app.route("/settings/export-data")
    def settings_export_data():
        """Export all data as JSON (backup)."""
        profiles = get_all_profiles()
        all_data = {
            "export_date": datetime.now(timezone.utc).isoformat(),
            "version": "1.0.0",
            "profiles": [],
        }

        for profile in profiles:
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

        return Response(
            json.dumps(all_data, indent=2, default=str),
            mimetype="application/json",
            headers={
                "Content-Disposition":
                    f"attachment; filename=privacyscrub-backup-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
            },
        )

    @app.route("/settings/delete-all-data", methods=["POST"])
    def settings_delete_all_data():
        """Delete all application data (nuclear option)."""
        confirm = request.form.get("confirm", "")
        if confirm != "DELETE":
            flash("Type DELETE to confirm data deletion.", "error")
            return redirect(url_for("settings_page"))

        from models import DB_PATH
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
            init_db()

        flash("All data has been deleted. Database reset.", "success")
        return redirect(url_for("settings_page"))

    # ===================================================================
    # 13. ACTIVITY LOG — /activity
    # ===================================================================

    @app.route("/activity")
    def activity_page():
        """Full audit trail of all system actions."""
        profile_id = request.args.get("profile_id", type=int)
        category = request.args.get("category")
        page = request.args.get("page", 1, type=int)
        per_page = 50
        offset = (page - 1) * per_page

        activities = get_activity_log(
            profile_id=profile_id,
            category=category,
            limit=per_page + 1,  # fetch one extra to check if there's a next page
            offset=offset,
        )

        has_next = len(activities) > per_page
        activities = activities[:per_page]

        categories = [
            "general", "scan", "optout", "breach", "legal",
            "system", "export",
        ]

        return render_template(
            "activity.html",
            activities=activities,
            current_page=page,
            has_next=has_next,
            has_prev=page > 1,
            profile_id=profile_id,
            category=category,
            categories=categories,
        )

    # ===================================================================
    # Error handlers
    # ===================================================================

    @app.errorhandler(404)
    def not_found(e):
        return render_template("base.html", error_title="404", error_message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception("Internal server error")
        return render_template("base.html", error_title="500", error_message="Internal server error."), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    print(f"""
    ╔══════════════════════════════════════════════════════╗
    ║          🛡️  PrivacyScrub v1.0.0                     ║
    ║          Self-Hosted Privacy Removal Platform        ║
    ║                                                      ║
    ║  Dashboard:  http://localhost:{port}                   ║
    ║  API:        http://localhost:{port}/api/health         ║
    ║  API Docs:   http://localhost:{port}/api-docs           ║
    ╚══════════════════════════════════════════════════════╝
    """)

    app.run(host="0.0.0.0", port=port, debug=debug)