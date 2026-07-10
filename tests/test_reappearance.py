"""Reappearance monitoring — due calculation, recheck flow, API."""

import time
from datetime import datetime, timezone, timedelta

import pytest

import models


def _make_confirmed_optout(profile_id, broker_id="whitepages",
                           months_ago=12) -> int:
    """Insert a confirmed opt-out backdated so its window has elapsed."""
    then = (datetime.now(timezone.utc)
            - timedelta(days=months_ago * 30)).isoformat()
    with models.db_session() as conn:
        cursor = conn.execute(
            """INSERT INTO optout_status
               (profile_id, broker_id, broker_name, status,
                confirmed_at, last_checked, updated_at)
               VALUES (?, ?, ?, 'confirmed', ?, '', ?)""",
            (profile_id, broker_id, broker_id.title(), then, then),
        )
        return cursor.lastrowid


def test_due_calculation_respects_window(client, profile_id):
    from reappearance import get_due_optouts

    # Elapsed window → due (whitepages reappears in ~4 months)
    due_id = _make_confirmed_optout(profile_id, "whitepages", months_ago=12)
    # Fresh confirmation → not due
    fresh_id = _make_confirmed_optout(profile_id, "spokeo", months_ago=0)

    due = get_due_optouts(profile_id)
    due_ids = {d["id"] for d in due}
    assert due_id in due_ids
    assert fresh_id not in due_ids
    entry = next(d for d in due if d["id"] == due_id)
    assert entry["reappear_months"] > 0
    assert "due_since" in entry


def test_zero_window_brokers_never_due(client, profile_id):
    """Brokers with reappear_months == 0 must never be flagged."""
    import json
    from pathlib import Path
    from reappearance import get_due_optouts

    brokers = json.loads(
        (Path(__file__).resolve().parent.parent / "brokers.json").read_text())
    zero_broker = next(b["id"] for b in brokers
                       if int(b.get("reappear_months") or 0) == 0)

    _make_confirmed_optout(profile_id, zero_broker, months_ago=48)
    assert get_due_optouts(profile_id) == []


def test_api_due_endpoint(client, profile_id):
    _make_confirmed_optout(profile_id, "whitepages", months_ago=12)
    data = client.get(
        f"/api/reappearance/due?profile_id={profile_id}").get_json()
    assert data["count"] == 1
    assert data["due"][0]["broker_id"] == "whitepages"


def test_recheck_validation(client):
    assert client.post("/api/reappearance/recheck",
                       json={}).status_code == 400
    assert client.post("/api/reappearance/recheck",
                       json={"profile_id": 999}).status_code == 404


def test_recheck_nothing_due(client, profile_id):
    resp = client.post("/api/reappearance/recheck",
                       json={"profile_id": profile_id})
    assert resp.status_code == 200
    assert resp.get_json()["batch_id"] is None


def _wait_until(predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def test_recheck_marks_reappeared_when_found(client, profile_id, monkeypatch):
    """Forced 'found' scan result must flip the opt-out to reappeared."""
    import scanner

    def fake_scan(broker, first_name, last_name, city="", state="",
                  phone="", profile_id=0, batch_id=""):
        return {
            "profile_id": profile_id,
            "broker_id": broker["id"],
            "broker_name": broker.get("name", broker["id"]),
            "broker_category": broker.get("category", ""),
            "found": 1,
            "listing_url": "https://example.com/listing",
            "data_types_found": ["name"],
            "data_depth_score": 1.0,
            "scan_batch_id": batch_id,
        }

    monkeypatch.setattr(scanner, "_scan_single_broker", fake_scan)

    oid = _make_confirmed_optout(profile_id, "whitepages", months_ago=12)
    resp = client.post("/api/reappearance/recheck",
                       json={"profile_id": profile_id})
    assert resp.status_code == 202
    assert resp.get_json()["due"] == 1

    def reappeared():
        with models.db_session() as conn:
            row = conn.execute(
                "SELECT status FROM optout_status WHERE id = ?",
                (oid,)).fetchone()
        return row["status"] == "reappeared"

    assert _wait_until(reappeared), "opt-out was not marked reappeared"


def test_recheck_clean_result_restarts_window(client, profile_id, monkeypatch):
    """A clean recheck must refresh last_checked so the item is no longer due."""
    import scanner
    from reappearance import get_due_optouts

    def fake_scan(broker, first_name, last_name, city="", state="",
                  phone="", profile_id=0, batch_id=""):
        return {
            "profile_id": profile_id,
            "broker_id": broker["id"],
            "broker_name": broker.get("name", broker["id"]),
            "broker_category": broker.get("category", ""),
            "found": 0,
            "listing_url": "",
            "data_types_found": [],
            "data_depth_score": 0.0,
            "scan_batch_id": batch_id,
        }

    monkeypatch.setattr(scanner, "_scan_single_broker", fake_scan)

    oid = _make_confirmed_optout(profile_id, "whitepages", months_ago=12)
    assert len(get_due_optouts(profile_id)) == 1

    client.post("/api/reappearance/recheck", json={"profile_id": profile_id})

    def no_longer_due():
        with models.db_session() as conn:
            row = conn.execute(
                "SELECT status, last_checked FROM optout_status WHERE id = ?",
                (oid,)).fetchone()
        return row["status"] == "confirmed" and bool(row["last_checked"]) \
            and len(get_due_optouts(profile_id)) == 0

    assert _wait_until(no_longer_due), "clean recheck did not restart the window"


def test_reappeared_status_fires_webhook_event(client):
    """optout.reappeared must be a known, dispatchable event."""
    from webhooks import KNOWN_EVENTS, dispatch
    assert "optout.reappeared" in KNOWN_EVENTS
    assert dispatch("optout.reappeared", {"optout_id": 1}) == 0  # no hooks: 0 targets, no error
