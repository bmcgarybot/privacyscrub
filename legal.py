"""
PrivacyScrub — Legal Document Generators

Generates privacy-related legal documents auto-filled with user profile data:
    - GDPR Article 17 "Right to Erasure" requests
    - CCPA "Right to Delete" requests
    - State-specific privacy law templates (AZ, CA, CO, CT, VA)
    - Cease-and-desist letters for persistent data brokers
    - "Delete my account" email templates for common services
    - Marketing unsubscribe templates

All generators accept a profile dict and broker info to produce
ready-to-send letters/emails with proper legal citations.
"""

import os
import json
from datetime import datetime, timezone
from typing import Optional

from models import get_profile, log_activity

# ---------------------------------------------------------------------------
# Template directory
# ---------------------------------------------------------------------------
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legal_templates")


# ---------------------------------------------------------------------------
# Helper — Variable substitution
# ---------------------------------------------------------------------------

def _fill_template(template: str, variables: dict) -> str:
    """
    Replace {{variable}} placeholders in a template string.

    Args:
        template: Template text with {{placeholders}}.
        variables: Dict of variable_name → value.

    Returns:
        Filled template string.
    """
    result = template
    for key, value in variables.items():
        result = result.replace("{{" + key + "}}", str(value))
    return result


def _get_profile_vars(profile: dict) -> dict:
    """Build standard template variables from a profile dict."""
    addresses = profile.get("addresses", "[]")
    if isinstance(addresses, str):
        try:
            addresses = json.loads(addresses)
        except (json.JSONDecodeError, TypeError):
            addresses = []

    full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
    address_str = addresses[0] if addresses else ""
    city_state_zip = ", ".join(filter(None, [
        profile.get("city", ""),
        profile.get("state", ""),
        profile.get("zip_code", ""),
    ]))

    return {
        "full_name": full_name,
        "first_name": profile.get("first_name", ""),
        "last_name": profile.get("last_name", ""),
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "address": address_str,
        "city": profile.get("city", ""),
        "state": profile.get("state", ""),
        "zip_code": profile.get("zip_code", ""),
        "city_state_zip": city_state_zip,
        "date_of_birth": profile.get("date_of_birth", ""),
        "date": datetime.now(timezone.utc).strftime("%B %d, %Y"),
        "year": str(datetime.now(timezone.utc).year),
    }


# ---------------------------------------------------------------------------
# GDPR Article 17 — Right to Erasure
# ---------------------------------------------------------------------------

GDPR_ERASURE_TEMPLATE = """Subject: Data Erasure Request Under GDPR Article 17

To the Data Protection Officer,

I am writing to request the erasure of my personal data that your organisation holds, in accordance with Article 17 of the General Data Protection Regulation (GDPR), also known as the "Right to Erasure" or "Right to be Forgotten."

My details for identification purposes:
- Full Name: {{full_name}}
- Email Address: {{email}}
- Phone Number: {{phone}}
- Date of Birth: {{date_of_birth}}
- Address: {{address}}, {{city_state_zip}}

I request that you delete all personal data you hold about me, including but not limited to:
- Names, aliases, and identifiers
- Contact information (email, phone, address)
- Location and address history
- Family member and associate information
- Employment and education records
- Financial information
- Any derived or inferred data profiles

I am making this request for the following reasons under GDPR Article 17(1):
(a) The personal data is no longer necessary for the purpose for which it was originally collected.
(b) I withdraw my consent for data processing (Article 6(1)(a) or Article 9(2)(a)).
(d) The personal data has been unlawfully processed.

Please note:
- Under Article 17(1), you are obligated to erase this data "without undue delay" and at the latest within one month of receipt of this request (Article 12(3)).
- Under Article 19, you must notify any recipients to whom the personal data has been disclosed about this erasure request.
- If you have made the personal data public, you must take reasonable steps to inform other controllers processing the data to erase any links to, copies, or replications of that data (Article 17(2)).

If you believe an exemption applies under Article 17(3), please provide a detailed written explanation of the legal basis for your refusal within the statutory time frame.

If you do not comply with this request within one calendar month, I reserve the right to lodge a complaint with the relevant supervisory authority under Article 77 and to seek a judicial remedy under Article 79.

Please confirm receipt of this request and provide written confirmation once the erasure has been completed.

Sincerely,
{{full_name}}
{{email}}
{{date}}
"""


def generate_gdpr_erasure(
    profile_id: int,
    broker_name: str = "",
    broker_email: str = "",
    additional_context: str = "",
) -> dict:
    """
    Generate a GDPR Article 17 erasure request letter.

    Args:
        profile_id: Profile ID for auto-fill.
        broker_name: Name of the data broker/company.
        broker_email: DPO or privacy email address.
        additional_context: Extra context to include.

    Returns:
        {
            "subject": str,
            "body": str,
            "to_email": str,
            "legal_basis": "GDPR Article 17",
        }
    """
    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found"}

    variables = _get_profile_vars(profile)
    variables["broker_name"] = broker_name
    variables["broker_email"] = broker_email

    body = _fill_template(GDPR_ERASURE_TEMPLATE, variables)

    if broker_name:
        body = body.replace(
            "To the Data Protection Officer,",
            f"To the Data Protection Officer at {broker_name},",
        )

    if additional_context:
        body = body.replace(
            "Please confirm receipt",
            f"Additional context: {additional_context}\n\nPlease confirm receipt",
        )

    log_activity(
        None, profile_id, "legal_generated", "legal",
        f"GDPR erasure request generated for {broker_name or 'generic'}",
    )

    return {
        "subject": "Data Erasure Request Under GDPR Article 17",
        "body": body.strip(),
        "to_email": broker_email,
        "legal_basis": "GDPR Article 17",
    }


# ---------------------------------------------------------------------------
# CCPA — Right to Delete
# ---------------------------------------------------------------------------

CCPA_DELETE_TEMPLATE = """Subject: Consumer Data Deletion Request Under CCPA (Cal. Civ. Code § 1798.105)

To Whom It May Concern,

I am a California resident and I am exercising my right under the California Consumer Privacy Act (CCPA), specifically California Civil Code Section 1798.105, to request the deletion of my personal information that your business has collected.

My details for verification:
- Full Name: {{full_name}}
- Email Address: {{email}}
- Phone Number: {{phone}}
- Date of Birth: {{date_of_birth}}
- Address: {{address}}, {{city_state_zip}}

Under CCPA Section 1798.105(a), I have the right to request that your business delete any personal information about me that you have collected from me. Under Section 1798.105(c), upon receiving a verifiable consumer request, you must delete the consumer's personal information from your records and direct any service providers to delete the consumer's personal information from their records.

I request deletion of all personal information you hold, including but not limited to:
- Real name, alias, postal address, email address, phone number
- Internet or other electronic network activity information
- Geolocation data
- Professional or employment-related information
- Education information
- Inferences drawn from any of the above
- Any profiles created about me

Under CCPA, you must:
1. Confirm receipt of this request within 10 business days (Section 1798.130(a)(1))
2. Complete the deletion within 45 calendar days (Section 1798.105(b))
3. Notify all service providers and contractors to delete my data (Section 1798.105(c))

If you require additional information to verify my identity, please contact me at the email address above. Please do not use the additional information for any purpose other than verification.

If you deny this request in whole or in part, please provide:
- The legal basis for the denial
- A description of the specific exception(s) that apply
- Instructions for appealing the decision

Failure to comply with this request may constitute a violation of the CCPA and California Civil Code Section 1798.150.

Sincerely,
{{full_name}}
{{email}}
{{date}}
"""


def generate_ccpa_delete(
    profile_id: int,
    broker_name: str = "",
    broker_email: str = "",
    additional_context: str = "",
) -> dict:
    """
    Generate a CCPA Right to Delete request.

    Args:
        profile_id: Profile ID for auto-fill.
        broker_name: Name of the data broker/company.
        broker_email: Privacy/compliance email address.
        additional_context: Extra context to include.

    Returns:
        {
            "subject": str,
            "body": str,
            "to_email": str,
            "legal_basis": "CCPA Cal. Civ. Code § 1798.105",
        }
    """
    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found"}

    variables = _get_profile_vars(profile)
    body = _fill_template(CCPA_DELETE_TEMPLATE, variables)

    if broker_name:
        body = body.replace(
            "To Whom It May Concern,",
            f"To the Privacy Team at {broker_name},",
        )

    if additional_context:
        body = body.replace(
            "Sincerely,",
            f"Additional context: {additional_context}\n\nSincerely,",
        )

    log_activity(
        None, profile_id, "legal_generated", "legal",
        f"CCPA delete request generated for {broker_name or 'generic'}",
    )

    return {
        "subject": "Consumer Data Deletion Request Under CCPA (Cal. Civ. Code § 1798.105)",
        "body": body.strip(),
        "to_email": broker_email,
        "legal_basis": "CCPA Cal. Civ. Code § 1798.105",
    }


# ---------------------------------------------------------------------------
# State-Specific Privacy Law Templates
# ---------------------------------------------------------------------------

STATE_TEMPLATES = {
    "AZ": {
        "law_name": "Arizona Data Privacy Act (pending)",
        "citation": "Ariz. Rev. Stat. § TBD",
        "effective": "Pending legislation",
        "body": """Subject: Consumer Data Deletion Request — Arizona Resident

To Whom It May Concern,

I am an Arizona resident and I am requesting the deletion of my personal data under applicable state and federal privacy protections.

My details:
- Full Name: {{full_name}}
- Email: {{email}}
- Phone: {{phone}}
- Address: {{address}}, {{city_state_zip}}

While Arizona's comprehensive privacy legislation is pending, I invoke my rights under the FTC Act (15 U.S.C. § 45) prohibiting unfair and deceptive practices, and I request complete removal of my personal information from your databases and all affiliated services.

Please confirm receipt and completion of this request.

Sincerely,
{{full_name}}
{{date}}
""",
    },
    "CA": {
        "law_name": "California Consumer Privacy Act (CCPA) / CPRA",
        "citation": "Cal. Civ. Code §§ 1798.100–1798.199.100",
        "effective": "January 1, 2020 (CCPA) / January 1, 2023 (CPRA)",
        "body": CCPA_DELETE_TEMPLATE,  # Reuse full CCPA template for CA
    },
    "CO": {
        "law_name": "Colorado Privacy Act (CPA)",
        "citation": "Colo. Rev. Stat. § 6-1-1301 et seq.",
        "effective": "July 1, 2023",
        "body": """Subject: Consumer Data Deletion Request Under the Colorado Privacy Act

To Whom It May Concern,

I am a Colorado resident exercising my rights under the Colorado Privacy Act (CPA), Colo. Rev. Stat. § 6-1-1301 et seq.

My identification details:
- Full Name: {{full_name}}
- Email: {{email}}
- Phone: {{phone}}
- Address: {{address}}, {{city_state_zip}}

Under CPA § 6-1-1306(1)(d), I have the right to request deletion of personal data you have collected about me. I hereby exercise that right and request that you:

1. Delete all personal data concerning me from your records
2. Direct any processors to whom you disclosed my data to also delete it
3. Confirm completion of the deletion within 45 days (§ 6-1-1306(3))

If you decline this request, please provide a written explanation of the basis for denial, as required by § 6-1-1306(4), along with instructions for how I may appeal your decision to the Colorado Attorney General.

Sincerely,
{{full_name}}
{{date}}
""",
    },
    "CT": {
        "law_name": "Connecticut Data Privacy Act (CTDPA)",
        "citation": "Conn. Public Act No. 22-15",
        "effective": "July 1, 2023",
        "body": """Subject: Consumer Data Deletion Request Under the Connecticut Data Privacy Act

To Whom It May Concern,

I am a Connecticut resident exercising my rights under the Connecticut Data Privacy Act (CTDPA), Public Act No. 22-15.

My identification details:
- Full Name: {{full_name}}
- Email: {{email}}
- Phone: {{phone}}
- Address: {{address}}, {{city_state_zip}}

Under Section 4(4) of the CTDPA, I have the right to delete personal data that you have obtained about me. I hereby exercise that right and request that you:

1. Delete all personal data you hold about me
2. Notify any third-party recipients of this deletion request
3. Respond to this request within 45 days as required by Section 4(d)

If you require additional information for identity verification, please contact me at the email address above. Any denial must include a justification and instructions for appeal to the Connecticut Attorney General.

Sincerely,
{{full_name}}
{{date}}
""",
    },
    "VA": {
        "law_name": "Virginia Consumer Data Protection Act (VCDPA)",
        "citation": "Va. Code Ann. § 59.1-575 et seq.",
        "effective": "January 1, 2023",
        "body": """Subject: Consumer Data Deletion Request Under the Virginia CDPA

To Whom It May Concern,

I am a Virginia resident exercising my rights under the Virginia Consumer Data Protection Act (VCDPA), Va. Code Ann. § 59.1-575 et seq.

My identification details:
- Full Name: {{full_name}}
- Email: {{email}}
- Phone: {{phone}}
- Address: {{address}}, {{city_state_zip}}

Under § 59.1-577(A)(4), I have the right to delete personal data you have collected about me. I hereby request:

1. Deletion of all personal data concerning me from your systems
2. Notification to any processors or third parties who received my data
3. A response within 45 days as required by § 59.1-577(C)

If this request is denied, please provide the legal basis for the denial and information on how to appeal to the Virginia Attorney General under § 59.1-577(D).

Sincerely,
{{full_name}}
{{date}}
""",
    },
}


def generate_state_request(
    profile_id: int,
    state_code: str,
    broker_name: str = "",
    broker_email: str = "",
) -> dict:
    """
    Generate a state-specific privacy deletion request.

    Args:
        profile_id: Profile for auto-fill.
        state_code: Two-letter state code (AZ, CA, CO, CT, VA).
        broker_name: Target company name.
        broker_email: Target email address.

    Returns:
        {
            "subject": str,
            "body": str,
            "to_email": str,
            "legal_basis": str,
            "law_name": str,
            "state": str,
        }
    """
    state_code = state_code.upper()
    template_info = STATE_TEMPLATES.get(state_code)

    if not template_info:
        return {
            "error": f"No template available for state '{state_code}'. "
                     f"Available: {', '.join(STATE_TEMPLATES.keys())}",
        }

    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found"}

    variables = _get_profile_vars(profile)
    body = _fill_template(template_info["body"], variables)

    if broker_name:
        body = body.replace(
            "To Whom It May Concern,",
            f"To the Privacy Team at {broker_name},",
        )

    # Extract subject from body
    subject = ""
    for line in body.strip().split("\n"):
        if line.startswith("Subject:"):
            subject = line.replace("Subject:", "").strip()
            break

    log_activity(
        None, profile_id, "legal_generated", "legal",
        f"State-specific ({state_code}) request generated for {broker_name or 'generic'}",
    )

    return {
        "subject": subject,
        "body": body.strip(),
        "to_email": broker_email,
        "legal_basis": template_info["citation"],
        "law_name": template_info["law_name"],
        "state": state_code,
    }


def get_available_states() -> list[dict]:
    """Return list of states with available templates."""
    return [
        {
            "code": code,
            "law_name": info["law_name"],
            "citation": info["citation"],
            "effective": info["effective"],
        }
        for code, info in STATE_TEMPLATES.items()
    ]


# ---------------------------------------------------------------------------
# Cease and Desist Letter
# ---------------------------------------------------------------------------

CEASE_DESIST_TEMPLATE = """{{date}}

VIA EMAIL: {{broker_email}}

RE: CEASE AND DESIST — Unauthorized Publication of Personal Information

Dear {{broker_name}} Privacy/Legal Department,

I am writing to demand that you immediately CEASE AND DESIST the publication, sale, distribution, and display of my personal information on your website and through any affiliated services, databases, or data-sharing arrangements.

AFFECTED INDIVIDUAL:
- Name: {{full_name}}
- Email: {{email}}
- Phone: {{phone}}
- Address: {{address}}, {{city_state_zip}}

DEMAND:

1. IMMEDIATELY remove all personal information about me from your website(s) and databases.

2. PERMANENTLY suppress my information from appearing in any future data publications, search results, or data-sharing arrangements.

3. NOTIFY all third parties, affiliates, data partners, and downstream recipients who have received my personal information to also delete it.

4. CONFIRM in writing within fourteen (14) calendar days that all of the above actions have been completed.

LEGAL BASIS:

This demand is supported by the following legal authorities:

• Federal Trade Commission Act (15 U.S.C. § 45) — The publication of my personal information without consent constitutes an unfair and deceptive trade practice.

• State Privacy Laws — Including but not limited to applicable state data protection statutes (CCPA, CPA, VCDPA, CTDPA, and others).

• Common Law Right to Privacy — The public disclosure of private facts and intrusion upon seclusion.

• Negligence — Your failure to implement reasonable data protection measures exposes me to identity theft, harassment, and other harms.

CONSEQUENCES OF NON-COMPLIANCE:

If you fail to comply with this demand within fourteen (14) calendar days, I reserve the right to:

1. File complaints with the Federal Trade Commission and relevant state Attorneys General.
2. Pursue legal action for damages, including statutory damages under applicable state privacy laws.
3. Seek injunctive relief to prevent further publication of my personal information.
4. Report your practices to relevant data protection authorities.

This letter constitutes formal notice of your tortious and potentially illegal conduct. Any further publication or sale of my personal information after receipt of this letter will be considered willful and intentional, entitling me to enhanced damages.

GOVERN YOURSELF ACCORDINGLY.

Sincerely,

{{full_name}}
{{email}}
{{phone}}
{{address}}
{{city_state_zip}}
"""


def generate_cease_desist(
    profile_id: int,
    broker_name: str,
    broker_email: str = "",
    listing_url: str = "",
    additional_violations: str = "",
) -> dict:
    """
    Generate a cease-and-desist letter for a data broker.

    Args:
        profile_id: Profile for auto-fill.
        broker_name: Name of the offending company.
        broker_email: Email to send the letter to.
        listing_url: URL of the specific listing.
        additional_violations: Additional details about violations.

    Returns:
        {
            "subject": str,
            "body": str,
            "to_email": str,
            "legal_basis": "Cease and Desist — Multiple",
        }
    """
    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found"}

    variables = _get_profile_vars(profile)
    variables["broker_name"] = broker_name
    variables["broker_email"] = broker_email

    body = _fill_template(CEASE_DESIST_TEMPLATE, variables)

    if listing_url:
        body = body.replace(
            "DEMAND:",
            f"YOUR LISTING URL: {listing_url}\n\nDEMAND:",
        )

    if additional_violations:
        body = body.replace(
            "CONSEQUENCES OF NON-COMPLIANCE:",
            f"ADDITIONAL VIOLATIONS:\n{additional_violations}\n\n"
            "CONSEQUENCES OF NON-COMPLIANCE:",
        )

    log_activity(
        None, profile_id, "legal_generated", "legal",
        f"Cease-and-desist letter generated for {broker_name}",
    )

    return {
        "subject": f"CEASE AND DESIST — Unauthorized Publication of Personal Information — {profile.get('last_name', '')}",
        "body": body.strip(),
        "to_email": broker_email,
        "legal_basis": "Cease and Desist — FTC Act, State Privacy Laws, Common Law",
    }


# ---------------------------------------------------------------------------
# Account Deletion Email Templates
# ---------------------------------------------------------------------------

ACCOUNT_DELETE_TEMPLATE = """Subject: Account Deletion Request — {{service_name}}

Dear {{service_name}} Support,

I am requesting the complete and permanent deletion of my account and all associated personal data from your service.

Account details:
- Name: {{full_name}}
- Email: {{email}}

I request that you:
1. Permanently delete my account and all associated data
2. Remove my information from all backups within your stated retention period
3. Confirm the deletion via email to {{email}}

This request is made pursuant to applicable data protection laws, including GDPR Article 17 and CCPA Section 1798.105 where applicable.

Thank you,
{{full_name}}
{{date}}
"""


def generate_account_deletion(
    profile_id: int,
    service_name: str,
    service_email: str = "",
) -> dict:
    """
    Generate an account deletion request email for a service.

    Args:
        profile_id: Profile for auto-fill.
        service_name: Name of the service (e.g., "Facebook", "LinkedIn").
        service_email: Support/privacy email for the service.

    Returns:
        {"subject": str, "body": str, "to_email": str}
    """
    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found"}

    variables = _get_profile_vars(profile)
    variables["service_name"] = service_name

    body = _fill_template(ACCOUNT_DELETE_TEMPLATE, variables)

    log_activity(
        None, profile_id, "legal_generated", "legal",
        f"Account deletion request generated for {service_name}",
    )

    return {
        "subject": f"Account Deletion Request — {service_name}",
        "body": body.strip(),
        "to_email": service_email,
    }


# ---------------------------------------------------------------------------
# Marketing Unsubscribe Template
# ---------------------------------------------------------------------------

UNSUBSCRIBE_TEMPLATE = """Subject: Unsubscribe / Remove From All Mailing Lists

To Whom It May Concern,

Please remove the following contact information from all mailing lists, marketing databases, and communication channels:

- Name: {{full_name}}
- Email: {{email}}
- Phone: {{phone}}
- Address: {{address}}, {{city_state_zip}}

I do not wish to receive any further marketing communications, including but not limited to:
- Email marketing
- Direct mail
- Telemarketing calls
- SMS/text messages

This request is made under CAN-SPAM Act (15 U.S.C. § 7704), TCPA (47 U.S.C. § 227), and applicable state marketing laws. Please process this request within 10 business days.

Thank you,
{{full_name}}
{{date}}
"""


def generate_unsubscribe_request(
    profile_id: int,
    company_name: str = "",
    company_email: str = "",
) -> dict:
    """Generate a marketing unsubscribe request."""
    profile = get_profile(profile_id)
    if not profile:
        return {"error": "Profile not found"}

    variables = _get_profile_vars(profile)
    body = _fill_template(UNSUBSCRIBE_TEMPLATE, variables)

    if company_name:
        body = body.replace(
            "To Whom It May Concern,",
            f"To {company_name} Marketing Department,",
        )

    return {
        "subject": "Unsubscribe / Remove From All Mailing Lists",
        "body": body.strip(),
        "to_email": company_email,
    }


# ---------------------------------------------------------------------------
# Get all available legal request types
# ---------------------------------------------------------------------------

def get_legal_request_types() -> list[dict]:
    """Return all available legal request types with descriptions."""
    return [
        {
            "id": "gdpr_erasure",
            "name": "GDPR Article 17 — Right to Erasure",
            "description": "Request data deletion under EU General Data Protection Regulation.",
            "jurisdiction": "EU/EEA",
        },
        {
            "id": "ccpa_delete",
            "name": "CCPA — Right to Delete",
            "description": "Request data deletion under California Consumer Privacy Act.",
            "jurisdiction": "California, USA",
        },
        {
            "id": "state_specific",
            "name": "State-Specific Privacy Request",
            "description": "Privacy deletion requests citing state-specific laws.",
            "jurisdiction": "AZ, CA, CO, CT, VA",
        },
        {
            "id": "cease_desist",
            "name": "Cease and Desist",
            "description": "Formal demand to stop publishing personal information.",
            "jurisdiction": "USA (federal + state)",
        },
        {
            "id": "account_deletion",
            "name": "Account Deletion Request",
            "description": "Request to permanently delete an online account.",
            "jurisdiction": "Global",
        },
        {
            "id": "unsubscribe",
            "name": "Marketing Unsubscribe",
            "description": "Remove from all marketing and mailing lists.",
            "jurisdiction": "USA (CAN-SPAM, TCPA)",
        },
    ]
