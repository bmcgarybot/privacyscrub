"""
PrivacyScrub — Email Sender Module

Connects to user-configured SMTP (Gmail App Password, custom SMTP, or SendGrid)
and auto-generates GDPR/CCPA/generic removal request emails for data brokers.

Features:
    - Rate limiting: 250 emails/day max, 2-second delay between sends
    - Dry-run mode (preview without sending)
    - Templates: GDPR, CCPA, CPRA, generic US, Arizona-specific
    - Personalized with profile data (name, address, email, phone)
    - Tracks which emails were sent, to whom, when
    - Family member batch sending support
"""

import json
import logging
import smtplib
import time
import uuid
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from string import Template
from typing import Optional

from models import (
    db_session, get_profile, get_family_members,
    get_setting, set_setting, log_activity,
)

logger = logging.getLogger("privacyscrub.email")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DAILY_SEND_LIMIT = 250
SEND_DELAY_SECONDS = 2.0
BATCH_SIZE = 25  # process in batches for progress tracking

# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

TEMPLATES = {
    "gdpr": {
        "name": "GDPR Erasure Request (EU)",
        "subject": "Right to Erasure Request — $full_name (GDPR Article 17)",
        "body": """Dear Data Protection Officer,

I am writing to exercise my right to erasure ("right to be forgotten") as provided under Article 17 of the General Data Protection Regulation (EU) 2016/679.

I request that you erase the following personal data you hold about me without undue delay:

Full Name: $full_name
Email Address: $email
Phone Number: $phone
Address: $address
Date of Birth: $dob

I request that you erase ALL personal data you hold about me, including but not limited to my name, contact information, address history, relatives, and any other personally identifiable information.

Under Article 17(1), you are required to erase personal data without undue delay where:
(a) The personal data is no longer necessary for the purpose it was collected;
(b) I withdraw my consent on which the processing is based;
(d) The personal data has been unlawfully processed.

I also request that you inform any third parties to whom my data has been disclosed about this erasure request, as required under Article 17(2).

Please confirm completion of this erasure within 30 days as required by Article 12(3). If you are unable to comply, please provide a written explanation of the legal basis for retaining my data.

If I do not receive a satisfactory response within 30 days, I reserve the right to lodge a complaint with the relevant supervisory authority.

Sincerely,
$full_name
$email
$address

Date: $date""",
    },
    "ccpa": {
        "name": "CCPA Deletion Request (California)",
        "subject": "Request to Delete Personal Information — $full_name (CCPA §1798.105)",
        "body": """Dear Privacy Team,

Pursuant to the California Consumer Privacy Act (CCPA), California Civil Code §1798.105, I am a California consumer and I request the deletion of any and all personal information your business has collected about me.

My identifying information:

Full Name: $full_name
Email Address: $email
Phone Number: $phone
Address: $address

Under the CCPA, I have the right to request that a business delete any personal information about me which the business has collected from me. Upon receiving a verifiable consumer request from me, you are obligated to delete my personal information from your records and direct any service providers to delete my personal information from their records.

You are required to respond to this request within 45 days. If additional time is needed, you must notify me within 45 days and may extend the period by an additional 45 days.

Please confirm deletion of my personal information by responding to this email at $email.

I also request that you do not sell or share my personal information going forward, as is my right under §1798.120.

Sincerely,
$full_name
$email

Date: $date""",
    },
    "cpra": {
        "name": "CPRA Enhanced Request (California)",
        "subject": "Right to Delete and Correct — $full_name (CPRA Enhanced Rights)",
        "body": """Dear Privacy Team,

Under the California Privacy Rights Act (CPRA), which amends and extends the CCPA, I am exercising my enhanced rights as a California consumer.

I request the following:

1. DELETION of all personal information you have collected about me (§1798.105)
2. RESTRICTION of processing of my sensitive personal information (§1798.121)
3. OPT-OUT of any sale or sharing of my personal information (§1798.120)

My identifying information:

Full Name: $full_name
Email Address: $email
Phone Number: $phone
Address: $address
Date of Birth: $dob

Under the CPRA, you must:
- Respond within 45 business days
- Delete my information from all systems and direct service providers to do the same
- Cease any automated decision-making that uses my personal data
- Not retaliate against me for exercising these rights

If you deny any part of this request, please provide a detailed explanation citing the specific legal exception.

Sincerely,
$full_name
$email

Date: $date""",
    },
    "generic_us": {
        "name": "Generic US Removal Request",
        "subject": "Data Removal Request — $full_name",
        "body": """Dear Privacy Team,

I am writing to formally request the removal of my personal information from your database and any associated services.

My personal information that I am requesting be removed:

Full Name: $full_name
Email Address: $email
Phone Number: $phone
Address: $address

I request that you:
1. Remove all personal information you hold about me from your databases
2. Remove my information from any public-facing search results or profiles
3. Cease sharing, selling, or distributing my personal information to any third parties
4. Confirm the deletion of my data by responding to this email

Under various federal and state privacy laws, I have the right to request the removal of my personal information. Many states including California, Virginia, Colorado, Connecticut, Utah, and others have enacted comprehensive privacy legislation protecting consumer data rights.

Please process this request promptly and confirm completion. I expect a response within 30 days.

Sincerely,
$full_name
$email

Date: $date""",
    },
    "arizona": {
        "name": "Arizona Data Privacy Request",
        "subject": "Data Removal Request — $full_name (Arizona Resident)",
        "body": """Dear Privacy Team,

I am a resident of the State of Arizona and I am formally requesting the deletion and removal of my personal information from your database and any associated services.

My identifying information:

Full Name: $full_name
Email Address: $email
Phone Number: $phone
Address: $address
State of Residence: Arizona

Under Arizona's consumer data privacy protections and the growing body of state privacy legislation, I request that you:

1. Delete all personal data you hold about me
2. Remove my information from any publicly accessible search results, profiles, or listings
3. Direct any processors or service providers to delete my data
4. Cease any further collection, sale, or sharing of my personal information
5. Confirm the deletion by responding to this email

Arizona law (A.R.S. §44-1694 et seq.) provides protections for consumer data. Additionally, many data brokers are subject to applicable federal regulations including the Fair Credit Reporting Act (FCRA).

I expect a response and confirmation of data deletion within 30 days of receiving this request. Failure to comply may result in further legal action.

Sincerely,
$full_name
$email
Arizona Resident

Date: $date""",
    },
}


# ---------------------------------------------------------------------------
# SMTP configuration helpers
# ---------------------------------------------------------------------------

def get_email_config() -> Optional[dict]:
    """
    Retrieve the saved SMTP configuration from settings.

    Returns:
        dict with smtp_host, smtp_port, smtp_user, smtp_pass, from_email, from_name
        or None if not configured.
    """
    config_json = get_setting("email_smtp_config", "")
    if not config_json:
        return None
    try:
        config = json.loads(config_json)
        # Validate required fields
        if not config.get("smtp_host") or not config.get("smtp_user"):
            return None
        return config
    except (json.JSONDecodeError, TypeError):
        return None


def save_email_config(config: dict) -> None:
    """
    Save SMTP configuration to settings.

    Args:
        config: Dict with smtp_host, smtp_port, smtp_user, smtp_pass, from_email, from_name
    """
    set_setting(
        "email_smtp_config",
        json.dumps(config),
        category="notification",
        description="SMTP configuration for email sender",
    )


def test_smtp_connection(config: dict) -> dict:
    """
    Test SMTP connection with the given config.

    Returns:
        {"success": True/False, "message": "..."}
    """
    try:
        host = config.get("smtp_host", "")
        port = int(config.get("smtp_port", 587))
        user = config.get("smtp_user", "")
        password = config.get("smtp_pass", "")

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(user, password)
        server.quit()

        return {"success": True, "message": "SMTP connection successful"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "message": "Authentication failed. Check username/password."}
    except smtplib.SMTPConnectError as e:
        return {"success": False, "message": f"Could not connect to SMTP server: {e}"}
    except Exception as e:
        return {"success": False, "message": f"Connection error: {e}"}


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def render_template(template_key: str, profile: dict, broker: Optional[dict] = None) -> dict:
    """
    Render an email template with profile data.

    Args:
        template_key: One of the TEMPLATES keys (gdpr, ccpa, cpra, generic_us, arizona)
        profile: Profile dict with first_name, last_name, email, phone, etc.
        broker: Optional broker dict for the recipient

    Returns:
        {"subject": "...", "body": "...", "template_name": "..."}
    """
    template_data = TEMPLATES.get(template_key)
    if not template_data:
        return {"error": f"Unknown template: {template_key}"}

    full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    addresses = profile.get("addresses", "[]")
    if isinstance(addresses, str):
        try:
            addresses = json.loads(addresses)
        except (json.JSONDecodeError, TypeError):
            addresses = [addresses] if addresses else []
    address = addresses[0] if addresses else ""

    # Build location string
    city = profile.get("city", "")
    state = profile.get("state", "")
    zip_code = profile.get("zip_code", "")
    if city or state:
        location = f"{city}, {state} {zip_code}".strip().rstrip(",")
        if address:
            address = f"{address}, {location}"
        else:
            address = location

    vars_dict = {
        "full_name": full_name,
        "first_name": profile.get("first_name", ""),
        "last_name": profile.get("last_name", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "address": address,
        "dob": profile.get("date_of_birth", ""),
        "date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
    }

    subject = Template(template_data["subject"]).safe_substitute(vars_dict)
    body = Template(template_data["body"]).safe_substitute(vars_dict)

    return {
        "subject": subject,
        "body": body,
        "template_name": template_data["name"],
        "template_key": template_key,
    }


def render_family_member_template(
    template_key: str, member: dict, parent_profile: dict
) -> dict:
    """
    Render an email template for a family member.

    Uses family member's data merged with parent profile address info.
    """
    # Build a pseudo-profile from the family member data
    pseudo_profile = {
        "first_name": member.get("first_name", ""),
        "last_name": member.get("last_name", parent_profile.get("last_name", "")),
        "email": member.get("email", parent_profile.get("email", "")),
        "phone": member.get("phone", parent_profile.get("phone", "")),
        "addresses": parent_profile.get("addresses", "[]"),
        "city": parent_profile.get("city", ""),
        "state": parent_profile.get("state", ""),
        "zip_code": parent_profile.get("zip_code", ""),
        "date_of_birth": member.get("date_of_birth", ""),
    }

    return render_template(template_key, pseudo_profile)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def get_daily_send_count() -> int:
    """Get the number of emails sent today."""
    with db_session() as conn:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM email_requests WHERE sent_at LIKE ? AND status != 'pending'",
            (f"{today}%",),
        ).fetchone()
        return row["cnt"] if row else 0


def can_send_more() -> tuple[bool, int]:
    """
    Check if more emails can be sent today.

    Returns:
        (can_send: bool, remaining: int)
    """
    sent = get_daily_send_count()
    remaining = max(0, DAILY_SEND_LIMIT - sent)
    return remaining > 0, remaining


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_single_email(
    config: dict,
    to_email: str,
    subject: str,
    body: str,
    from_email: str,
    from_name: str = "PrivacyScrub",
) -> dict:
    """
    Send a single email via SMTP.

    Args:
        config: SMTP configuration dict
        to_email: Recipient email address
        subject: Email subject line
        body: Plain text email body
        from_email: Sender email address
        from_name: Sender display name

    Returns:
        {"success": True/False, "message": "...", "message_id": "..."}
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = to_email
        msg["Reply-To"] = from_email

        # Generate a message ID for tracking
        message_id = f"<{uuid.uuid4().hex}@privacyscrub>"
        msg["Message-ID"] = message_id

        # Attach plain text body
        msg.attach(MIMEText(body, "plain", "utf-8"))

        host = config.get("smtp_host", "")
        port = int(config.get("smtp_port", 587))
        user = config.get("smtp_user", "")
        password = config.get("smtp_pass", "")

        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()

        server.login(user, password)
        server.send_message(msg)
        server.quit()

        return {
            "success": True,
            "message": "Email sent successfully",
            "message_id": message_id,
        }

    except smtplib.SMTPRecipientsRefused:
        return {"success": False, "message": f"Recipient refused: {to_email}"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "message": "SMTP authentication failed"}
    except smtplib.SMTPException as e:
        return {"success": False, "message": f"SMTP error: {e}"}
    except Exception as e:
        return {"success": False, "message": f"Send error: {e}"}


# ---------------------------------------------------------------------------
# Batch sending
# ---------------------------------------------------------------------------

def send_batch(
    profile_id: int,
    broker_ids: list[str],
    template_key: str = "generic_us",
    dry_run: bool = False,
    include_family: bool = False,
    priority_filter: Optional[str] = None,
) -> dict:
    """
    Send removal request emails to a batch of brokers.

    Args:
        profile_id: Profile to send on behalf of
        broker_ids: List of broker IDs to email (or empty for all)
        template_key: Which email template to use
        dry_run: If True, generate emails but don't actually send
        include_family: If True, also send for all family members
        priority_filter: If set, only send to brokers with this priority

    Returns:
        {"sent": int, "failed": int, "skipped": int, "dry_run": bool,
         "remaining_today": int, "details": [...]}
    """
    from scanner import load_brokers

    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found"}

    config = get_email_config()
    if not config and not dry_run:
        return {"error": "SMTP not configured. Set up email settings first."}

    # Load brokers
    all_brokers = load_brokers()
    broker_map = {b["id"]: b for b in all_brokers}

    # Filter to requested brokers
    if broker_ids:
        target_brokers = [broker_map[bid] for bid in broker_ids if bid in broker_map]
    else:
        target_brokers = all_brokers

    # Apply priority filter
    if priority_filter:
        target_brokers = [b for b in target_brokers if b.get("priority") == priority_filter]

    # Only email brokers that have a privacy_email
    target_brokers = [b for b in target_brokers if b.get("privacy_email")]

    # Build list of (person_profile, broker) pairs
    send_pairs = []
    for broker in target_brokers:
        send_pairs.append((profile, broker, None))  # None = main profile

    if include_family:
        family = get_family_members(profile_id)
        for member in family:
            for broker in target_brokers:
                send_pairs.append((profile, broker, member))

    # Check rate limits
    can_send, remaining = can_send_more()
    if not can_send and not dry_run:
        return {
            "error": "Daily email limit reached (250/day). Try again tomorrow.",
            "remaining_today": 0,
        }

    results = {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": dry_run,
        "remaining_today": remaining,
        "details": [],
        "batch_id": f"batch-{uuid.uuid4().hex[:12]}",
    }

    for parent_profile, broker, family_member in send_pairs:
        # Rate limit check
        if not dry_run:
            can_send, remaining = can_send_more()
            if not can_send:
                results["skipped"] += 1
                results["details"].append({
                    "broker_id": broker["id"],
                    "status": "skipped",
                    "reason": "Daily limit reached",
                })
                continue

        # Check if already sent recently (within 30 days)
        already_sent = _check_recent_send(
            profile_id,
            broker["id"],
            family_member["id"] if family_member else None,
        )
        if already_sent and not dry_run:
            results["skipped"] += 1
            results["details"].append({
                "broker_id": broker["id"],
                "status": "skipped",
                "reason": "Already sent within 30 days",
            })
            continue

        # Render template
        if family_member:
            rendered = render_family_member_template(template_key, family_member, parent_profile)
            person_name = f"{family_member['first_name']} {family_member.get('last_name', parent_profile.get('last_name', ''))}"
        else:
            rendered = render_template(template_key, parent_profile, broker)
            person_name = f"{parent_profile['first_name']} {parent_profile['last_name']}"

        if "error" in rendered:
            results["failed"] += 1
            results["details"].append({
                "broker_id": broker["id"],
                "status": "failed",
                "reason": rendered["error"],
            })
            continue

        to_email = broker.get("privacy_email", "")
        if not to_email:
            results["skipped"] += 1
            continue

        if dry_run:
            # Record as pending (dry run preview)
            _record_email_request(
                profile_id=profile_id,
                broker_id=broker["id"],
                to_email=to_email,
                subject=rendered["subject"],
                template_key=template_key,
                status="preview",
                family_member_id=family_member["id"] if family_member else None,
                batch_id=results["batch_id"],
            )
            results["sent"] += 1
            results["details"].append({
                "broker_id": broker["id"],
                "broker_name": broker["name"],
                "to_email": to_email,
                "subject": rendered["subject"],
                "person": person_name,
                "status": "preview",
                "body_preview": rendered["body"][:200] + "...",
            })
        else:
            # Actually send
            from_email = config.get("from_email", config.get("smtp_user", ""))
            from_name = config.get("from_name", "PrivacyScrub")

            send_result = send_single_email(
                config, to_email, rendered["subject"], rendered["body"],
                from_email, from_name,
            )

            if send_result["success"]:
                _record_email_request(
                    profile_id=profile_id,
                    broker_id=broker["id"],
                    to_email=to_email,
                    subject=rendered["subject"],
                    template_key=template_key,
                    status="sent",
                    message_id=send_result.get("message_id", ""),
                    family_member_id=family_member["id"] if family_member else None,
                    batch_id=results["batch_id"],
                )
                results["sent"] += 1
                results["details"].append({
                    "broker_id": broker["id"],
                    "broker_name": broker["name"],
                    "to_email": to_email,
                    "status": "sent",
                    "person": person_name,
                })
                log_activity(
                    None, profile_id, "email_sent", "optout",
                    f"Removal email sent to {broker['name']} ({to_email}) for {person_name}",
                )
            else:
                _record_email_request(
                    profile_id=profile_id,
                    broker_id=broker["id"],
                    to_email=to_email,
                    subject=rendered["subject"],
                    template_key=template_key,
                    status="failed",
                    response_text=send_result["message"],
                    family_member_id=family_member["id"] if family_member else None,
                    batch_id=results["batch_id"],
                )
                results["failed"] += 1
                results["details"].append({
                    "broker_id": broker["id"],
                    "broker_name": broker["name"],
                    "to_email": to_email,
                    "status": "failed",
                    "reason": send_result["message"],
                    "person": person_name,
                })

            # Rate limiting delay
            time.sleep(SEND_DELAY_SECONDS)

    results["remaining_today"] = max(0, DAILY_SEND_LIMIT - get_daily_send_count())

    # Log batch activity
    log_activity(
        None, profile_id, "email_batch_completed", "optout",
        f"Email batch complete: {results['sent']} sent, {results['failed']} failed, {results['skipped']} skipped (dry_run={dry_run})",
    )

    return results


# ---------------------------------------------------------------------------
# Database helpers for email_requests table
# ---------------------------------------------------------------------------

def _record_email_request(
    profile_id: int,
    broker_id: str,
    to_email: str,
    subject: str,
    template_key: str,
    status: str = "pending",
    message_id: str = "",
    response_text: str = "",
    family_member_id: Optional[int] = None,
    batch_id: str = "",
) -> int:
    """Record an email request in the database."""
    with db_session() as conn:
        now = datetime.now(timezone.utc).isoformat()
        # Calculate follow-up date (30 days from now for initial, 90 for re-sends)
        follow_up = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        cursor = conn.execute(
            """INSERT INTO email_requests
               (profile_id, broker_id, to_email, subject, template_key,
                status, message_id, response_text, family_member_id,
                batch_id, sent_at, follow_up_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile_id, broker_id, to_email, subject, template_key,
                status, message_id, response_text, family_member_id,
                batch_id, now, follow_up,
            ),
        )
        return cursor.lastrowid


def _check_recent_send(
    profile_id: int, broker_id: str, family_member_id: Optional[int] = None
) -> bool:
    """Check if an email was sent to this broker for this profile in the last 30 days."""
    with db_session() as conn:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

        if family_member_id:
            row = conn.execute(
                """SELECT id FROM email_requests
                   WHERE profile_id = ? AND broker_id = ? AND family_member_id = ?
                   AND sent_at > ? AND status IN ('sent', 'delivered')""",
                (profile_id, broker_id, family_member_id, cutoff),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id FROM email_requests
                   WHERE profile_id = ? AND broker_id = ? AND family_member_id IS NULL
                   AND sent_at > ? AND status IN ('sent', 'delivered')""",
                (profile_id, broker_id, cutoff),
            ).fetchone()

        return row is not None


def get_email_requests(
    profile_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Get email request records with optional filters."""
    with db_session() as conn:
        query = "SELECT * FROM email_requests WHERE 1=1"
        params = []

        if profile_id is not None:
            query += " AND profile_id = ?"
            params.append(profile_id)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY sent_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_email_summary(profile_id: Optional[int] = None) -> dict:
    """
    Get a summary of email request statuses.

    Returns:
        {"total": int, "sent": int, "delivered": int, "replied": int,
         "action_needed": int, "completed": int, "failed": int, "pending": int}
    """
    with db_session() as conn:
        where = ""
        params = []
        if profile_id is not None:
            where = " WHERE profile_id = ?"
            params = [profile_id]

        rows = conn.execute(
            f"SELECT status, COUNT(*) as cnt FROM email_requests{where} GROUP BY status",
            params,
        ).fetchall()

        summary = {
            "total": 0,
            "pending": 0,
            "preview": 0,
            "sent": 0,
            "delivered": 0,
            "replied": 0,
            "action_needed": 0,
            "completed": 0,
            "failed": 0,
        }

        for row in rows:
            status = row["status"]
            count = row["cnt"]
            summary["total"] += count
            if status in summary:
                summary[status] = count

        return summary


def update_email_request_status(
    request_id: int,
    status: str,
    response_text: str = "",
) -> bool:
    """Update the status of an email request."""
    with db_session() as conn:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute(
            """UPDATE email_requests
               SET status = ?, response_text = ?, updated_at = ?
               WHERE id = ?""",
            (status, response_text, now, request_id),
        )
        return cursor.rowcount > 0


def get_pending_followups() -> list[dict]:
    """Get email requests that are past their follow-up date and need re-sending."""
    with db_session() as conn:
        now = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            """SELECT * FROM email_requests
               WHERE follow_up_date < ? AND status IN ('sent', 'delivered')
               ORDER BY follow_up_date ASC""",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_email_request(request_id: int) -> Optional[dict]:
    """Get a single email request by ID."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM email_requests WHERE id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None


def get_available_templates() -> list[dict]:
    """Return metadata about available email templates."""
    return [
        {"key": key, "name": data["name"]}
        for key, data in TEMPLATES.items()
    ]
