"""
PrivacyScrub test fixtures.

Every test gets a fresh, isolated SQLite database (models.DB_PATH is
patched to a per-test temp file) and a Flask test client.
"""

import sys
from pathlib import Path

import pytest

# Make the application importable when running `pytest` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client bound to a fresh temp database."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(models, "DB_PATH", str(db_file))
    models.init_db()

    import app as app_module
    flask_app = app_module.create_app()
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


@pytest.fixture()
def profile_id(client):
    """A profile created through the real form endpoint."""
    resp = client.post("/profiles/add", data={
        "first_name": "Test", "last_name": "User",
        "emails": "test@example.com", "city": "Springfield", "state": "IL",
    })
    assert resp.status_code in (200, 302)
    data = client.get("/api/profiles").get_json()
    return data["profiles"][0]["id"]
