"""
PrivacyScrub — Reappearance Monitoring

Data brokers routinely re-list people months after a confirmed removal.
Every broker in the database carries a `reappear_months` estimate (0 =
not known to re-list). This module turns that into action:

    1. `get_due_optouts()` — confirmed opt-outs whose reappearance window
       has elapsed since the last check are flagged as due.
    2. `recheck_profile()` — runs a real scan limited to the due brokers.
       Brokers that show the person's data again are flipped to
       'reappeared' (firing the optout.reappeared webhook via
       OptOutManager.update_status); clean brokers get their
       last_checked timestamp refreshed, restarting their window.
    3. `schedule_reappearance_checks()` — a daily background thread that
       rechecks every profile automatically (setting:
       reappearance_auto_check, on by default).

No guesses: an opt-out is only marked 'reappeared' when a fresh scan
actually finds the listing again.
"""

import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from models import (
    db_session, get_setting, set_setting, get_all_profiles, log_activity,
)

logger = logging.getLogger("privacyscrub.reappearance")

CHECK_INTERVAL = 24 * 3600  # scheduler cadence: daily
_scheduler_thread: Optional[threading.Thread] = None
_scheduler_stop = threading.Event()


# ---------------------------------------------------------------------------
# Due calculation
# ---------------------------------------------------------------------------

def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _broker_windows() -> dict[str, int]:
    """broker_id → reappear_months (0 = not known to re-list)."""
    from scanner import load_brokers
    return {b["id"]: int(b.get("reappear_months") or 0) for b in load_brokers()}


def get_due_optouts(profile_id: Optional[int] = None) -> list[dict]:
    """
    Confirmed opt-outs whose reappearance window has elapsed.

    The window starts at the most recent of confirmed_at / last_checked
    (a clean recheck restarts the clock) and lasts the broker's
    reappear_months. Brokers with reappear_months == 0 are never due.

    Returns opt-out rows augmented with reappear_months and due_since.
    """
    windows = _broker_windows()
    now = datetime.now(timezone.utc)

    query = "SELECT * FROM optout_status WHERE status = 'confirmed'"
    params: tuple = ()
    if profile_id is not None:
        query += " AND profile_id = ?"
        params = (profile_id,)

    with db_session() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    due = []
    for row in rows:
        months = windows.get(row["broker_id"], 0)
        if months <= 0:
            continue
        anchor = max(filter(None, (
            _parse_dt(row.get("confirmed_at")),
            _parse_dt(row.get("last_checked")),
            _parse_dt(row.get("updated_at")),
        )), default=None)
        if anchor is None:
            continue
        due_at = anchor + timedelta(days=months * 30)
        if now >= due_at:
            row["reappear_months"] = months
            row["due_since"] = due_at.isoformat()
            due.append(row)

    return due


def _touch_last_checked(optout_ids: list[int]) -> None:
    if not optout_ids:
        return
    now = datetime.now(timezone.utc).isoformat()
    with db_session() as conn:
        conn.executemany(
            "UPDATE optout_status SET last_checked = ?, updated_at = ? WHERE id = ?",
            [(now, now, oid) for oid in optout_ids],
        )


# ---------------------------------------------------------------------------
# Recheck — scan the due brokers and act on real findings
# ---------------------------------------------------------------------------

def recheck_profile(profile_id: int) -> dict:
    """
    Start a scan limited to this profile's due brokers.

    On completion (async): brokers found listing the person again are
    marked 'reappeared'; clean brokers get last_checked refreshed.

    Returns {"due": n, "batch_id": str|None}. batch_id is None when
    nothing is due.
    """
    due = get_due_optouts(profile_id)
    if not due:
        return {"due": 0, "batch_id": None}

    by_broker = {row["broker_id"]: row for row in due}

    def _on_complete(batch_id: str, results: list[dict]) -> None:
        try:
            reappeared, clean = [], []
            for result in results:
                row = by_broker.get(result.get("broker_id"))
                if not row:
                    continue
                if result.get("found"):
                    reappeared.append(row)
                else:
                    clean.append(row["id"])

            _touch_last_checked(clean)

            if reappeared:
                from optout import OptOutManager
                manager = OptOutManager()
                for row in reappeared:
                    manager.update_status(
                        row["id"], "reappeared",
                        f"Reappearance recheck {batch_id}: listing found again",
                    )

            log_activity(
                None, profile_id, "reappearance_check", "optout",
                f"Reappearance recheck: {len(reappeared)} reappeared, "
                f"{len(clean)} still clean of {len(due)} due",
                {"batch_id": batch_id, "reappeared": len(reappeared),
                 "clean": len(clean)},
            )
        except Exception as e:
            logger.exception("Reappearance recheck post-processing failed: %s", e)

    from scanner import start_scan
    batch_id = start_scan(
        profile_id,
        broker_ids=list(by_broker.keys()),
        on_complete=_on_complete,
    )
    return {"due": len(due), "batch_id": batch_id}


# ---------------------------------------------------------------------------
# Daily scheduler (mirrors the breach check scheduler)
# ---------------------------------------------------------------------------

def _run_scheduled_checks() -> dict:
    checked, started = 0, 0
    for profile in get_all_profiles():
        checked += 1
        try:
            result = recheck_profile(profile["id"])
            if result["batch_id"]:
                started += 1
                logger.info(
                    "Reappearance recheck started for profile %s "
                    "(%d due, batch %s)",
                    profile["id"], result["due"], result["batch_id"])
        except Exception as e:
            logger.exception("Reappearance recheck failed for profile %s: %s",
                             profile["id"], e)

    set_setting("reappearance_last_check",
                datetime.now(timezone.utc).isoformat(),
                "general", "Last scheduled reappearance check timestamp")
    return {"profiles_checked": checked, "rechecks_started": started}


def _scheduler_loop() -> None:
    logger.info("Reappearance scheduler started (interval: %d seconds)",
                CHECK_INTERVAL)
    while not _scheduler_stop.is_set():
        if get_setting("reappearance_auto_check", "1") == "1":
            last = _parse_dt(get_setting("reappearance_last_check", ""))
            elapsed = ((datetime.now(timezone.utc) - last).total_seconds()
                       if last else CHECK_INTERVAL)
            if elapsed >= CHECK_INTERVAL:
                try:
                    _run_scheduled_checks()
                except Exception as e:
                    logger.exception("Scheduled reappearance check crashed: %s", e)

        for _ in range(60):  # respond to stop within 60s
            if _scheduler_stop.wait(60):
                return


def schedule_reappearance_checks() -> None:
    """Start the daily reappearance scheduler. Safe to call repeatedly."""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        logger.info("Reappearance scheduler already running")
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="reappearance-scheduler", daemon=True)
    _scheduler_thread.start()
    logger.info("Reappearance scheduler started (daily interval)")
