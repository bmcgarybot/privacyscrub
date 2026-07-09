"""
PrivacyScrub — Database Models & Auto-Migration

SQLite-backed data layer with automatic schema migration.
All tables are created on first run; new columns are added
non-destructively on upgrade (no data loss).

Tables:
    profiles         — Primary user profiles (the people being protected)
    family_members   — Additional family members linked to a profile
    scan_results     — Individual broker scan hits
    optout_status    — Opt-out request tracking per broker per profile
    breaches         — Breach records from HIBP and other sources
    activity_log     — Full audit trail of all system actions
    settings         — Key-value application settings
    custom_removals  — User-submitted custom removal requests (any URL)
"""

import sqlite3
import os
import json
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, Any

# ---------------------------------------------------------------------------
# Database path — sits next to the application files
# ---------------------------------------------------------------------------
DB_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DB_DIR, "privacyscrub.db")

# ---------------------------------------------------------------------------
# Schema definitions — authoritative source of truth
# Each entry: (column_name, column_type_with_constraints)
# ---------------------------------------------------------------------------

SCHEMA: dict[str, list[tuple[str, str]]] = {
    "profiles": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("first_name", "TEXT NOT NULL"),
        ("last_name", "TEXT NOT NULL"),
        ("middle_name", "TEXT DEFAULT ''"),
        ("email", "TEXT DEFAULT ''"),
        ("phone", "TEXT DEFAULT ''"),
        ("date_of_birth", "TEXT DEFAULT ''"),
        ("addresses", "TEXT DEFAULT '[]'"),          # JSON array of address strings
        ("city", "TEXT DEFAULT ''"),
        ("state", "TEXT DEFAULT ''"),
        ("zip_code", "TEXT DEFAULT ''"),
        ("aliases", "TEXT DEFAULT '[]'"),             # JSON array of former/maiden names
        ("social_accounts", "TEXT DEFAULT '[]'"),     # JSON array of {platform, url} dicts
        ("is_primary", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
        ("updated_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "family_members": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("profile_id", "INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE"),
        ("first_name", "TEXT NOT NULL"),
        ("last_name", "TEXT NOT NULL"),
        ("relationship", "TEXT DEFAULT ''"),           # spouse, child, parent, sibling
        ("date_of_birth", "TEXT DEFAULT ''"),
        ("email", "TEXT DEFAULT ''"),
        ("phone", "TEXT DEFAULT ''"),
        ("is_minor", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "scan_results": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("profile_id", "INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE"),
        ("broker_id", "TEXT NOT NULL"),                # matches brokers.json → id
        ("broker_name", "TEXT NOT NULL"),
        ("broker_category", "TEXT DEFAULT ''"),
        ("found", "INTEGER DEFAULT 0"),                # 1 = listing found
        ("listing_url", "TEXT DEFAULT ''"),
        ("data_types_found", "TEXT DEFAULT '[]'"),     # JSON array: name, phone, address…
        ("data_depth_score", "REAL DEFAULT 0.0"),      # 0.0–1.0 how much data exposed
        ("screenshot_path", "TEXT DEFAULT ''"),
        ("scan_batch_id", "TEXT DEFAULT ''"),           # groups results from one scan run
        ("scanned_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "optout_status": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("profile_id", "INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE"),
        ("broker_id", "TEXT NOT NULL"),
        ("broker_name", "TEXT NOT NULL"),
        ("status", "TEXT DEFAULT 'pending'"),           # pending|submitted|confirmed|reappeared|failed
        ("opt_out_method", "TEXT DEFAULT ''"),           # form|email|phone|mail|api
        ("submitted_at", "TEXT DEFAULT ''"),
        ("confirmed_at", "TEXT DEFAULT ''"),
        ("reappeared_at", "TEXT DEFAULT ''"),
        ("expected_completion", "TEXT DEFAULT ''"),      # estimated date based on processing_days
        ("notes", "TEXT DEFAULT ''"),
        ("auto_submitted", "INTEGER DEFAULT 0"),        # 1 if automation handled it
        ("last_checked", "TEXT DEFAULT ''"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
        ("updated_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "breaches": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("profile_id", "INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE"),
        ("breach_name", "TEXT NOT NULL"),
        ("breach_domain", "TEXT DEFAULT ''"),
        ("breach_date", "TEXT DEFAULT ''"),
        ("compromised_data", "TEXT DEFAULT '[]'"),      # JSON array: emails, passwords, etc.
        ("description", "TEXT DEFAULT ''"),
        ("severity", "TEXT DEFAULT 'medium'"),           # low|medium|high|critical
        ("is_verified", "INTEGER DEFAULT 1"),
        ("is_sensitive", "INTEGER DEFAULT 0"),
        ("pwned_count", "INTEGER DEFAULT 0"),
        ("source", "TEXT DEFAULT 'hibp'"),               # hibp|manual|dark_web
        ("discovered_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "activity_log": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("profile_id", "INTEGER"),                       # NULL for system-level events
        ("action", "TEXT NOT NULL"),                      # scan_started, optout_submitted, etc.
        ("category", "TEXT DEFAULT 'general'"),           # scan|optout|breach|legal|system|export
        ("details", "TEXT DEFAULT ''"),
        ("metadata", "TEXT DEFAULT '{}'"),                # JSON blob for structured data
        ("ip_address", "TEXT DEFAULT ''"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "settings": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("key", "TEXT UNIQUE NOT NULL"),
        ("value", "TEXT DEFAULT ''"),
        ("category", "TEXT DEFAULT 'general'"),           # general|scan|notification|webhook|display
        ("description", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "custom_removals": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("profile_id", "INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE"),
        ("url", "TEXT NOT NULL"),
        ("site_name", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'pending'"),             # pending|submitted|confirmed|failed
        ("method_used", "TEXT DEFAULT ''"),                # email|form|legal
        ("notes", "TEXT DEFAULT ''"),
        ("submitted_at", "TEXT DEFAULT ''"),
        ("confirmed_at", "TEXT DEFAULT ''"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
        ("updated_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "email_requests": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("profile_id", "INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE"),
        ("broker_id", "TEXT NOT NULL"),
        ("to_email", "TEXT NOT NULL"),
        ("subject", "TEXT DEFAULT ''"),
        ("template_key", "TEXT DEFAULT ''"),               # gdpr|ccpa|cpra|generic_us|arizona
        ("status", "TEXT DEFAULT 'pending'"),              # pending|preview|sent|delivered|replied|action_needed|completed|failed
        ("message_id", "TEXT DEFAULT ''"),                 # SMTP Message-ID for tracking
        ("response_text", "TEXT DEFAULT ''"),              # Notes about the response
        ("family_member_id", "INTEGER"),                   # NULL = main profile, else family_members.id
        ("batch_id", "TEXT DEFAULT ''"),                   # Groups emails from one batch send
        ("sent_at", "TEXT DEFAULT ''"),
        ("follow_up_date", "TEXT DEFAULT ''"),             # When to follow up / auto-resend
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
        ("updated_at", "TEXT DEFAULT (datetime('now'))"),
    ],
    "webhooks": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("url", "TEXT NOT NULL"),
        ("events", "TEXT DEFAULT '[]'"),                   # JSON array of subscribed events
        ("active", "INTEGER DEFAULT 1"),
        ("last_status", "TEXT DEFAULT ''"),                # ok | error: <detail>
        ("last_fired_at", "TEXT DEFAULT ''"),
        ("failure_count", "INTEGER DEFAULT 0"),
        ("created_at", "TEXT DEFAULT (datetime('now'))"),
    ],
}

# ---------------------------------------------------------------------------
# Default settings seeded on first run
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: list[dict[str, str]] = [
    {"key": "scan_frequency", "value": "quarterly", "category": "scan",
     "description": "How often to auto-scan brokers (daily/weekly/monthly/quarterly)"},
    {"key": "scan_concurrency", "value": "3", "category": "scan",
     "description": "Maximum concurrent scan threads"},
    {"key": "webhook_url", "value": "", "category": "webhook",
     "description": "URL to POST notifications when exposures are found"},
    {"key": "webhook_secret", "value": "", "category": "webhook",
     "description": "HMAC secret for webhook signature verification"},
    {"key": "notification_email", "value": "", "category": "notification",
     "description": "Email address for scan result notifications"},
    {"key": "displacement_mode", "value": "0", "category": "general",
     "description": "Emergency privacy lockdown mode (0=off, 1=on)"},
    {"key": "hibp_api_key", "value": "", "category": "general",
     "description": "Have I Been Pwned API key for breach monitoring"},
    {"key": "proxy_url", "value": "", "category": "scan",
     "description": "HTTP/SOCKS proxy for scanner requests"},
    {"key": "dark_theme", "value": "1", "category": "display",
     "description": "Enable dark theme (always on by default)"},
    {"key": "first_run_complete", "value": "0", "category": "general",
     "description": "Whether initial setup wizard has been completed"},
]


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """
    Open a SQLite connection with recommended pragmas.

    Args:
        db_path: Override path to DB file. Defaults to DB_PATH.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db_session(db_path: str | None = None):
    """
    Context manager that yields a connection and auto-commits/rolls back.

    Usage:
        with db_session() as conn:
            conn.execute("INSERT INTO ...")
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auto-migration engine
# ---------------------------------------------------------------------------

def _get_existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return set of column names already present in *table*."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check whether *table* exists in the database."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def init_db(db_path: str | None = None) -> None:
    """
    Initialise the database — create missing tables and add missing columns.

    This is safe to call on every application start. It will:
    1. Create any tables that don't exist yet.
    2. For existing tables, ADD any columns defined in SCHEMA that are missing
       (non-destructive migration).
    3. Seed default settings if the settings table is empty.

    Args:
        db_path: Override database path (for tests).
    """
    with db_session(db_path) as conn:
        for table, columns in SCHEMA.items():
            if not _table_exists(conn, table):
                # Build full CREATE TABLE statement
                col_defs = ", ".join(f"{name} {typedef}" for name, typedef in columns)
                conn.execute(f"CREATE TABLE {table} ({col_defs})")
            else:
                # Add any missing columns
                existing = _get_existing_columns(conn, table)
                for col_name, col_type in columns:
                    if col_name not in existing:
                        # Strip PRIMARY KEY / AUTOINCREMENT / NOT NULL for ALTER
                        safe_type = col_type.replace("PRIMARY KEY", "").replace("AUTOINCREMENT", "")
                        # NOT NULL columns need a default for ALTER TABLE
                        if "NOT NULL" in safe_type and "DEFAULT" not in safe_type:
                            safe_type = safe_type.replace("NOT NULL", "") + " DEFAULT ''"
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {safe_type.strip()}")

        # Seed default settings
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM settings")
        if cursor.fetchone()["cnt"] == 0:
            for s in DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings (key, value, category, description) VALUES (?, ?, ?, ?)",
                    (s["key"], s["value"], s["category"], s["description"]),
                )

        # Create useful indexes
        _ensure_indexes(conn)


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    """Create performance indexes if they don't already exist."""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_scan_profile ON scan_results(profile_id)",
        "CREATE INDEX IF NOT EXISTS idx_scan_broker ON scan_results(broker_id)",
        "CREATE INDEX IF NOT EXISTS idx_scan_batch ON scan_results(scan_batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_optout_profile ON optout_status(profile_id)",
        "CREATE INDEX IF NOT EXISTS idx_optout_broker ON optout_status(broker_id)",
        "CREATE INDEX IF NOT EXISTS idx_optout_status ON optout_status(status)",
        "CREATE INDEX IF NOT EXISTS idx_breach_profile ON breaches(profile_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_profile ON activity_log(profile_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_log(action)",
        "CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(key)",
        "CREATE INDEX IF NOT EXISTS idx_custom_profile ON custom_removals(profile_id)",
        "CREATE INDEX IF NOT EXISTS idx_email_req_profile ON email_requests(profile_id)",
        "CREATE INDEX IF NOT EXISTS idx_email_req_broker ON email_requests(broker_id)",
        "CREATE INDEX IF NOT EXISTS idx_email_req_status ON email_requests(status)",
        "CREATE INDEX IF NOT EXISTS idx_email_req_batch ON email_requests(batch_id)",
        "CREATE INDEX IF NOT EXISTS idx_email_req_followup ON email_requests(follow_up_date)",
    ]
    for idx in indexes:
        conn.execute(idx)


# ---------------------------------------------------------------------------
# CRUD helpers — Profiles
# ---------------------------------------------------------------------------

def create_profile(data: dict) -> int:
    """
    Create a new profile and return its ID.

    Args:
        data: Dict with keys matching profiles columns.

    Returns:
        The new profile's row ID.
    """
    with db_session() as conn:
        # Ensure JSON fields are strings
        for json_field in ("addresses", "aliases", "social_accounts"):
            if json_field in data and isinstance(data[json_field], list):
                data[json_field] = json.dumps(data[json_field])

        cols = [k for k in data if k != "id"]
        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        values = [data[c] for c in cols]

        cursor = conn.execute(
            f"INSERT INTO profiles ({col_names}) VALUES ({placeholders})",
            values,
        )
        profile_id = cursor.lastrowid

        log_activity(conn, profile_id, "profile_created", "system",
                      f"Profile created: {data.get('first_name', '')} {data.get('last_name', '')}")
        return profile_id


def get_profile(profile_id: int) -> Optional[dict]:
    """Fetch a single profile by ID, or None if not found."""
    with db_session() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return dict(row) if row else None


def get_all_profiles() -> list[dict]:
    """Return all profiles ordered by creation date."""
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM profiles ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def update_profile(profile_id: int, data: dict) -> bool:
    """
    Update profile fields. Returns True if a row was modified.

    Args:
        profile_id: Profile to update.
        data: Dict of column→value pairs to set.
    """
    with db_session() as conn:
        for json_field in ("addresses", "aliases", "social_accounts"):
            if json_field in data and isinstance(data[json_field], list):
                data[json_field] = json.dumps(data[json_field])

        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in data)
        values = list(data.values()) + [profile_id]

        cursor = conn.execute(f"UPDATE profiles SET {sets} WHERE id = ?", values)
        return cursor.rowcount > 0


def delete_profile(profile_id: int) -> bool:
    """Delete a profile and cascade to related records. Returns True if deleted."""
    with db_session() as conn:
        cursor = conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# CRUD helpers — Family Members
# ---------------------------------------------------------------------------

def add_family_member(data: dict) -> int:
    """Add a family member linked to a profile. Returns new ID."""
    with db_session() as conn:
        cursor = conn.execute(
            """INSERT INTO family_members
               (profile_id, first_name, last_name, relationship, date_of_birth, email, phone, is_minor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["profile_id"], data["first_name"], data["last_name"],
                data.get("relationship", ""), data.get("date_of_birth", ""),
                data.get("email", ""), data.get("phone", ""),
                data.get("is_minor", 0),
            ),
        )
        log_activity(conn, data["profile_id"], "family_member_added", "system",
                      f"Added family member: {data['first_name']} {data['last_name']}")
        return cursor.lastrowid


def get_family_members(profile_id: int) -> list[dict]:
    """Get all family members for a profile."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM family_members WHERE profile_id = ? ORDER BY created_at",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_family_member(member_id: int) -> bool:
    """Delete a family member by ID."""
    with db_session() as conn:
        cursor = conn.execute("DELETE FROM family_members WHERE id = ?", (member_id,))
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# CRUD helpers — Scan Results
# ---------------------------------------------------------------------------

def save_scan_result(data: dict) -> int:
    """Save a single scan result. Returns new row ID."""
    with db_session() as conn:
        if isinstance(data.get("data_types_found"), list):
            data["data_types_found"] = json.dumps(data["data_types_found"])

        cursor = conn.execute(
            """INSERT INTO scan_results
               (profile_id, broker_id, broker_name, broker_category, found,
                listing_url, data_types_found, data_depth_score, scan_batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["profile_id"], data["broker_id"], data["broker_name"],
                data.get("broker_category", ""), data.get("found", 0),
                data.get("listing_url", ""), data.get("data_types_found", "[]"),
                data.get("data_depth_score", 0.0), data.get("scan_batch_id", ""),
            ),
        )
        return cursor.lastrowid


def get_scan_results(profile_id: int, batch_id: str | None = None) -> list[dict]:
    """
    Get scan results for a profile, optionally filtered to a specific batch.
    """
    with db_session() as conn:
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM scan_results WHERE profile_id = ? AND scan_batch_id = ? ORDER BY scanned_at DESC",
                (profile_id, batch_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scan_results WHERE profile_id = ? ORDER BY scanned_at DESC",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_latest_scan_results(profile_id: int) -> list[dict]:
    """Get the most recent scan batch results for a profile."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT scan_batch_id FROM scan_results WHERE profile_id = ? ORDER BY scanned_at DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if not row:
            return []
        return get_scan_results(profile_id, row["scan_batch_id"])


def get_exposure_count(profile_id: int) -> int:
    """Count brokers where the profile was found in the latest scan."""
    results = get_latest_scan_results(profile_id)
    return sum(1 for r in results if r.get("found"))


# ---------------------------------------------------------------------------
# CRUD helpers — Opt-Out Status
# ---------------------------------------------------------------------------

def save_optout(data: dict) -> int:
    """Create or update an opt-out status record. Returns row ID."""
    with db_session() as conn:
        # Check if one already exists for this profile+broker
        existing = conn.execute(
            "SELECT id FROM optout_status WHERE profile_id = ? AND broker_id = ?",
            (data["profile_id"], data["broker_id"]),
        ).fetchone()

        if existing:
            # Update existing record
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            sets = ", ".join(f"{k} = ?" for k in data if k not in ("profile_id", "broker_id"))
            values = [data[k] for k in data if k not in ("profile_id", "broker_id")]
            values += [data["profile_id"], data["broker_id"]]
            conn.execute(
                f"UPDATE optout_status SET {sets} WHERE profile_id = ? AND broker_id = ?",
                values,
            )
            return existing["id"]
        else:
            cursor = conn.execute(
                """INSERT INTO optout_status
                   (profile_id, broker_id, broker_name, status, opt_out_method,
                    submitted_at, expected_completion, notes, auto_submitted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["profile_id"], data["broker_id"], data.get("broker_name", ""),
                    data.get("status", "pending"), data.get("opt_out_method", ""),
                    data.get("submitted_at", ""), data.get("expected_completion", ""),
                    data.get("notes", ""), data.get("auto_submitted", 0),
                ),
            )
            return cursor.lastrowid


def get_optouts(profile_id: int, status: str | None = None) -> list[dict]:
    """Get opt-out records for a profile, optionally filtered by status."""
    with db_session() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM optout_status WHERE profile_id = ? AND status = ? ORDER BY updated_at DESC",
                (profile_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM optout_status WHERE profile_id = ? ORDER BY updated_at DESC",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_all_optouts() -> list[dict]:
    """Get all opt-out records across all profiles."""
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM optout_status ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def update_optout_status(optout_id: int, status: str, notes: str = "") -> bool:
    """Update the status of an opt-out record."""
    with db_session() as conn:
        now = datetime.now(timezone.utc).isoformat()
        extra = ""
        params: list[Any] = [status, notes, now]

        if status == "confirmed":
            extra = ", confirmed_at = ?"
            params.insert(2, now)
        elif status == "reappeared":
            extra = ", reappeared_at = ?"
            params.insert(2, now)

        params.append(optout_id)
        cursor = conn.execute(
            f"UPDATE optout_status SET status = ?, notes = ?{extra}, updated_at = ? WHERE id = ?",
            params,
        )
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# CRUD helpers — Breaches
# ---------------------------------------------------------------------------

def save_breach(data: dict) -> int:
    """Save a breach record. Returns row ID."""
    with db_session() as conn:
        if isinstance(data.get("compromised_data"), list):
            data["compromised_data"] = json.dumps(data["compromised_data"])

        # Avoid duplicates
        existing = conn.execute(
            "SELECT id FROM breaches WHERE profile_id = ? AND breach_name = ?",
            (data["profile_id"], data["breach_name"]),
        ).fetchone()
        if existing:
            return existing["id"]

        cursor = conn.execute(
            """INSERT INTO breaches
               (profile_id, breach_name, breach_domain, breach_date,
                compromised_data, description, severity, is_verified,
                is_sensitive, pwned_count, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["profile_id"], data["breach_name"],
                data.get("breach_domain", ""), data.get("breach_date", ""),
                data.get("compromised_data", "[]"), data.get("description", ""),
                data.get("severity", "medium"), data.get("is_verified", 1),
                data.get("is_sensitive", 0), data.get("pwned_count", 0),
                data.get("source", "hibp"),
            ),
        )
        return cursor.lastrowid


def get_breaches(profile_id: int) -> list[dict]:
    """Get all breaches for a profile."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM breaches WHERE profile_id = ? ORDER BY breach_date DESC",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_breach_count(profile_id: int) -> int:
    """Count breaches for a profile."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM breaches WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# CRUD helpers — Activity Log
# ---------------------------------------------------------------------------

def log_activity(
    conn_or_none,
    profile_id: int | None,
    action: str,
    category: str = "general",
    details: str = "",
    metadata: dict | None = None,
) -> int:
    """
    Write an entry to the activity log.

    Can be called with an existing connection (inside a transaction)
    or with None to open its own connection.
    """
    meta_json = json.dumps(metadata or {})

    def _insert(conn):
        cursor = conn.execute(
            """INSERT INTO activity_log (profile_id, action, category, details, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (profile_id, action, category, details, meta_json),
        )
        return cursor.lastrowid

    if conn_or_none is not None:
        return _insert(conn_or_none)
    else:
        with db_session() as conn:
            return _insert(conn)


def get_activity_log(
    profile_id: int | None = None,
    category: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    Retrieve activity log entries with optional filters.
    """
    with db_session() as conn:
        query = "SELECT * FROM activity_log WHERE 1=1"
        params: list[Any] = []

        if profile_id is not None:
            query += " AND profile_id = ?"
            params.append(profile_id)
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CRUD helpers — Settings
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    """Get a setting value by key."""
    with db_session() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str, category: str = "general", description: str = "") -> None:
    """Set a setting value (upsert)."""
    with db_session() as conn:
        existing = conn.execute("SELECT id FROM settings WHERE key = ?", (key,)).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            conn.execute(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                (value, now, key),
            )
        else:
            conn.execute(
                "INSERT INTO settings (key, value, category, description, updated_at) VALUES (?, ?, ?, ?, ?)",
                (key, value, category, description, now),
            )


def get_all_settings() -> dict[str, str]:
    """Return all settings as a key→value dict."""
    with db_session() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def get_settings_by_category(category: str) -> list[dict]:
    """Return all settings in a category."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM settings WHERE category = ? ORDER BY key",
            (category,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CRUD helpers — Custom Removals
# ---------------------------------------------------------------------------

def add_custom_removal(data: dict) -> int:
    """Add a custom removal request. Returns new row ID."""
    with db_session() as conn:
        cursor = conn.execute(
            """INSERT INTO custom_removals
               (profile_id, url, site_name, status, method_used, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data["profile_id"], data["url"],
                data.get("site_name", ""), data.get("status", "pending"),
                data.get("method_used", ""), data.get("notes", ""),
            ),
        )
        log_activity(conn, data["profile_id"], "custom_removal_added", "optout",
                      f"Custom removal added: {data['url']}")
        return cursor.lastrowid


def get_custom_removals(profile_id: int) -> list[dict]:
    """Get all custom removal requests for a profile."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM custom_removals WHERE profile_id = ? ORDER BY created_at DESC",
            (profile_id,),
        ).fetchall()
        return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def add_webhook(url: str, events: list[str], active: bool = True) -> int:
    """Register a webhook. Returns the new webhook id."""
    with db_session() as conn:
        cursor = conn.execute(
            "INSERT INTO webhooks (url, events, active) VALUES (?, ?, ?)",
            (url, json.dumps(sorted(set(events))), 1 if active else 0),
        )
        return cursor.lastrowid


def get_webhooks(active_only: bool = False) -> list[dict]:
    """List registered webhooks, events decoded to lists."""
    query = "SELECT * FROM webhooks"
    if active_only:
        query += " WHERE active = 1"
    with db_session() as conn:
        rows = conn.execute(query + " ORDER BY id").fetchall()
    hooks = []
    for row in rows:
        hook = dict(row)
        try:
            hook["events"] = json.loads(hook.get("events") or "[]")
        except (TypeError, json.JSONDecodeError):
            hook["events"] = []
        hooks.append(hook)
    return hooks


def get_webhook(webhook_id: int) -> Optional[dict]:
    """Fetch one webhook by id, or None."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM webhooks WHERE id = ?", (webhook_id,)
        ).fetchone()
    if not row:
        return None
    hook = dict(row)
    try:
        hook["events"] = json.loads(hook.get("events") or "[]")
    except (TypeError, json.JSONDecodeError):
        hook["events"] = []
    return hook


def delete_webhook(webhook_id: int) -> bool:
    """Delete a webhook. Returns True if a row was removed."""
    with db_session() as conn:
        cursor = conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
        return cursor.rowcount > 0


def record_webhook_result(webhook_id: int, ok: bool, detail: str = "") -> None:
    """Record the outcome of a delivery attempt on the webhook row."""
    status = "ok" if ok else f"error: {detail}"[:200]
    with db_session() as conn:
        if ok:
            conn.execute(
                "UPDATE webhooks SET last_status = ?, last_fired_at = datetime('now'), "
                "failure_count = 0 WHERE id = ?",
                (status, webhook_id),
            )
        else:
            conn.execute(
                "UPDATE webhooks SET last_status = ?, last_fired_at = datetime('now'), "
                "failure_count = failure_count + 1 WHERE id = ?",
                (status, webhook_id),
            )
