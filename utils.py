"""
PrivacyScrub — Utility Functions

Privacy DNA Score calculation, PDF report generation, and data export
(CSV/JSON). The scoring algorithm uses a weighted composite that factors
in broker exposure, breach history, data depth, and social media exposure.

Score Weights:
    Broker Exposure    40%  — How many brokers list the user
    Breach Count       20%  — Number of known breaches
    Data Depth         25%  — Average depth of exposed data per broker
    Social Exposure    15%  — Social media / platform discoverability
"""

import csv
import io
import json
import os
import math
from datetime import datetime, timezone
from typing import Any

# PDF generation
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Image as RLImage,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


# ---------------------------------------------------------------------------
# Privacy DNA Score — Weighted composite (0–100, lower = better)
# ---------------------------------------------------------------------------

# Weight constants
W_BROKER_EXPOSURE = 0.40
W_BREACH_COUNT = 0.20
W_DATA_DEPTH = 0.25
W_SOCIAL_EXPOSURE = 0.15

# Normalisation ceilings (scores plateau at these values)
MAX_BROKER_HITS = 100       # 100+ brokers ⇒ worst possible broker score
MAX_BREACHES = 20           # 20+ breaches ⇒ worst possible breach score
MAX_SOCIAL_HITS = 10        # found on 10+ social platforms ⇒ max social score

# Social / platform broker IDs for social exposure sub-score
SOCIAL_BROKER_IDS = {
    "google", "facebook", "instagram", "twitter", "tiktok",
    "reddit", "pinterest", "discord", "whatsapp", "linkedin",
    "youtube", "snapchat", "telegram", "tumblr",
}


def calculate_privacy_score(
    scan_results: list[dict],
    breaches: list[dict],
    total_brokers: int = 800,
) -> dict:
    """
    Calculate the Privacy DNA Score (0–100).

    A score of 0 means maximum exposure; 100 means fully private.

    Args:
        scan_results: Latest scan results dicts (must have 'found', 'broker_id',
                      'data_depth_score', 'broker_category').
        breaches: Breach dicts for the profile.
        total_brokers: Total brokers in the database (for %).

    Returns:
        Dict with overall score and component breakdown:
        {
            "score": 72,
            "grade": "C",
            "broker_exposure": {"raw": 35, "normalised": 35, "weighted": 14.0},
            "breach_count": {"raw": 3, "normalised": 15, "weighted": 3.0},
            "data_depth": {"raw": 0.42, "normalised": 42, "weighted": 10.5},
            "social_exposure": {"raw": 4, "normalised": 40, "weighted": 6.0},
            "risk_level": "moderate",
            "recommendations": [...]
        }
    """
    # --- Broker Exposure (40%) ---
    broker_hits = sum(1 for r in scan_results if r.get("found"))
    broker_raw = min(broker_hits / MAX_BROKER_HITS, 1.0) * 100  # 0–100 (100 = worst)

    # --- Breach Count (20%) ---
    breach_count = len(breaches)
    breach_raw = min(breach_count / MAX_BREACHES, 1.0) * 100

    # --- Data Depth (25%) ---
    found_results = [r for r in scan_results if r.get("found")]
    if found_results:
        avg_depth = sum(r.get("data_depth_score", 0) for r in found_results) / len(found_results)
    else:
        avg_depth = 0.0
    depth_raw = avg_depth * 100  # already 0–1 scale

    # --- Social Exposure (15%) ---
    social_hits = sum(
        1 for r in scan_results
        if r.get("found") and r.get("broker_id", "").lower() in SOCIAL_BROKER_IDS
    )
    social_raw = min(social_hits / MAX_SOCIAL_HITS, 1.0) * 100

    # --- Weighted composite (0–100 where 100 = worst exposure) ---
    exposure_score = (
        broker_raw * W_BROKER_EXPOSURE
        + breach_raw * W_BREACH_COUNT
        + depth_raw * W_DATA_DEPTH
        + social_raw * W_SOCIAL_EXPOSURE
    )

    # Invert: 100 = fully private, 0 = fully exposed
    privacy_score = max(0, min(100, round(100 - exposure_score)))

    # Grade
    grade = _score_to_grade(privacy_score)
    risk_level = _score_to_risk(privacy_score)
    recommendations = _generate_recommendations(
        broker_hits, breach_count, avg_depth, social_hits, scan_results
    )

    return {
        "score": privacy_score,
        "grade": grade,
        "risk_level": risk_level,
        "broker_exposure": {
            "raw": broker_hits,
            "normalised": round(broker_raw),
            "weighted": round(broker_raw * W_BROKER_EXPOSURE, 1),
        },
        "breach_count": {
            "raw": breach_count,
            "normalised": round(breach_raw),
            "weighted": round(breach_raw * W_BREACH_COUNT, 1),
        },
        "data_depth": {
            "raw": round(avg_depth, 3),
            "normalised": round(depth_raw),
            "weighted": round(depth_raw * W_DATA_DEPTH, 1),
        },
        "social_exposure": {
            "raw": social_hits,
            "normalised": round(social_raw),
            "weighted": round(social_raw * W_SOCIAL_EXPOSURE, 1),
        },
        "recommendations": recommendations,
        "total_brokers_scanned": len(scan_results),
        "total_brokers_found": broker_hits,
    }


def _score_to_grade(score: int) -> str:
    """Convert numeric score to letter grade."""
    if score >= 90:
        return "A+"
    elif score >= 80:
        return "A"
    elif score >= 70:
        return "B"
    elif score >= 60:
        return "C"
    elif score >= 50:
        return "D"
    else:
        return "F"


def _score_to_risk(score: int) -> str:
    """Convert numeric score to risk level label."""
    if score >= 80:
        return "low"
    elif score >= 60:
        return "moderate"
    elif score >= 40:
        return "high"
    else:
        return "critical"


def _generate_recommendations(
    broker_hits: int,
    breach_count: int,
    avg_depth: float,
    social_hits: int,
    scan_results: list[dict],
) -> list[str]:
    """Generate actionable privacy improvement recommendations."""
    recs = []

    if broker_hits > 50:
        recs.append("CRITICAL: You appear on 50+ data brokers. Begin systematic opt-out immediately.")
    elif broker_hits > 20:
        recs.append("High broker exposure detected. Prioritise opt-outs for Tier 1 people-search sites.")
    elif broker_hits > 5:
        recs.append("Moderate broker exposure. Submit opt-out requests for the identified brokers.")

    if breach_count > 10:
        recs.append("CRITICAL: 10+ breaches found. Change all passwords and enable 2FA everywhere.")
    elif breach_count > 3:
        recs.append("Multiple breaches detected. Change passwords for affected accounts and use a password manager.")
    elif breach_count > 0:
        recs.append("Breach(es) found. Update passwords for affected services.")

    if avg_depth > 0.7:
        recs.append("Deep data exposure — brokers have extensive personal details. Consider legal removal requests.")
    elif avg_depth > 0.4:
        recs.append("Moderate data depth — brokers have significant personal information visible.")

    if social_hits > 5:
        recs.append("High social media visibility. Review privacy settings on all platforms.")
    elif social_hits > 2:
        recs.append("Review social media privacy settings — several platforms expose your data publicly.")

    # Check for specific high-risk categories
    categories_found = set()
    for r in scan_results:
        if r.get("found"):
            categories_found.add(r.get("broker_category", ""))

    if "financial" in categories_found:
        recs.append("Financial data broker exposure detected. Consider credit freezes at all bureaus.")
    if "background_check" in categories_found:
        recs.append("Background check sites list you. Submit removal requests — these often have opt-out pages.")

    if not recs:
        recs.append("Your privacy posture looks good! Continue monitoring with quarterly scans.")

    return recs


# ---------------------------------------------------------------------------
# PDF Report Generator
# ---------------------------------------------------------------------------

def generate_pdf_report(
    profile: dict,
    score_data: dict,
    scan_results: list[dict],
    breaches: list[dict],
    optouts: list[dict],
    output_path: str | None = None,
) -> bytes | str:
    """
    Generate a comprehensive privacy audit PDF report.

    Args:
        profile: Profile dict.
        score_data: Output from calculate_privacy_score().
        scan_results: Latest scan results.
        breaches: Breach records.
        optouts: Opt-out status records.
        output_path: If provided, write to file and return path.
                     Otherwise return PDF bytes.

    Returns:
        File path (str) if output_path given, else raw PDF bytes.

    Raises:
        ImportError: If reportlab is not installed.
    """
    if not REPORTLAB_AVAILABLE:
        raise ImportError(
            "reportlab is required for PDF generation. "
            "Install it with: pip install reportlab"
        )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=24,
        textColor=HexColor("#00d4aa"),
        spaceAfter=20,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=16,
        textColor=HexColor("#00d4aa"),
        spaceBefore=15,
        spaceAfter=10,
    )
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["Normal"],
        fontSize=11,
        leading=14,
        spaceAfter=6,
    )
    small_style = ParagraphStyle(
        "SmallText",
        parent=styles["Normal"],
        fontSize=9,
        textColor=HexColor("#888888"),
    )

    elements = []

    # --- Title ---
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    full_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()

    elements.append(Paragraph("🛡️ PrivacyScrub", title_style))
    elements.append(Paragraph("Privacy Audit Report", heading_style))
    elements.append(Paragraph(f"Generated: {now_str}", body_style))
    elements.append(Paragraph(f"Subject: {full_name}", body_style))
    elements.append(Spacer(1, 20))

    # --- Privacy Score ---
    elements.append(Paragraph("Privacy DNA Score", heading_style))
    score = score_data.get("score", 0)
    grade = score_data.get("grade", "?")
    risk = score_data.get("risk_level", "unknown")
    elements.append(Paragraph(
        f"<b>Score: {score}/100</b> &nbsp; Grade: <b>{grade}</b> &nbsp; Risk: <b>{risk.upper()}</b>",
        body_style,
    ))
    elements.append(Spacer(1, 6))

    # Score breakdown table
    breakdown_data = [
        ["Component", "Raw", "Impact (Weighted)"],
        [
            "Broker Exposure (40%)",
            str(score_data["broker_exposure"]["raw"]) + " brokers",
            str(score_data["broker_exposure"]["weighted"]),
        ],
        [
            "Breach Count (20%)",
            str(score_data["breach_count"]["raw"]) + " breaches",
            str(score_data["breach_count"]["weighted"]),
        ],
        [
            "Data Depth (25%)",
            f"{score_data['data_depth']['raw']:.1%}",
            str(score_data["data_depth"]["weighted"]),
        ],
        [
            "Social Exposure (15%)",
            str(score_data["social_exposure"]["raw"]) + " platforms",
            str(score_data["social_exposure"]["weighted"]),
        ],
    ]
    breakdown_table = Table(breakdown_data, colWidths=[2.5 * inch, 1.5 * inch, 1.5 * inch])
    breakdown_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#00d4aa")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#333333")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f5f5f5"), HexColor("#ffffff")]),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(breakdown_table)
    elements.append(Spacer(1, 15))

    # --- Recommendations ---
    elements.append(Paragraph("Recommendations", heading_style))
    for rec in score_data.get("recommendations", []):
        elements.append(Paragraph(f"• {rec}", body_style))
    elements.append(Spacer(1, 10))

    # --- Broker Exposure Summary ---
    elements.append(PageBreak())
    elements.append(Paragraph("Broker Exposure Details", heading_style))

    found_results = [r for r in scan_results if r.get("found")]
    if found_results:
        broker_table_data = [["Broker", "Category", "Data Types", "Depth"]]
        for r in found_results[:50]:  # Cap at 50 for readability
            data_types = r.get("data_types_found", "[]")
            if isinstance(data_types, str):
                try:
                    data_types = json.loads(data_types)
                except (json.JSONDecodeError, TypeError):
                    data_types = []
            types_str = ", ".join(data_types[:3])
            if len(data_types) > 3:
                types_str += f" +{len(data_types) - 3}"

            broker_table_data.append([
                r.get("broker_name", r.get("broker_id", "")),
                r.get("broker_category", ""),
                types_str,
                f"{r.get('data_depth_score', 0):.0%}",
            ])

        broker_table = Table(broker_table_data, colWidths=[2 * inch, 1.2 * inch, 2 * inch, 0.6 * inch])
        broker_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#00d4aa")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f5f5f5"), HexColor("#ffffff")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(broker_table)
        if len(found_results) > 50:
            elements.append(Paragraph(
                f"... and {len(found_results) - 50} more brokers (see full CSV export)",
                small_style,
            ))
    else:
        elements.append(Paragraph("No broker exposures found. ✅", body_style))

    # --- Breaches ---
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("Breach History", heading_style))
    if breaches:
        for b in breaches[:20]:
            compromised = b.get("compromised_data", "[]")
            if isinstance(compromised, str):
                try:
                    compromised = json.loads(compromised)
                except (json.JSONDecodeError, TypeError):
                    compromised = []
            elements.append(Paragraph(
                f"<b>{b.get('breach_name', 'Unknown')}</b> "
                f"({b.get('breach_date', 'unknown date')}) — "
                f"Severity: {b.get('severity', 'unknown').upper()} — "
                f"Compromised: {', '.join(compromised[:5])}",
                body_style,
            ))
    else:
        elements.append(Paragraph("No breaches found. ✅", body_style))

    # --- Opt-Out Progress ---
    elements.append(PageBreak())
    elements.append(Paragraph("Opt-Out Progress", heading_style))
    if optouts:
        status_counts = {}
        for o in optouts:
            s = o.get("status", "pending")
            status_counts[s] = status_counts.get(s, 0) + 1

        for status, count in sorted(status_counts.items()):
            elements.append(Paragraph(
                f"• <b>{status.capitalize()}</b>: {count} brokers",
                body_style,
            ))
    else:
        elements.append(Paragraph("No opt-out requests submitted yet.", body_style))

    # --- Footer ---
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(
        "This report was generated by PrivacyScrub — self-hosted privacy removal platform. "
        "https://github.com/privacyscrub",
        small_style,
    ))
    elements.append(Paragraph(
        f"Report ID: {full_name.replace(' ', '-').lower()}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        small_style,
    ))

    # Build PDF
    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(pdf_bytes)
        return output_path

    return pdf_bytes


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

def export_scan_results_csv(scan_results: list[dict]) -> str:
    """
    Export scan results to CSV format string.

    Args:
        scan_results: List of scan result dicts.

    Returns:
        CSV content as a string.
    """
    output = io.StringIO()
    fieldnames = [
        "broker_id", "broker_name", "broker_category", "found",
        "listing_url", "data_types_found", "data_depth_score", "scanned_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for result in scan_results:
        row = {k: result.get(k, "") for k in fieldnames}
        # Flatten JSON fields
        if isinstance(row.get("data_types_found"), list):
            row["data_types_found"] = "; ".join(row["data_types_found"])
        elif isinstance(row.get("data_types_found"), str):
            try:
                parsed = json.loads(row["data_types_found"])
                row["data_types_found"] = "; ".join(parsed) if isinstance(parsed, list) else row["data_types_found"]
            except (json.JSONDecodeError, TypeError):
                pass
        writer.writerow(row)

    return output.getvalue()


def export_breaches_csv(breaches: list[dict]) -> str:
    """Export breach records to CSV format string."""
    output = io.StringIO()
    fieldnames = [
        "breach_name", "breach_domain", "breach_date", "compromised_data",
        "severity", "is_verified", "pwned_count", "source", "discovered_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for breach in breaches:
        row = {k: breach.get(k, "") for k in fieldnames}
        if isinstance(row.get("compromised_data"), list):
            row["compromised_data"] = "; ".join(row["compromised_data"])
        elif isinstance(row.get("compromised_data"), str):
            try:
                parsed = json.loads(row["compromised_data"])
                row["compromised_data"] = "; ".join(parsed) if isinstance(parsed, list) else row["compromised_data"]
            except (json.JSONDecodeError, TypeError):
                pass
        writer.writerow(row)

    return output.getvalue()


def export_optouts_csv(optouts: list[dict]) -> str:
    """Export opt-out status records to CSV format string."""
    output = io.StringIO()
    fieldnames = [
        "broker_id", "broker_name", "status", "opt_out_method",
        "submitted_at", "confirmed_at", "reappeared_at",
        "expected_completion", "auto_submitted", "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for optout in optouts:
        row = {k: optout.get(k, "") for k in fieldnames}
        writer.writerow(row)

    return output.getvalue()


# ---------------------------------------------------------------------------
# JSON Export
# ---------------------------------------------------------------------------

def export_full_report_json(
    profile: dict,
    score_data: dict,
    scan_results: list[dict],
    breaches: list[dict],
    optouts: list[dict],
) -> str:
    """
    Export a comprehensive privacy report as JSON.

    Args:
        profile: Profile dict.
        score_data: Privacy score breakdown.
        scan_results: Scan results.
        breaches: Breach records.
        optouts: Opt-out status records.

    Returns:
        Pretty-printed JSON string.
    """
    report = {
        "report_metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "PrivacyScrub",
            "version": "1.0.0",
        },
        "profile": {
            "name": f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
            "email": profile.get("email", ""),
            "state": profile.get("state", ""),
        },
        "privacy_score": score_data,
        "scan_summary": {
            "total_scanned": len(scan_results),
            "total_found": sum(1 for r in scan_results if r.get("found")),
            "by_category": _group_by_category(scan_results),
        },
        "broker_exposures": [
            {
                "broker_id": r["broker_id"],
                "broker_name": r.get("broker_name", ""),
                "category": r.get("broker_category", ""),
                "listing_url": r.get("listing_url", ""),
                "data_depth": r.get("data_depth_score", 0),
                "data_types": _safe_json_loads(r.get("data_types_found", "[]")),
            }
            for r in scan_results if r.get("found")
        ],
        "breaches": [
            {
                "name": b["breach_name"],
                "domain": b.get("breach_domain", ""),
                "date": b.get("breach_date", ""),
                "severity": b.get("severity", ""),
                "compromised": _safe_json_loads(b.get("compromised_data", "[]")),
            }
            for b in breaches
        ],
        "optout_progress": {
            "total": len(optouts),
            "by_status": _count_by_field(optouts, "status"),
        },
    }

    return json.dumps(report, indent=2, default=str)


def _group_by_category(scan_results: list[dict]) -> dict:
    """Group scan results by broker category with counts."""
    groups: dict[str, dict[str, int]] = {}
    for r in scan_results:
        cat = r.get("broker_category", "unknown")
        if cat not in groups:
            groups[cat] = {"scanned": 0, "found": 0}
        groups[cat]["scanned"] += 1
        if r.get("found"):
            groups[cat]["found"] += 1
    return groups


def _count_by_field(items: list[dict], field: str) -> dict[str, int]:
    """Count items grouped by a field value."""
    counts: dict[str, int] = {}
    for item in items:
        val = item.get(field, "unknown")
        counts[val] = counts.get(val, 0) + 1
    return counts


def _safe_json_loads(val: Any) -> Any:
    """Safely parse JSON string, returning original value on failure."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


# ---------------------------------------------------------------------------
# Misc Helpers
# ---------------------------------------------------------------------------

def format_timestamp(ts: str | None) -> str:
    """Format an ISO timestamp to a human-readable string."""
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %I:%M %p")
    except (ValueError, AttributeError):
        return ts


def truncate_string(s: str, max_len: int = 80) -> str:
    """Truncate a string with ellipsis if it exceeds max_len."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    keepchars = (" ", ".", "_", "-")
    return "".join(c for c in name if c.isalnum() or c in keepchars).strip()
