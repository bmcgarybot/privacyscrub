"""The shipped broker database must always validate cleanly."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from validate_brokers import validate  # noqa: E402


def _load():
    return json.loads((ROOT / "brokers.json").read_text())


def test_broker_database_has_no_errors():
    errors, _warnings = validate(_load())
    assert errors == [], "\n".join(errors)


def test_broker_count_matches_marketing():
    # README advertises an 807+ broker database — don't let it shrink silently.
    assert len(_load()) >= 807


def test_auto_removable_brokers_can_actually_be_auto_removed():
    for broker in _load():
        if broker.get("auto_removable"):
            assert broker.get("privacy_email"), broker["id"]
