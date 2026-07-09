"""
PrivacyScrub — Breach Monitoring (Have I Been Pwned Integration)

Features:
    - Email breach lookup via HIBP v3 API
    - Password compromise check using k-anonymity (safe, no full password sent)
    - Breach detail parsing and severity classification
    - Batch email checking for profile + family members
    - Results stored to database for history/reporting

k-Anonymity Password Check:
    1. SHA-1 hash the password
    2. Send only the first 5 characters to HIBP
    3. HIBP returns all suffixes matching that prefix
    4. Check locally if your full hash is in the list
    → Your password never leaves your machine

API Key:
    Email breach lookups require an HIBP API key (paid, ~$3.50/month).
    Password checks are free and don't require a key.
    Set the key in Settings → hibp_api_key.
"""

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from models import (
    save_breach, get_breaches, get_breach_count, get_profile,
    get_family_members, log_activity, get_setting, set_setting,
    get_all_profiles,
)

logger = logging.getLogger("privacyscrub.breach")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HIBP_API_BASE = "https://haveibeenpwned.com/api/v3"
HIBP_PASSWORD_API = "https://api.pwnedpasswords.com/range"
HIBP_USER_AGENT = "PrivacyScrub-BreachMonitor"
RATE_LIMIT_DELAY = 1.5  # seconds between HIBP API calls


# ---------------------------------------------------------------------------
# HIBP API Client
# ---------------------------------------------------------------------------

class HIBPClient:
    """
    Client for the Have I Been Pwned API.

    Handles authentication, rate limiting, and response parsing
    for both breach lookups and password checks.
    """

    def __init__(self, api_key: str | None = None):
        """
        Args:
            api_key: HIBP API key. If None, reads from settings.
        """
        self.api_key = api_key or get_setting("hibp_api_key", "")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": HIBP_USER_AGENT,
        })
        if self.api_key:
            self.session.headers["hibp-api-key"] = self.api_key
        self._last_request_time = 0

    def _rate_limit(self) -> None:
        """Enforce rate limiting between API calls."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    # ----- Email Breach Lookup -----

    def check_email(self, email: str, truncate: bool = False) -> list[dict]:
        """
        Check if an email has been involved in any known breaches.

        Args:
            email: Email address to check.
            truncate: If True, returns truncated breach data (name only).

        Returns:
            List of breach dicts with details:
            [
                {
                    "Name": "Adobe",
                    "Title": "Adobe",
                    "Domain": "adobe.com",
                    "BreachDate": "2013-10-04",
                    "Description": "...",
                    "DataClasses": ["Email addresses", "Passwords"],
                    "IsVerified": true,
                    "IsSensitive": false,
                    "PwnCount": 152445165,
                }
            ]

        Raises:
            HIBPError: On API error (non-404).
        """
        if not self.api_key:
            logger.warning("HIBP API key not set — email breach check unavailable")
            return []

        self._rate_limit()

        url = f"{HIBP_API_BASE}/breachedaccount/{requests.utils.quote(email)}"
        params = {"truncateResponse": "true"} if truncate else {}

        try:
            response = self.session.get(url, params=params, timeout=15)

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                # Not found in any breaches — good!
                return []
            elif response.status_code == 401:
                logger.error("HIBP API key is invalid or expired")
                raise HIBPError("Invalid API key", response.status_code)
            elif response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 5))
                logger.warning("HIBP rate limited — retrying after %ds", retry_after)
                time.sleep(retry_after)
                return self.check_email(email, truncate)
            else:
                logger.error("HIBP API error: HTTP %d", response.status_code)
                raise HIBPError(
                    f"HIBP API returned HTTP {response.status_code}",
                    response.status_code,
                )

        except requests.RequestException as e:
            logger.error("HIBP request failed: %s", e)
            raise HIBPError(f"Network error: {e}")

    def get_breach_details(self, breach_name: str) -> Optional[dict]:
        """
        Get detailed information about a specific breach.

        Args:
            breach_name: The breach name (e.g., "Adobe").

        Returns:
            Breach detail dict or None if not found.
        """
        self._rate_limit()

        url = f"{HIBP_API_BASE}/breach/{requests.utils.quote(breach_name)}"

        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return None
            else:
                logger.error("HIBP breach detail error: HTTP %d", response.status_code)
                return None
        except requests.RequestException as e:
            logger.error("HIBP breach detail request failed: %s", e)
            return None

    def get_all_breaches(self) -> list[dict]:
        """
        Get a list of all breaches in the HIBP database.

        Returns:
            List of all breach dicts.
        """
        self._rate_limit()

        url = f"{HIBP_API_BASE}/breaches"

        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                return response.json()
            return []
        except requests.RequestException as e:
            logger.error("HIBP all-breaches request failed: %s", e)
            return []

    # ----- Password Check (k-Anonymity) -----

    def check_password(self, password: str) -> dict:
        """
        Check if a password has appeared in known breaches using k-anonymity.

        The password is SHA-1 hashed locally. Only the first 5 characters
        of the hash are sent to HIBP. The full password never leaves
        this machine.

        Args:
            password: The password to check.

        Returns:
            {
                "compromised": bool,
                "count": int,       # number of times seen in breaches
                "message": str,     # human-readable result
            }
        """
        # SHA-1 hash the password
        sha1_hash = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix = sha1_hash[:5]
        suffix = sha1_hash[5:]

        self._rate_limit()

        try:
            response = self.session.get(
                f"{HIBP_PASSWORD_API}/{prefix}",
                timeout=10,
            )

            if response.status_code != 200:
                logger.error("HIBP password API error: HTTP %d", response.status_code)
                return {
                    "compromised": False,
                    "count": 0,
                    "message": f"Unable to check (API returned HTTP {response.status_code})",
                }

            # Parse response — format: SUFFIX:COUNT\r\n
            for line in response.text.splitlines():
                parts = line.strip().split(":")
                if len(parts) == 2 and parts[0].upper() == suffix:
                    count = int(parts[1])
                    return {
                        "compromised": True,
                        "count": count,
                        "message": f"⚠️ This password has been seen {count:,} times in data breaches. "
                                   f"Do NOT use this password.",
                    }

            return {
                "compromised": False,
                "count": 0,
                "message": "✅ This password has not been found in any known breaches.",
            }

        except requests.RequestException as e:
            logger.error("HIBP password check failed: %s", e)
            return {
                "compromised": False,
                "count": 0,
                "message": f"Unable to check password: {e}",
            }

    # ----- Paste Lookup -----

    def check_pastes(self, email: str) -> list[dict]:
        """
        Check if an email has been found in any pastes (Pastebin, etc.).

        Args:
            email: Email address to check.

        Returns:
            List of paste dicts.
        """
        if not self.api_key:
            return []

        self._rate_limit()

        url = f"{HIBP_API_BASE}/pasteaccount/{requests.utils.quote(email)}"

        try:
            response = self.session.get(url, timeout=15)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                return []
            else:
                logger.error("HIBP paste check error: HTTP %d", response.status_code)
                return []
        except requests.RequestException as e:
            logger.error("HIBP paste check failed: %s", e)
            return []


class HIBPError(Exception):
    """Exception for HIBP API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Severity Classification
# ---------------------------------------------------------------------------

def classify_severity(breach: dict) -> str:
    """
    Classify the severity of a breach based on compromised data types.

    Args:
        breach: HIBP breach dict.

    Returns:
        Severity level: "low", "medium", "high", or "critical".
    """
    data_classes = set(dc.lower() for dc in breach.get("DataClasses", []))

    critical_types = {"passwords", "credit cards", "bank account numbers",
                      "social security numbers", "government issued ids"}
    high_types = {"phone numbers", "physical addresses", "dates of birth",
                  "ip addresses", "security questions and answers"}
    medium_types = {"email addresses", "usernames", "names",
                    "genders", "employers"}

    if data_classes & critical_types:
        return "critical"
    elif data_classes & high_types:
        return "high"
    elif data_classes & medium_types:
        return "medium"
    else:
        return "low"


# ---------------------------------------------------------------------------
# Profile Breach Scan — Orchestrator
# ---------------------------------------------------------------------------

def scan_profile_breaches(profile_id: int) -> dict:
    """
    Run a breach scan for a profile (and optionally family members).

    Checks the profile's email against HIBP and saves results to the database.

    Args:
        profile_id: Profile to scan.

    Returns:
        {
            "total_breaches": int,
            "new_breaches": int,
            "emails_checked": list[str],
            "breaches": list[dict],
            "error": str | None,
        }
    """
    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found", "total_breaches": 0, "new_breaches": 0,
                "emails_checked": [], "breaches": []}

    client = HIBPClient()
    results = {
        "total_breaches": 0,
        "new_breaches": 0,
        "emails_checked": [],
        "breaches": [],
        "error": None,
    }

    # Collect emails to check
    emails_to_check = []
    email = profile.get("email", "").strip()
    if email:
        emails_to_check.append(email)

    # Check family member emails too
    family = get_family_members(profile_id)
    for member in family:
        member_email = member.get("email", "").strip()
        if member_email and member_email not in emails_to_check:
            emails_to_check.append(member_email)

    if not emails_to_check:
        results["error"] = "No email addresses to check"
        return results

    results["emails_checked"] = emails_to_check

    for check_email in emails_to_check:
        try:
            breaches = client.check_email(check_email)

            for breach in breaches:
                severity = classify_severity(breach)

                breach_data = {
                    "profile_id": profile_id,
                    "breach_name": breach.get("Name", "Unknown"),
                    "breach_domain": breach.get("Domain", ""),
                    "breach_date": breach.get("BreachDate", ""),
                    "compromised_data": breach.get("DataClasses", []),
                    "description": breach.get("Description", ""),
                    "severity": severity,
                    "is_verified": 1 if breach.get("IsVerified") else 0,
                    "is_sensitive": 1 if breach.get("IsSensitive") else 0,
                    "pwned_count": breach.get("PwnCount", 0),
                    "source": "hibp",
                }

                breach_id = save_breach(breach_data)

                # save_breach returns existing ID if duplicate
                existing = get_breaches(profile_id)
                existing_names = {b["breach_name"] for b in existing}

                results["breaches"].append({
                    "id": breach_id,
                    "name": breach_data["breach_name"],
                    "domain": breach_data["breach_domain"],
                    "date": breach_data["breach_date"],
                    "severity": severity,
                    "data_classes": breach.get("DataClasses", []),
                    "is_new": breach_data["breach_name"] not in existing_names,
                })

        except HIBPError as e:
            logger.error("HIBP error for %s: %s", check_email, e)
            if "Invalid API key" in str(e):
                results["error"] = "HIBP API key is invalid or not set. Go to Settings to configure."
                break
        except Exception as e:
            logger.error("Breach scan error for %s: %s", check_email, e)

    results["total_breaches"] = len(results["breaches"])
    results["new_breaches"] = sum(1 for b in results["breaches"] if b.get("is_new"))

    log_activity(
        None, profile_id, "breach_scan_completed", "breach",
        f"Breach scan: {results['total_breaches']} breaches found "
        f"({results['new_breaches']} new) across {len(emails_to_check)} email(s)",
    )

    if results["new_breaches"] > 0:
        try:
            from webhooks import dispatch
            dispatch("breach.found", {
                "profile_id": profile_id,
                "total_breaches": results["total_breaches"],
                "new_breaches": results["new_breaches"],
                "breach_names": [
                    b.get("name", "") for b in results["breaches"]
                    if b.get("is_new")
                ][:20],
            })
        except Exception as e:
            logger.error("Webhook dispatch error: %s", e)

    return results


# ---------------------------------------------------------------------------
# Password Audit — Batch check
# ---------------------------------------------------------------------------

def check_passwords(passwords: list[str]) -> list[dict]:
    """
    Check multiple passwords against the HIBP Pwned Passwords database.

    Uses k-anonymity — passwords never leave the machine.

    Args:
        passwords: List of passwords to check.

    Returns:
        List of result dicts (same order as input):
        [
            {"password_hint": "pas***", "compromised": True, "count": 12345, "message": "..."},
        ]
    """
    client = HIBPClient()
    results = []

    for pw in passwords:
        result = client.check_password(pw)
        # Add a hint (first 3 chars + asterisks) — never store full password
        hint = pw[:3] + "*" * max(0, len(pw) - 3) if len(pw) >= 3 else "***"
        result["password_hint"] = hint
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Breach Summary for Dashboard
# ---------------------------------------------------------------------------

def get_breach_summary(profile_id: int) -> dict:
    """
    Get a breach summary suitable for dashboard display.

    Returns:
        {
            "total": int,
            "by_severity": {"critical": 1, "high": 3, ...},
            "most_recent": str | None,  # name of most recent breach
            "most_severe": str | None,  # name of most severe breach
            "compromised_types": list[str],  # all unique data types
        }
    """
    breaches = get_breaches(profile_id)

    if not breaches:
        return {
            "total": 0,
            "by_severity": {},
            "most_recent": None,
            "most_severe": None,
            "compromised_types": [],
        }

    by_severity = {}
    all_types = set()
    severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    most_severe = None
    max_severity = 0

    for b in breaches:
        sev = b.get("severity", "medium")
        by_severity[sev] = by_severity.get(sev, 0) + 1

        # Track most severe
        sev_score = severity_order.get(sev, 0)
        if sev_score > max_severity:
            max_severity = sev_score
            most_severe = b.get("breach_name")

        # Collect compromised data types
        comp = b.get("compromised_data", "[]")
        if isinstance(comp, str):
            try:
                comp = json.loads(comp)
            except (json.JSONDecodeError, TypeError):
                comp = []
        if isinstance(comp, list):
            all_types.update(comp)

    # Most recent by breach date
    dated = [b for b in breaches if b.get("breach_date")]
    most_recent = None
    if dated:
        most_recent = max(dated, key=lambda b: b["breach_date"]).get("breach_name")

    return {
        "total": len(breaches),
        "by_severity": by_severity,
        "most_recent": most_recent,
        "most_severe": most_severe,
        "compromised_types": sorted(all_types),
    }


# ---------------------------------------------------------------------------
# Recurring Breach Check Scheduler
# ---------------------------------------------------------------------------

BREACH_CHECK_INTERVAL = 7 * 24 * 60 * 60  # 7 days in seconds

_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop = threading.Event()


def _run_scheduled_breach_check() -> dict:
    """
    Run breach checks across ALL profiles + family members.
    Flags new breaches discovered since last check.

    Returns:
        Summary dict with results per profile.
    """
    last_check = get_setting("breach_last_check", "")
    now_iso = datetime.now(timezone.utc).isoformat()

    profiles = get_all_profiles()
    if not profiles:
        logger.info("Scheduled breach check: no profiles to scan")
        return {"profiles_checked": 0, "total_new": 0}

    total_new = 0

    for profile in profiles:
        pid = profile["id"]
        # Get existing breaches BEFORE scan
        existing_names = {b["breach_name"] for b in get_breaches(pid)}

        try:
            result = scan_profile_breaches(pid)
        except Exception as e:
            logger.error("Scheduled breach scan failed for profile %d: %s", pid, e)
            continue

        # Identify truly new breaches (not in DB before this scan)
        new_breaches = []
        for b in result.get("breaches", []):
            if b["name"] not in existing_names:
                new_breaches.append(b)

        if new_breaches:
            total_new += len(new_breaches)
            # Log prominently
            severity_counts = {}
            for nb in new_breaches:
                s = nb.get("severity", "medium")
                severity_counts[s] = severity_counts.get(s, 0) + 1

            severity_summary = ", ".join(f"{c} {s}" for s, c in sorted(severity_counts.items(),
                                         key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x[0], 4)))
            breach_names = ", ".join(nb["name"] for nb in new_breaches[:5])
            if len(new_breaches) > 5:
                breach_names += f" (+{len(new_breaches) - 5} more)"

            log_activity(
                None, pid, "breach_new_found", "breach",
                f"🚨 {len(new_breaches)} NEW breach(es) detected: {breach_names} ({severity_summary})",
                metadata={
                    "new_count": len(new_breaches),
                    "severity_counts": severity_counts,
                    "breach_names": [nb["name"] for nb in new_breaches],
                    "scheduled": True,
                },
            )

    # Update last check timestamp
    set_setting("breach_last_check", now_iso, "general", "Last scheduled breach check timestamp")

    log_activity(
        None, None, "breach_scheduled_check", "breach",
        f"Scheduled breach check complete: {len(profiles)} profile(s) scanned, {total_new} new breach(es) found",
    )

    logger.info("Scheduled breach check done: %d profiles, %d new breaches", len(profiles), total_new)
    return {"profiles_checked": len(profiles), "total_new": total_new}


def _scheduler_loop() -> None:
    """Background thread loop that runs breach checks weekly."""
    logger.info("Breach scheduler started (interval: %d seconds)", BREACH_CHECK_INTERVAL)

    while not _scheduler_stop.is_set():
        # Check if enough time has passed since last check
        last_check = get_setting("breach_last_check", "")
        should_run = True

        if last_check:
            try:
                last_dt = datetime.fromisoformat(last_check)
                elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if elapsed < BREACH_CHECK_INTERVAL:
                    should_run = False
                    wait_time = BREACH_CHECK_INTERVAL - elapsed
                    logger.debug("Breach check not due yet (%.1f hours until next)", wait_time / 3600)
            except (ValueError, TypeError):
                should_run = True

        if should_run:
            try:
                _run_scheduled_breach_check()
            except Exception as e:
                logger.exception("Scheduled breach check crashed: %s", e)

        # Sleep in small intervals so we can respond to stop event
        for _ in range(60):  # Check stop event every 60 seconds
            if _scheduler_stop.wait(60):
                return


def schedule_breach_checks() -> None:
    """
    Start the background breach check scheduler.
    Safe to call multiple times — only one thread will run.
    Call on app startup.
    """
    global _scheduler_thread

    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        logger.info("Breach scheduler already running")
        return

    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        name="breach-scheduler",
        daemon=True,
    )
    _scheduler_thread.start()
    logger.info("Breach check scheduler started (weekly interval)")


def stop_breach_scheduler() -> None:
    """Stop the background breach check scheduler."""
    global _scheduler_thread
    _scheduler_stop.set()
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=5)
        _scheduler_thread = None
    logger.info("Breach scheduler stopped")


def get_last_breach_check() -> Optional[str]:
    """Return the ISO timestamp of the last scheduled breach check, or None."""
    val = get_setting("breach_last_check", "")
    return val if val else None
