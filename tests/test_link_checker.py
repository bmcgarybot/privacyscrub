"""Link-checker classification and probe mechanics against a local server."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate_brokers import _classify, check_links  # noqa: E402


# ---------------------------------------------------------------------------
# Classification table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,error,expected", [
    (200, None, "OK"),
    (301, None, "OK"),          # redirects followed; 3xx terminal still OK
    (404, None, "DEAD"),
    (410, None, "DEAD"),
    (403, None, "BLOCKED"),     # bot wall — probably fine in a browser
    (429, None, "BLOCKED"),
    (999, None, "BLOCKED"),     # LinkedIn-style
    (500, None, "ERROR"),
    (503, None, "ERROR"),
    (None, "ConnectTimeout: x", "ERROR"),
])
def test_classification(status, error, expected):
    assert _classify(status, error) == expected


# ---------------------------------------------------------------------------
# Probe mechanics against a live local server
# ---------------------------------------------------------------------------

@pytest.fixture()
def link_server():
    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            route = self.path.rstrip("/")
            if route == "/ok":
                self.send_response(200)
            elif route == "/gone":
                self.send_response(404)
            elif route == "/walled":
                self.send_response(403)
            elif route == "/broken":
                self.send_response(500)
            elif route == "/head-hostile":
                # Rejects HEAD, accepts GET — checker must fall back
                self.send_response(405 if self.command == "HEAD" else 200)
            else:
                self.send_response(404)
            self.end_headers()

        do_GET = _respond
        do_HEAD = _respond

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _broker(bid, base, route):
    return {"id": bid, "name": bid.title(), "opt_out_url": f"{base}{route}"}


def test_check_links_buckets(link_server):
    brokers = [
        _broker("alive", link_server, "/ok"),
        _broker("dead", link_server, "/gone"),
        _broker("walled", link_server, "/walled"),
        _broker("broken", link_server, "/broken"),
        _broker("head-hostile", link_server, "/head-hostile"),
        _broker("unreachable", "http://127.0.0.1:1", "/x"),
        {"id": "no-url", "name": "No URL", "opt_out_url": ""},  # skipped
    ]
    buckets = check_links(brokers, workers=4, timeout=5)

    ids = {v: {i["id"] for i in items} for v, items in buckets.items()}
    assert ids["OK"] == {"alive", "head-hostile"}  # HEAD 405 → GET fallback
    assert ids["DEAD"] == {"dead"}
    assert ids["BLOCKED"] == {"walled"}
    assert ids["ERROR"] == {"broken", "unreachable"}
    assert sum(len(v) for v in buckets.values()) == 6  # no-url skipped


def test_ids_and_limit_filters(link_server):
    brokers = [
        _broker("one", link_server, "/ok"),
        _broker("two", link_server, "/ok"),
        _broker("three", link_server, "/ok"),
    ]
    only = check_links(brokers, workers=2, timeout=5, only_ids={"two"})
    assert [i["id"] for i in only["OK"]] == ["two"]

    limited = check_links(brokers, workers=2, timeout=5, limit=2)
    assert sum(len(v) for v in limited.values()) == 2
