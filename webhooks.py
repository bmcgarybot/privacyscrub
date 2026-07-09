"""
PrivacyScrub — Webhook Dispatch

Delivers event notifications to registered webhooks (the /api/webhooks
registry). Deliveries run on a daemon thread and never block or raise
into application flows; failures are logged and recorded on the webhook
row (last_status / failure_count).

Payloads match the documented shape:

    {
        "event": "scan.complete",
        "timestamp": "2026-01-15T14:30:00+00:00",
        "data": { ... }
    }

If a webhook_secret is configured in settings, deliveries carry an
X-PrivacyScrub-Signature header: sha256 hex of the sorted-key JSON body
concatenated with the secret — the same scheme as the legacy single
webhook_url setting, so receivers verify both identically.

Events:
    scan.complete     — a broker scan finished
    optout.submitted  — an opt-out was auto-submitted (or drafted)
    optout.confirmed  — an opt-out was marked confirmed
    breach.found      — a breach scan found new breaches
"""

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone

import requests

from models import get_setting, get_webhooks, record_webhook_result

logger = logging.getLogger("privacyscrub.webhooks")

KNOWN_EVENTS = {
    "scan.complete",
    "optout.submitted",
    "optout.confirmed",
    "breach.found",
}

_TIMEOUT = 10  # seconds per delivery attempt


def _signed_headers(payload: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    secret = get_setting("webhook_secret", "")
    if secret:
        sig = hashlib.sha256(
            (json.dumps(payload, sort_keys=True) + secret).encode()
        ).hexdigest()
        headers["X-PrivacyScrub-Signature"] = sig
    return headers


def _deliver(hook: dict, payload: dict) -> None:
    try:
        resp = requests.post(
            hook["url"], json=payload,
            headers=_signed_headers(payload), timeout=_TIMEOUT,
        )
        ok = 200 <= resp.status_code < 300
        record_webhook_result(
            hook["id"], ok,
            "" if ok else f"HTTP {resp.status_code}")
        if ok:
            logger.info("Webhook %s delivered %s", hook["id"], payload["event"])
        else:
            logger.warning("Webhook %s returned HTTP %s for %s",
                           hook["id"], resp.status_code, payload["event"])
    except Exception as e:
        record_webhook_result(hook["id"], False, str(e))
        logger.warning("Webhook %s delivery failed: %s", hook["id"], e)


def dispatch(event: str, data: dict) -> int:
    """
    Fire `event` to every active webhook subscribed to it.

    Returns the number of webhooks targeted. Delivery happens on a
    daemon thread; this function returns immediately and never raises.
    """
    if event not in KNOWN_EVENTS:
        logger.error("Unknown webhook event: %s", event)
        return 0

    try:
        hooks = [
            h for h in get_webhooks(active_only=True)
            if event in (h.get("events") or [])
        ]
    except Exception as e:  # DB unavailable — never break the caller
        logger.error("Webhook lookup failed: %s", e)
        return 0

    if not hooks:
        return 0

    payload = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    def _run():
        for hook in hooks:
            _deliver(hook, payload)

    threading.Thread(target=_run, daemon=True,
                     name=f"webhook-{event}").start()
    return len(hooks)


def send_test(hook: dict) -> dict:
    """
    Synchronously send a test event to one webhook and return the result.
    Used by POST /api/webhooks/<id>/test so the caller gets real feedback.
    """
    payload = {
        "event": "webhook.test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"webhook_id": hook["id"], "message": "PrivacyScrub test delivery"},
    }
    try:
        resp = requests.post(
            hook["url"], json=payload,
            headers=_signed_headers(payload), timeout=_TIMEOUT,
        )
        ok = 200 <= resp.status_code < 300
        record_webhook_result(hook["id"], ok,
                              "" if ok else f"HTTP {resp.status_code}")
        return {"delivered": ok, "http_status": resp.status_code}
    except Exception as e:
        record_webhook_result(hook["id"], False, str(e))
        return {"delivered": False, "error": str(e)}
