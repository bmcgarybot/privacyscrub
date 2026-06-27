"""
PrivacyScrub — Opt-Out Tracking & Automation

Manages the lifecycle of opt-out requests across all brokers:
    pending → submitted → confirmed → (reappeared)
                                    ↘ failed

Features:
    - Status management with full audit trail
    - Automated form submission for simple opt-out pages
    - Batch opt-out across parent company networks
    - Re-check scheduler to detect reappearances
    - Expected completion date calculation
    - Opt-out instruction generation with step-by-step guides
"""

import json
import logging
import os
import re
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from models import (
    save_optout, get_optouts, get_all_optouts, update_optout_status,
    get_profile, log_activity, db_session, get_setting,
)
from scanner import load_brokers, _get_user_agent, _get_proxies
from legal import generate_state_request

logger = logging.getLogger("privacyscrub.optout")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Statuses in lifecycle order
VALID_STATUSES = ("pending", "submitted", "confirmed", "reappeared", "failed")

# Opt-out methods
METHODS = {
    "form": "Online form submission",
    "email": "Email request",
    "phone": "Phone call required",
    "mail": "Physical mail required",
    "api": "API-based removal",
    "account": "Requires account creation first",
}


# ---------------------------------------------------------------------------
# Opt-Out Manager
# ---------------------------------------------------------------------------

class OptOutManager:
    """
    Manages opt-out request lifecycle.

    Usage:
        manager = OptOutManager()

        # Create opt-out from scan result
        manager.create_from_scan(profile_id=1, broker_id="whitepages")

        # Batch create from all found results
        manager.create_batch_from_scan(profile_id=1, scan_results=[...])

        # Update status
        manager.update_status(optout_id=42, status="submitted")

        # Auto-submit where possible
        manager.auto_submit(profile_id=1)
    """

    def __init__(self):
        self._brokers_cache: list[dict] | None = None

    def _get_broker(self, broker_id: str) -> Optional[dict]:
        """Look up a broker by ID from the database."""
        if self._brokers_cache is None:
            self._brokers_cache = load_brokers()
        for b in self._brokers_cache:
            if b.get("id") == broker_id:
                return b
        return None

    def create_from_scan(
        self,
        profile_id: int,
        broker_id: str,
        listing_url: str = "",
    ) -> int:
        """
        Create an opt-out record from a scan result.

        Args:
            profile_id: Profile that was found.
            broker_id: Broker where profile was found.
            listing_url: Direct URL to the listing (if found).

        Returns:
            Opt-out record ID.
        """
        broker = self._get_broker(broker_id)
        if not broker:
            logger.warning("Broker %s not found in database", broker_id)
            broker = {"id": broker_id, "name": broker_id, "opt_out_method": "email"}

        # Calculate expected completion
        processing_days = broker.get("processing_days", 30)
        expected = (datetime.now(timezone.utc) + timedelta(days=processing_days)).isoformat()

        data = {
            "profile_id": profile_id,
            "broker_id": broker_id,
            "broker_name": broker.get("name", broker_id),
            "status": "pending",
            "opt_out_method": broker.get("opt_out_method", "email"),
            "expected_completion": expected,
            "notes": f"Listing URL: {listing_url}" if listing_url else "",
        }

        optout_id = save_optout(data)

        log_activity(
            None, profile_id, "optout_created", "optout",
            f"Opt-out created for {broker.get('name', broker_id)}",
            {"broker_id": broker_id, "optout_id": optout_id},
        )

        return optout_id

    def create_batch_from_scan(
        self,
        profile_id: int,
        scan_results: list[dict],
    ) -> list[int]:
        """
        Create opt-out records for all brokers where the profile was found.

        Args:
            profile_id: Profile ID.
            scan_results: Scan results (only 'found' entries will be processed).

        Returns:
            List of opt-out record IDs created.
        """
        created_ids = []
        found_results = [r for r in scan_results if r.get("found")]

        for result in found_results:
            try:
                optout_id = self.create_from_scan(
                    profile_id=profile_id,
                    broker_id=result["broker_id"],
                    listing_url=result.get("listing_url", ""),
                )
                created_ids.append(optout_id)
            except Exception as e:
                logger.error("Failed to create opt-out for %s: %s",
                             result.get("broker_id", "?"), e)

        log_activity(
            None, profile_id, "batch_optout_created", "optout",
            f"Batch opt-out: {len(created_ids)} records created from {len(found_results)} scan hits",
        )

        return created_ids

    def update_status(
        self,
        optout_id: int,
        status: str,
        notes: str = "",
    ) -> bool:
        """
        Update the status of an opt-out request.

        Args:
            optout_id: Record ID.
            status: New status (must be in VALID_STATUSES).
            notes: Optional notes about the status change.

        Returns:
            True if updated successfully.

        Raises:
            ValueError: If status is invalid.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of: {VALID_STATUSES}")

        success = update_optout_status(optout_id, status, notes)

        if success:
            log_activity(
                None, None, f"optout_{status}", "optout",
                f"Opt-out #{optout_id} → {status}" + (f": {notes}" if notes else ""),
                {"optout_id": optout_id, "status": status},
            )

        return success

    def get_summary(self, profile_id: int) -> dict:
        """
        Get a summary of opt-out statuses for a profile.

        Returns:
            {
                "total": 45,
                "pending": 10,
                "submitted": 20,
                "confirmed": 12,
                "reappeared": 2,
                "failed": 1,
                "completion_rate": 0.267,
            }
        """
        optouts = get_optouts(profile_id)
        counts = {s: 0 for s in VALID_STATUSES}
        for o in optouts:
            status = o.get("status", "pending")
            if status in counts:
                counts[status] += 1

        total = len(optouts)
        confirmed = counts["confirmed"]
        completion_rate = confirmed / total if total > 0 else 0.0

        return {
            "total": total,
            **counts,
            "completion_rate": round(completion_rate, 3),
        }

    def get_instructions(self, broker_id: str) -> dict:
        """
        Get opt-out instructions for a specific broker.

        Returns:
            {
                "broker_name": "Whitepages",
                "method": "form",
                "method_description": "Online form submission",
                "opt_out_url": "https://...",
                "difficulty": "medium",
                "estimated_time": "5 minutes",
                "processing_days": 2,
                "verification_type": "phone",
                "step_by_step": "1. Go to...\n2. Search...",
                "notes": "Phone verification via automated call",
            }
        """
        broker = self._get_broker(broker_id)
        if not broker:
            return {
                "broker_name": broker_id,
                "method": "email",
                "method_description": "Email the site to request removal",
                "opt_out_url": "",
                "difficulty": "unknown",
                "estimated_time": "Unknown",
                "processing_days": 30,
                "verification_type": "none",
                "step_by_step": "Contact the website directly to request data removal.",
                "notes": "Broker not in database — submit a manual removal request.",
            }

        method = broker.get("opt_out_method", "email")
        return {
            "broker_name": broker.get("name", broker_id),
            "method": method,
            "method_description": METHODS.get(method, method),
            "opt_out_url": broker.get("opt_out_url", ""),
            "difficulty": broker.get("difficulty", "unknown"),
            "estimated_time": f"{broker.get('estimated_time_minutes', '?')} minutes",
            "processing_days": broker.get("processing_days", 30),
            "verification_type": broker.get("verification_type", "none"),
            "step_by_step": broker.get("step_by_step", "No step-by-step instructions available."),
            "notes": broker.get("notes", ""),
        }

    def get_network_siblings(self, broker_id: str) -> list[dict]:
        """
        Find other brokers owned by the same parent company.

        Useful for batch opt-outs — some parent companies honour one
        request across all their sites.

        Args:
            broker_id: Starting broker ID.

        Returns:
            List of sibling broker dicts (same parent_company).
        """
        broker = self._get_broker(broker_id)
        if not broker:
            return []

        parent = broker.get("parent_company", "")
        if not parent:
            return []

        brokers = load_brokers()
        siblings = [
            b for b in brokers
            if b.get("parent_company") == parent and b.get("id") != broker_id
        ]

        # Also include explicit network_sites
        network_ids = set(broker.get("network_sites", []))
        network_brokers = [b for b in brokers if b.get("id") in network_ids]

        # Combine and deduplicate
        seen = {broker_id}
        result = []
        for b in siblings + network_brokers:
            if b["id"] not in seen:
                seen.add(b["id"])
                result.append(b)

        return result


# ---------------------------------------------------------------------------
# Automated Form Submission
# ---------------------------------------------------------------------------

class AutoSubmitter:
    """
    Attempts automated opt-out form submissions for simple broker sites.

    This handles brokers with straightforward HTML forms:
    1. Fetch the opt-out page
    2. Find the form
    3. Fill in profile data
    4. Submit

    Only used when broker.auto_removable is True. For complex sites
    (CAPTCHA, phone verification, etc.), manual submission is required.
    """

    def __init__(self):
        self.session = requests.Session()

    def attempt_submission(
        self,
        broker: dict,
        profile: dict,
        listing_url: str = "",
    ) -> dict:
        """
        Attempt automated opt-out form submission.

        Args:
            broker: Broker dict from database.
            profile: User profile dict.
            listing_url: URL of the specific listing to remove.

        Returns:
            {
                "success": bool,
                "method": "auto_form",
                "message": str,
                "response_code": int | None,
            }
        """
        result = {
            "success": False,
            "method": "auto_form",
            "message": "",
            "response_code": None,
        }

        if not broker.get("auto_removable", False):
            result["message"] = "Broker does not support automated removal"
            return result

        opt_out_url = broker.get("opt_out_url", "")
        if not opt_out_url:
            result["message"] = "No opt-out URL available"
            return result

        try:
            # Fetch the opt-out page
            headers = {"User-Agent": _get_user_agent()}
            proxies = _get_proxies()

            response = self.session.get(
                opt_out_url,
                headers=headers,
                proxies=proxies,
                timeout=15,
            )

            if response.status_code != 200:
                result["message"] = f"Failed to load opt-out page (HTTP {response.status_code})"
                result["response_code"] = response.status_code
                return result

            # Parse the form
            soup = BeautifulSoup(response.text, "lxml")
            form = self._find_optout_form(soup)

            if not form:
                result["message"] = "Could not locate opt-out form on the page"
                return result

            # Build form data
            form_data = self._build_form_data(form, profile, listing_url)
            action_url = self._resolve_form_action(form, opt_out_url)

            # Submit
            submit_response = self.session.post(
                action_url,
                data=form_data,
                headers={
                    "User-Agent": _get_user_agent(),
                    "Referer": opt_out_url,
                },
                proxies=proxies,
                timeout=20,
                allow_redirects=True,
            )

            result["response_code"] = submit_response.status_code

            if submit_response.status_code in (200, 201, 302):
                # Check for success indicators in response
                resp_text = submit_response.text.lower()
                success_indicators = [
                    "thank you", "request received", "successfully",
                    "submitted", "confirmation", "we will process",
                    "removal request", "opt-out request",
                ]
                if any(ind in resp_text for ind in success_indicators):
                    result["success"] = True
                    result["message"] = "Opt-out form submitted successfully"
                else:
                    result["success"] = True  # Assume success on 200/302
                    result["message"] = "Form submitted (confirmation pending)"
            else:
                result["message"] = f"Form submission returned HTTP {submit_response.status_code}"

        except requests.Timeout:
            result["message"] = "Request timed out"
        except requests.RequestException as e:
            result["message"] = f"Network error: {str(e)}"
        except Exception as e:
            logger.error("Auto-submit error for %s: %s", broker.get("id"), e)
            result["message"] = f"Unexpected error: {str(e)}"

        return result

    def _find_optout_form(self, soup: BeautifulSoup) -> Optional[BeautifulSoup]:
        """
        Locate the opt-out/removal form on the page.

        Heuristic: look for forms containing keywords like 'remove',
        'opt-out', 'suppress', 'delete', etc.
        """
        forms = soup.find_all("form")
        if not forms:
            return None

        optout_keywords = [
            "opt-out", "optout", "remove", "removal", "suppress",
            "delete", "erasure", "privacy", "unlist",
        ]

        for form in forms:
            form_text = form.get_text(strip=True).lower()
            form_action = (form.get("action") or "").lower()

            if any(kw in form_text or kw in form_action for kw in optout_keywords):
                return form

        # Fallback: return the first form if only one exists
        if len(forms) == 1:
            return forms[0]

        return None

    def _build_form_data(
        self,
        form: BeautifulSoup,
        profile: dict,
        listing_url: str,
    ) -> dict:
        """
        Auto-fill form fields with profile data based on field names/labels.
        """
        form_data = {}

        # Collect all input fields
        inputs = form.find_all(["input", "textarea", "select"])

        for inp in inputs:
            name = inp.get("name", "")
            if not name:
                continue

            field_type = inp.get("type", "text").lower()
            name_lower = name.lower()

            # Skip submit/hidden CSRF tokens
            if field_type == "submit":
                continue
            if field_type == "hidden":
                form_data[name] = inp.get("value", "")
                continue

            # Map common field names to profile data
            if any(kw in name_lower for kw in ("first", "fname", "given")):
                form_data[name] = profile.get("first_name", "")
            elif any(kw in name_lower for kw in ("last", "lname", "surname", "family")):
                form_data[name] = profile.get("last_name", "")
            elif any(kw in name_lower for kw in ("email", "mail")):
                form_data[name] = profile.get("email", "")
            elif any(kw in name_lower for kw in ("phone", "tel", "mobile")):
                form_data[name] = profile.get("phone", "")
            elif any(kw in name_lower for kw in ("city",)):
                form_data[name] = profile.get("city", "")
            elif any(kw in name_lower for kw in ("state", "region")):
                form_data[name] = profile.get("state", "")
            elif any(kw in name_lower for kw in ("zip", "postal")):
                form_data[name] = profile.get("zip_code", "")
            elif any(kw in name_lower for kw in ("address", "street")):
                addresses = profile.get("addresses", "[]")
                if isinstance(addresses, str):
                    try:
                        addresses = json.loads(addresses)
                    except (json.JSONDecodeError, TypeError):
                        addresses = []
                form_data[name] = addresses[0] if addresses else ""
            elif any(kw in name_lower for kw in ("url", "link", "listing", "profile")):
                form_data[name] = listing_url
            elif any(kw in name_lower for kw in ("name", "full")):
                form_data[name] = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
            elif any(kw in name_lower for kw in ("reason", "description", "comment", "message")):
                form_data[name] = (
                    "I am requesting the removal of my personal information from your website. "
                    "This data was posted without my consent. Please remove all records associated "
                    "with my name and contact information."
                )
            elif field_type == "checkbox":
                # Check agreement/consent checkboxes
                form_data[name] = "on"
            else:
                # Leave unknown fields with default value
                form_data[name] = inp.get("value", "")

        return form_data

    def _resolve_form_action(self, form: BeautifulSoup, page_url: str) -> str:
        """Resolve the form's action URL (may be relative)."""
        action = form.get("action", "")
        if not action:
            return page_url
        if action.startswith("http"):
            return action
        return urljoin(page_url, action)


# ---------------------------------------------------------------------------
# Email-Based Auto-Submission
# ---------------------------------------------------------------------------

class EmailAutoSubmitter:
    """
    Sends privacy removal emails to brokers using the legal template system.

    For brokers where auto_removable is True and process_type is 'email-only'
    or 'mixed', this generates a state-specific removal letter and sends it
    to the broker's privacy_email address.

    If SMTP is not configured, emails are saved as drafts in the Email Center.
    """

    def attempt_email_submission(
        self,
        broker: dict,
        profile: dict,
        profile_id: int,
    ) -> dict:
        """
        Attempt to send a removal email to a broker.

        Returns:
            {"success": bool, "method": "email", "message": str, "draft": bool}
        """
        from email_sender import (
            get_email_config, send_single_email, _record_email_request,
            can_send_more,
        )

        result = {
            "success": False,
            "method": "email",
            "message": "",
            "draft": False,
        }

        privacy_email = broker.get("privacy_email", "")
        if not privacy_email:
            result["message"] = "Broker has no privacy_email address"
            return result

        # Determine user's state for legal template
        state_code = profile.get("state", "") or get_setting("state_of_residence", "")
        if not state_code:
            state_code = "AZ"  # Default fallback

        # Generate state-specific removal letter
        letter = generate_state_request(
            profile_id,
            state_code,
            broker_name=broker.get("name", ""),
            broker_email=privacy_email,
        )

        if "error" in letter:
            # Fall back to generic template
            from email_sender import render_template as render_email_tmpl
            letter = render_email_tmpl("generic_us", profile, broker)
            if "error" in letter:
                result["message"] = f"Template error: {letter['error']}"
                return result

        subject = letter.get("subject", f"Data Removal Request — {profile.get('first_name', '')} {profile.get('last_name', '')}")
        body = letter.get("body", "")

        # Check if SMTP is configured
        config = get_email_config()

        if not config:
            # Save as draft in Email Center
            _record_email_request(
                profile_id=profile_id,
                broker_id=broker.get("id", ""),
                to_email=privacy_email,
                subject=subject,
                template_key="state_specific",
                status="preview",
                batch_id=f"auto-optout-{int(time.time())}",
            )
            result["success"] = True
            result["draft"] = True
            result["message"] = f"Draft saved (SMTP not configured) — {privacy_email}"
            return result

        # Check rate limit
        can_send, remaining = can_send_more()
        if not can_send:
            result["message"] = "Daily email limit reached"
            return result

        # Send the email
        from_email = config.get("from_email", config.get("smtp_user", ""))
        from_name = config.get("from_name", "PrivacyScrub")

        send_result = send_single_email(
            config, privacy_email, subject, body, from_email, from_name,
        )

        if send_result["success"]:
            _record_email_request(
                profile_id=profile_id,
                broker_id=broker.get("id", ""),
                to_email=privacy_email,
                subject=subject,
                template_key="state_specific",
                status="sent",
                message_id=send_result.get("message_id", ""),
                batch_id=f"auto-optout-{int(time.time())}",
            )
            result["success"] = True
            result["message"] = f"Email sent to {privacy_email}"
        else:
            _record_email_request(
                profile_id=profile_id,
                broker_id=broker.get("id", ""),
                to_email=privacy_email,
                subject=subject,
                template_key="state_specific",
                status="failed",
                response_text=send_result["message"],
                batch_id=f"auto-optout-{int(time.time())}",
            )
            result["message"] = f"Send failed: {send_result['message']}"

        return result


# ---------------------------------------------------------------------------
# Batch Operations
# ---------------------------------------------------------------------------

def batch_update_status(
    optout_ids: list[int],
    status: str,
    notes: str = "",
) -> dict:
    """
    Update multiple opt-out records to the same status.

    Args:
        optout_ids: List of opt-out record IDs.
        status: Target status.
        notes: Optional notes.

    Returns:
        {"updated": count, "failed": count, "errors": [...]}
    """
    manager = OptOutManager()
    results = {"updated": 0, "failed": 0, "errors": []}

    for oid in optout_ids:
        try:
            if manager.update_status(oid, status, notes):
                results["updated"] += 1
            else:
                results["failed"] += 1
                results["errors"].append(f"Opt-out #{oid}: record not found")
        except ValueError as e:
            results["failed"] += 1
            results["errors"].append(f"Opt-out #{oid}: {e}")
        except Exception as e:
            results["failed"] += 1
            results["errors"].append(f"Opt-out #{oid}: unexpected error — {e}")

    return results


def auto_submit_optouts(
    profile_id: int,
    broker_ids: list[str] | None = None,
) -> dict:
    """
    Attempt automated opt-out submissions for all eligible brokers.

    Uses EmailAutoSubmitter for email-only/mixed brokers, and the original
    AutoSubmitter for form-based brokers with auto_removable=True.

    Args:
        profile_id: Profile to submit opt-outs for.
        broker_ids: Limit to specific brokers (default: all eligible).

    Returns:
        Summary dict with success/failure counts.
    """
    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found", "submitted": 0, "failed": 0, "skipped": 0, "drafts": 0}

    optouts = get_optouts(profile_id, status="pending")
    if broker_ids:
        optouts = [o for o in optouts if o.get("broker_id") in broker_ids]

    form_submitter = AutoSubmitter()
    email_submitter = EmailAutoSubmitter()
    manager = OptOutManager()
    summary = {"submitted": 0, "failed": 0, "skipped": 0, "drafts": 0, "results": []}

    for optout in optouts:
        broker = manager._get_broker(optout["broker_id"])
        if not broker or not broker.get("auto_removable", False):
            summary["skipped"] += 1
            continue

        process_type = broker.get("process_type", "")

        if process_type in ("email-only", "mixed"):
            # Use email-based submission
            result = email_submitter.attempt_email_submission(
                broker=broker,
                profile=profile,
                profile_id=profile_id,
            )
        else:
            # Use form-based submission
            result = form_submitter.attempt_submission(
                broker=broker,
                profile=profile,
                listing_url=optout.get("notes", "").replace("Listing URL: ", ""),
            )

        if result["success"]:
            now = datetime.now(timezone.utc).isoformat()
            if result.get("draft"):
                summary["drafts"] += 1
                manager.update_status(
                    optout["id"], "submitted",
                    f"Draft saved (SMTP not configured): {result['message']}",
                )
            else:
                manager.update_status(
                    optout["id"], "submitted",
                    f"Auto-submitted: {result['message']}",
                )
            summary["submitted"] += 1
        else:
            summary["failed"] += 1

        summary["results"].append({
            "broker_id": optout["broker_id"],
            "broker_name": optout.get("broker_name", ""),
            **result,
        })

        # Rate limit: 3 second delay between emails
        time.sleep(3)

    log_activity(
        None, profile_id, "auto_submit_batch", "optout",
        f"Auto-submit: {summary['submitted']} submitted, "
        f"{summary['failed']} failed, {summary['skipped']} skipped, "
        f"{summary['drafts']} saved as drafts",
    )

    return summary


def auto_submit_single_optout(optout_id: int) -> dict:
    """
    Auto-submit a single opt-out request.

    Args:
        optout_id: The opt-out record ID.

    Returns:
        Result dict with success/failure info.
    """
    from models import db_session

    # Look up the optout record
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM optout_status WHERE id = ?", (optout_id,)
        ).fetchone()
        if not row:
            return {"error": "Opt-out record not found", "success": False}
        optout = dict(row)

    profile = get_profile(optout["profile_id"])
    if not profile:
        return {"error": "Profile not found", "success": False}

    manager = OptOutManager()
    broker = manager._get_broker(optout["broker_id"])
    if not broker:
        return {"error": "Broker not found", "success": False}

    if not broker.get("auto_removable", False):
        return {"error": "Broker does not support auto-removal", "success": False}

    process_type = broker.get("process_type", "")

    if process_type in ("email-only", "mixed"):
        email_submitter = EmailAutoSubmitter()
        result = email_submitter.attempt_email_submission(
            broker=broker,
            profile=profile,
            profile_id=optout["profile_id"],
        )
    else:
        form_submitter = AutoSubmitter()
        result = form_submitter.attempt_submission(
            broker=broker,
            profile=profile,
            listing_url=optout.get("notes", "").replace("Listing URL: ", ""),
        )

    if result["success"]:
        manager.update_status(
            optout_id, "submitted",
            f"Auto-submitted: {result['message']}",
        )
        log_activity(
            None, optout["profile_id"], "auto_submit_single", "optout",
            f"Auto-submitted opt-out for {broker.get('name', optout['broker_id'])}",
            {"optout_id": optout_id, "broker_id": optout["broker_id"]},
        )

    return {
        "success": result["success"],
        "broker_name": broker.get("name", optout["broker_id"]),
        "message": result["message"],
        "draft": result.get("draft", False),
    }


# ---------------------------------------------------------------------------
# Credit Freeze Quick Links
# ---------------------------------------------------------------------------

CREDIT_FREEZE_LINKS = {
    "equifax": {
        "name": "Equifax",
        "freeze_url": "https://www.equifax.com/personal/credit-report-services/credit-freeze/",
        "phone": "1-800-349-9960",
    },
    "experian": {
        "name": "Experian",
        "freeze_url": "https://www.experian.com/freeze/center.html",
        "phone": "1-888-397-3742",
    },
    "transunion": {
        "name": "TransUnion",
        "freeze_url": "https://www.transunion.com/credit-freeze",
        "phone": "1-888-909-8872",
    },
    "innovis": {
        "name": "Innovis",
        "freeze_url": "https://www.innovis.com/personal/securityFreeze",
        "phone": "1-800-540-2505",
    },
    "chexsystems": {
        "name": "ChexSystems",
        "freeze_url": "https://www.chexsystems.com/security-freeze/place-freeze",
        "phone": "1-800-428-9623",
    },
    "nctue": {
        "name": "NCTUE (National Consumer Telecom & Utilities Exchange)",
        "freeze_url": "https://www.nctue.com/consumers",
        "phone": "1-866-349-5185",
    },
}

OPT_OUT_PRESCREEN = {
    "url": "https://www.optoutprescreen.com/",
    "phone": "1-888-567-8688",
    "description": "Stop pre-approved credit card and insurance offers",
}


def get_credit_freeze_links() -> dict:
    """Return all credit freeze quick links."""
    return CREDIT_FREEZE_LINKS


def get_optout_prescreen() -> dict:
    """Return OptOutPrescreen info."""
    return OPT_OUT_PRESCREEN
