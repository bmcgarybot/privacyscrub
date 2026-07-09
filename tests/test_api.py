"""API contract tests — every endpoint the dashboard depends on."""

import json


# ---------------------------------------------------------------------------
# Health & pages
# ---------------------------------------------------------------------------

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] in ("ok", "healthy")


def test_all_pages_render(client):
    for route in ("/", "/scanner", "/profiles", "/optouts", "/breaches",
                  "/legal", "/reports", "/settings", "/activity",
                  "/accounts", "/email-center", "/credit", "/displacement",
                  "/api-docs"):
        assert client.get(route).status_code == 200, route


# ---------------------------------------------------------------------------
# Scan API
# ---------------------------------------------------------------------------

def test_scan_requires_profile(client):
    resp = client.post("/api/scan", json={})
    assert resp.status_code == 400


def test_scan_unknown_profile_404(client):
    resp = client.post("/api/scan", json={"profile_id": 9999})
    assert resp.status_code == 404


def test_scan_start_returns_batch(client, profile_id):
    resp = client.post("/api/scan", json={
        "profile_id": profile_id, "broker_ids": ["whitepages"]})
    assert resp.status_code == 202
    batch_id = resp.get_json()["batch_id"]
    assert batch_id

    status = client.get(f"/api/scan/{batch_id}/status").get_json()
    for field in ("progress", "brokers_checked", "found", "status", "status_text"):
        assert field in status, field


def test_scan_latest_requires_profile_param(client):
    assert client.get("/api/scan/latest/status").status_code == 400


def test_scan_status_unknown_batch_404(client):
    assert client.get("/api/scan/nope-123/status").status_code == 404


# ---------------------------------------------------------------------------
# Opt-out API
# ---------------------------------------------------------------------------

def test_optout_submit_requires_profile(client):
    resp = client.post("/api/optout/whitepages/submit", json={})
    assert resp.status_code == 400


def test_optout_submit_unknown_broker_404(client, profile_id):
    resp = client.post("/api/optout/not-a-broker/submit",
                       json={"profile_id": profile_id})
    assert resp.status_code == 404


def test_optout_submit_creates_record_and_reports_honestly(client, profile_id):
    resp = client.post("/api/optout/whitepages/submit",
                       json={"profile_id": profile_id})
    assert resp.status_code == 200
    data = resp.get_json()
    assert "submitted" in data and "optout_id" in data
    # Without SMTP configured the honest outcomes are draft or manual —
    # never a bare success with nothing behind it.
    if data["submitted"]:
        assert data["draft"] is True
    else:
        assert data["manual_required"] is True


def test_optout_batch_validation(client):
    assert client.post("/api/optout/batch", json={}).status_code == 400
    assert client.post("/api/optout/batch",
                       json={"optout_ids": ["x"]}).status_code == 400


def test_optout_batch_counts(client, profile_id):
    created = client.post("/api/optout/whitepages/submit",
                          json={"profile_id": profile_id}).get_json()
    resp = client.post("/api/optout/batch",
                       json={"optout_ids": [created["optout_id"]]})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["submitted"] + data["manual_required"] + data["failed"] >= 1
    assert len(data["results"]) == 1


# ---------------------------------------------------------------------------
# Breach check — must fail honestly without a HIBP key
# ---------------------------------------------------------------------------

def test_breach_check_requires_valid_email(client):
    assert client.post("/api/breaches/check",
                       json={"email": "nope"}).status_code == 400


def test_breach_check_without_key_is_honest_400(client):
    resp = client.post("/api/breaches/check",
                       json={"email": "someone@example.com"})
    assert resp.status_code == 400
    assert "API key" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def test_report_generate_validation(client, profile_id):
    assert client.post("/api/reports/generate", json={}).status_code == 400
    assert client.post("/api/reports/generate",
                       json={"profile_id": 999}).status_code == 404
    assert client.post("/api/reports/generate",
                       json={"profile_id": profile_id,
                             "format": "docx"}).status_code == 400


def test_report_generate_and_download_pdf(client, profile_id):
    resp = client.post("/api/reports/generate",
                       json={"profile_id": profile_id, "format": "pdf"})
    url = resp.get_json()["download_url"]
    pdf = client.get(url)
    assert pdf.status_code == 200
    assert pdf.data[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_settings_section_whitelist(client):
    ok = client.post("/api/settings/general",
                     json={"setting_scan_frequency": "weekly"})
    assert ok.status_code == 200
    assert client.post("/api/settings/bogus",
                       json={"x": "y"}).status_code == 404


# ---------------------------------------------------------------------------
# Data export / import / delete
# ---------------------------------------------------------------------------

def test_export_import_round_trip(client, profile_id):
    client.post("/api/optout/whitepages/submit", json={"profile_id": profile_id})
    backup = client.get("/api/data/export").get_json()
    assert backup["profiles"][0]["profile"]["first_name"] == "Test"

    resp = client.post("/api/data/import", json=backup)
    imported = resp.get_json()["imported"]
    assert imported["profiles"] == 1
    assert imported["optouts"] >= 1

    profiles = client.get("/api/profiles").get_json()["profiles"]
    assert len(profiles) == 2  # original + imported copy


def test_import_rejects_garbage(client):
    assert client.post("/api/data/import",
                       json={"nope": True}).status_code == 400


def test_delete_all_requires_typed_confirmation(client, profile_id):
    assert client.delete("/api/data/all",
                         json={"confirm": "yes"}).status_code == 400
    assert client.delete("/api/data/all",
                         json={"confirm": "DELETE"}).status_code == 200
    assert client.get("/api/profiles").get_json()["profiles"] == []


# ---------------------------------------------------------------------------
# Displacement mode
# ---------------------------------------------------------------------------

def test_displacement_toggle(client):
    import models
    assert client.post("/api/displacement/activate").status_code == 200
    assert models.get_setting("displacement_mode") == "1"
    assert client.post("/api/displacement/deactivate").status_code == 200
    assert models.get_setting("displacement_mode") == "0"


# ---------------------------------------------------------------------------
# API key gating — same-origin exemption
# ---------------------------------------------------------------------------

def test_api_key_gates_external_but_not_browser(client):
    import models
    models.set_setting("api_key", "sekret")
    try:
        # No key, no browser header → blocked
        assert client.get("/api/profiles").status_code == 401
        # Browser same-origin request → allowed
        assert client.get(
            "/api/profiles",
            headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
        # Correct key → allowed
        assert client.get(
            "/api/profiles",
            headers={"X-API-Key": "sekret"}).status_code == 200
    finally:
        models.set_setting("api_key", "")
