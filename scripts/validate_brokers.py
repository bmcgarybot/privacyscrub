#!/usr/bin/env python3
"""
PrivacyScrub — Broker Database Validator

Validates brokers.json against the schema the application code actually
depends on. Run after any edit to the broker database:

    python3 scripts/validate_brokers.py            # validate, exit 1 on errors
    python3 scripts/validate_brokers.py --stats    # also print a data summary

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
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
