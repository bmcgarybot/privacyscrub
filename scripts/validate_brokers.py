#!/usr/bin/env python3
"""
PrivacyScrub — Broker Database Validator

Validates brokers.json against the schema the application code actually
depends on. Run after any edit to the broker database:

    python3 scripts/validate_brokers.py                  # validate, exit 1 on errors
    python3 scripts/validate_brokers.py --stats          # also print a data summary
    python3 scripts/validate_brokers.py --check-links    # probe all opt-out URLs
    python3 scripts/validate_brokers.py --check-links --ids mylife,spokeo
    python3 scripts/validate_brokers.py --check-links --limit 50 --workers 8

Errors are problems that will break or mislead the app (missing required
fields, duplicate ids, bad references, auto_removable brokers that can't
actually be auto-submitted). Warnings are data-quality issues worth a look
but safe to ship.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

BROKERS_PATH = Path(__file__).resolve().parent.parent / "brokers.json"

REQUIRED_FIELDS = {
    "id": str, "name": str, "url": str, "opt_out_url": str,
    "method": str, "difficulty": str, "time_min": int,
    "processing_days": int, "verification": str, "reappear_months": int,
    "tier": int, "category": str, "data_types": list,
    "auto_removable": bool, "process_type": str,
}

# Enums the application logic branches on
VALID_METHODS = {"form", "email", "phone", "mail", "api", "account"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_VERIFICATIONS = {"none", "email", "phone", "id"}
VALID_PROCESS_TYPES = {"email-only", "form-required", "search-first",
                       "mixed", "phone-only"}
VALID_TIERS = {1, 2, 3, 4}

URL_RE = re.compile(r"^https?://[^\s]+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate(brokers: list[dict]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    ids = [b.get("id") for b in brokers]
    for dup, n in Counter(ids).items():
        if n > 1:
            errors.append(f"Duplicate broker id: '{dup}' appears {n} times")

    id_set = set(ids)
    name_counts = Counter(b.get("name", "").strip().lower() for b in brokers)

    for b in brokers:
        bid = b.get("id", "<missing id>")
        where = f"[{bid}]"

        # Required fields + types
        for field, ftype in REQUIRED_FIELDS.items():
            if field not in b:
                errors.append(f"{where} missing required field '{field}'")
            elif not isinstance(b[field], ftype):
                errors.append(
                    f"{where} field '{field}' should be {ftype.__name__}, "
                    f"got {type(b[field]).__name__}")

        # Enums
        if b.get("method") not in VALID_METHODS:
            errors.append(f"{where} invalid method: {b.get('method')!r}")
        if b.get("difficulty") not in VALID_DIFFICULTIES:
            errors.append(f"{where} invalid difficulty: {b.get('difficulty')!r}")
        if b.get("verification") not in VALID_VERIFICATIONS:
            errors.append(f"{where} invalid verification: {b.get('verification')!r}")
        if b.get("process_type") not in VALID_PROCESS_TYPES:
            errors.append(f"{where} invalid process_type: {b.get('process_type')!r}")
        if b.get("tier") not in VALID_TIERS:
            errors.append(f"{where} invalid tier: {b.get('tier')!r}")

        # URLs
        if b.get("url") and not URL_RE.match(b["url"]):
            errors.append(f"{where} malformed url: {b['url']!r}")
        if b.get("opt_out_url") and not URL_RE.match(b["opt_out_url"]):
            errors.append(f"{where} malformed opt_out_url: {b['opt_out_url']!r}")

        # privacy_email shape (empty is allowed unless auto_removable)
        email = b.get("privacy_email", "")
        if email and not EMAIL_RE.match(email):
            errors.append(f"{where} malformed privacy_email: {email!r}")

        # Auto-removal contract: EmailAutoSubmitter requires a privacy_email.
        if b.get("auto_removable"):
            if not email:
                errors.append(
                    f"{where} auto_removable=true but no privacy_email — "
                    "auto-submission would fail for this broker")

        # Reference integrity
        parent = b.get("parent")
        if parent and parent not in id_set:
            warnings.append(f"{where} parent '{parent}' is not a broker id")
        for sibling in b.get("network") or []:
            if sibling not in id_set:
                warnings.append(f"{where} network entry '{sibling}' is not a broker id")

        # Ranges
        if isinstance(b.get("time_min"), int) and b["time_min"] <= 0:
            warnings.append(f"{where} time_min should be positive, got {b['time_min']}")
        if isinstance(b.get("processing_days"), int) and b["processing_days"] < 0:
            errors.append(f"{where} negative processing_days: {b['processing_days']}")
        if isinstance(b.get("reappear_months"), int) and b["reappear_months"] < 0:
            errors.append(f"{where} negative reappear_months: {b['reappear_months']}")

        # Duplicate display names (legit for regional variants — warn only)
        if name_counts[b.get("name", "").strip().lower()] > 1:
            warnings.append(f"{where} duplicate display name: {b.get('name')!r}")

        # Missing opt-out path entirely
        if not b.get("opt_out_url") and not email:
            warnings.append(
                f"{where} has neither opt_out_url nor privacy_email — "
                "users have no removal path")

    return errors, warnings


def print_stats(brokers: list[dict]) -> None:
    print(f"\n📊 {len(brokers)} brokers")
    for field in ("category", "tier", "difficulty", "process_type"):
        counts = Counter(b.get(field) for b in brokers)
        pretty = ", ".join(f"{k}: {v}" for k, v in sorted(
            counts.items(), key=lambda kv: -kv[1]))
        print(f"   {field:<13} {pretty}")
    auto = sum(1 for b in brokers if b.get("auto_removable"))
    print(f"   auto-removable {auto} ({auto * 100 // len(brokers)}%)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate brokers.json")
    parser.add_argument("--stats", action="store_true", help="print data summary")
    parser.add_argument("--path", default=str(BROKERS_PATH),
                        help="path to brokers.json")
    parser.add_argument("--check-links", action="store_true",
                        help="probe every opt_out_url over HTTP (network required)")
    parser.add_argument("--workers", type=int, default=16,
                        help="concurrent probes for --check-links (default 16)")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="per-request timeout seconds (default 15)")
    parser.add_argument("--limit", type=int, default=None,
                        help="only probe the first N brokers (for quick passes)")
    parser.add_argument("--ids", default="",
                        help="comma-separated broker ids to probe (e.g. mylife,spokeo)")
    args = parser.parse_args()

    try:
        brokers = json.loads(Path(args.path).read_text())
    except FileNotFoundError:
        print(f"❌ Not found: {args.path}")
        return 1
    except json.JSONDecodeError as e:
        print(f"❌ brokers.json is not valid JSON: {e}")
        return 1

    if not isinstance(brokers, list):
        print("❌ brokers.json must be a JSON array")
        return 1

    errors, warnings = validate(brokers)

    for w in warnings:
        print(f"⚠️  {w}")
    for e in errors:
        print(f"❌ {e}")

    if args.stats:
        print_stats(brokers)

    print(f"\n{'❌' if errors else '✅'} {len(brokers)} brokers — "
          f"{len(errors)} error(s), {len(warnings)} warning(s)")

    if args.check_links:
        link_exit = run_link_check(brokers, args)
        return 1 if errors else link_exit

    return 1 if errors else 0




# ---------------------------------------------------------------------------
# Link checking (--check-links)
# ---------------------------------------------------------------------------
#
# Probes every broker's opt_out_url over HTTP and classifies the outcome.
# Data-broker sites are hostile to automation, so classification is
# deliberately conservative:
#
#   OK      2xx/3xx — the opt-out page answers
#   DEAD    404/410 — the page is gone (this is what breaks users)
#   BLOCKED 401/403/405/429/999 — bot wall; page is probably fine in a browser
#   ERROR   5xx, timeouts, DNS/SSL failures — can't tell; retry later
#
# Only DEAD links fail the run (exit 1). BLOCKED/ERROR are reported for
# human follow-up. Run from a residential connection for best signal —
# data-center IPs get blocked far more often.

CHECK_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36")

BLOCKED_CODES = {401, 403, 405, 406, 418, 429, 999}


def _classify(status: int | None, error: str | None) -> str:
    if error is not None:
        return "ERROR"
    if status in (404, 410):
        return "DEAD"
    if status in BLOCKED_CODES:
        return "BLOCKED"
    if status is not None and 200 <= status < 400:
        return "OK"
    return "ERROR"


def _probe(session, url: str, timeout: float) -> tuple[int | None, str | None]:
    """HEAD first (cheap), falling back to GET — many sites reject HEAD."""
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code in (405, 501) or resp.status_code >= 400:
            resp = session.get(url, timeout=timeout, allow_redirects=True,
                               stream=True)
            resp.close()
        return resp.status_code, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def check_links(brokers: list[dict], workers: int = 16,
                timeout: float = 15.0,
                only_ids: set[str] | None = None,
                limit: int | None = None) -> dict:
    """Probe opt_out_urls concurrently. Returns classification buckets."""
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    targets = [b for b in brokers if b.get("opt_out_url")]
    if only_ids:
        targets = [b for b in targets if b["id"] in only_ids]
    if limit:
        targets = targets[:limit]

    session = requests.Session()
    session.headers.update({
        "User-Agent": CHECK_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    buckets: dict[str, list[dict]] = {
        "OK": [], "DEAD": [], "BLOCKED": [], "ERROR": []}

    def _work(broker):
        status, error = _probe(session, broker["opt_out_url"], timeout)
        return broker, status, error

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, b) for b in targets]
        done = 0
        for future in as_completed(futures):
            broker, status, error = future.result()
            verdict = _classify(status, error)
            buckets[verdict].append({
                "id": broker["id"], "name": broker["name"],
                "url": broker["opt_out_url"],
                "status": status, "error": error,
            })
            done += 1
            if done % 25 == 0 or done == len(targets):
                print(f"   … {done}/{len(targets)} checked", flush=True)

    return buckets


def run_link_check(brokers: list[dict], args) -> int:
    only_ids = set(args.ids.split(",")) if args.ids else None
    print(f"🔗 Probing opt-out URLs "
          f"({args.workers} workers, {args.timeout:.0f}s timeout)…")
    buckets = check_links(brokers, workers=args.workers,
                          timeout=args.timeout, only_ids=only_ids,
                          limit=args.limit)

    for verdict, icon in (("DEAD", "❌"), ("BLOCKED", "🧱"), ("ERROR", "⚠️ ")):
        for item in sorted(buckets[verdict], key=lambda i: i["id"]):
            detail = (f"HTTP {item['status']}" if item["status"] is not None
                      else item["error"])
            print(f"{icon} {verdict:<8} [{item['id']}] {item['url']} — {detail}")

    total = sum(len(v) for v in buckets.values())
    print(f"\n{'❌' if buckets['DEAD'] else '✅'} {total} links checked — "
          f"{len(buckets['OK'])} ok, {len(buckets['DEAD'])} dead, "
          f"{len(buckets['BLOCKED'])} blocked (probably fine in a browser), "
          f"{len(buckets['ERROR'])} errors")
    if buckets["BLOCKED"] or buckets["ERROR"]:
        print("   Blocked/error links need a human check — "
              "data-broker sites aggressively wall off automated clients.")
    return 1 if buckets["DEAD"] else 0


if __name__ == "__main__":
    sys.exit(main())
