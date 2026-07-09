"""Webhook registry and dispatch tests with a real local receiver."""

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


@pytest.fixture()
def receiver():
    """A local HTTP server that records every delivery it receives."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append({
                "sig": self.headers.get("X-PrivacyScrub-Signature", ""),
                "body": json.loads(self.rfile.read(length) or b"{}"),
            })
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}/hook",
           "received": received}
    server.shutdown()


def _wait_for(received, count, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(received) >= count:
            return True
        time.sleep(0.05)
    return False


def test_register_validation(client):
    assert client.post("/api/webhooks", json={
        "url": "ftp://x", "events": ["scan.complete"]}).status_code == 400
    resp = client.post("/api/webhooks", json={
        "url": "http://127.0.0.1:1/x", "events": ["nope.event"]})
    assert resp.status_code == 400
    assert "Unknown event" in resp.get_json()["error"]
    assert client.post("/api/webhooks", json={
        "url": "http://127.0.0.1:1/x", "events": []}).status_code == 400


def test_register_list_delete(client):
    resp = client.post("/api/webhooks", json={
        "url": "http://127.0.0.1:1/x", "events": ["scan.complete"]})
    assert resp.status_code == 201
    hook_id = resp.get_json()["webhook"]["id"]

    listing = client.get("/api/webhooks").get_json()
    assert listing["count"] == 1
    assert listing["webhooks"][0]["events"] == ["scan.complete"]

    assert client.delete(f"/api/webhooks/{hook_id}").status_code == 200
    assert client.delete(f"/api/webhooks/{hook_id}").status_code == 404
    assert client.get("/api/webhooks").get_json()["count"] == 0


def test_dispatch_delivers_signed_payload(client, receiver):
    import models
    from webhooks import dispatch

    models.set_setting("webhook_secret", "testsecret")
    client.post("/api/webhooks", json={
        "url": receiver["url"], "events": ["scan.complete"]})

    n = dispatch("scan.complete", {"scan_id": "t-1", "found": 3})
    assert n == 1
    assert _wait_for(receiver["received"], 1)

    delivery = receiver["received"][0]
    assert delivery["body"]["event"] == "scan.complete"
    assert delivery["body"]["data"]["found"] == 3
    expected = hashlib.sha256(
        (json.dumps(delivery["body"], sort_keys=True) + "testsecret").encode()
    ).hexdigest()
    assert delivery["sig"] == expected


def test_dispatch_respects_subscriptions_and_active(client, receiver):
    from webhooks import dispatch

    client.post("/api/webhooks", json={
        "url": receiver["url"], "events": ["breach.found"]})
    client.post("/api/webhooks", json={
        "url": receiver["url"], "events": ["scan.complete"], "active": False})

    assert dispatch("scan.complete", {}) == 0   # only inactive hook subscribed
    assert dispatch("optout.confirmed", {}) == 0  # nobody subscribed
    assert dispatch("breach.found", {"new_breaches": 1}) == 1
    assert _wait_for(receiver["received"], 1)
    assert receiver["received"][0]["body"]["event"] == "breach.found"


def test_dispatch_unknown_event_is_rejected(client):
    from webhooks import dispatch
    assert dispatch("not.an.event", {}) == 0


def test_test_endpoint_and_failure_tracking(client, receiver):
    good = client.post("/api/webhooks", json={
        "url": receiver["url"], "events": ["scan.complete"]}).get_json()["webhook"]["id"]
    dead = client.post("/api/webhooks", json={
        "url": "http://127.0.0.1:1/nope", "events": ["scan.complete"]}).get_json()["webhook"]["id"]

    ok = client.post(f"/api/webhooks/{good}/test").get_json()["test"]
    assert ok["delivered"] is True

    bad = client.post(f"/api/webhooks/{dead}/test").get_json()["test"]
    assert bad["delivered"] is False

    hooks = {h["id"]: h for h in client.get("/api/webhooks").get_json()["webhooks"]}
    assert hooks[good]["last_status"] == "ok"
    assert hooks[dead]["failure_count"] >= 1
    assert hooks[dead]["last_status"].startswith("error")
