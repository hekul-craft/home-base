import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app, next_anniversary

TODAY = date(2026, 10, 6)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def client(db_path):
    c = TestClient(create_app(db_path, today=lambda: TODAY), follow_redirects=False)
    r = c.post("/setup", data={"name": "Pat Agent", "email": "pat@example.com",
                               "password": "secret123", "business_name": "Pat Realty"})
    assert r.status_code == 303
    return c


def add_vendor(client, **overrides):
    data = {"name": "Maria Lopez", "category": "Plumbing", "phone": "555-1000",
            "client_visible": "on", "notes": "SECRET-INTERNAL-NOTE", **overrides}
    r = client.post("/vendors/new", data=data)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def add_client(client, **overrides):
    data = {"name": "Morgan Lee", "client_type": "renter", **overrides}
    r = client.post("/clients/new", data=data)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def token_for(db_path, client_id):
    return sqlite3.connect(db_path).execute(
        "SELECT portal_token FROM clients WHERE id=?", (client_id,)).fetchone()[0]


def test_staff_pages_require_login(db_path):
    c = TestClient(create_app(db_path), follow_redirects=False)
    assert c.get("/vendors").headers["location"] == "/login"
    assert c.get("/login").headers["location"] == "/setup"  # no accounts yet


def test_setup_only_once_and_login(client):
    assert client.get("/setup").headers["location"] == "/login"
    client.post("/logout")
    assert client.post("/login", data={"email": "pat@example.com", "password": "nope"}).status_code == 400
    assert client.post("/login", data={"email": "PAT@example.com", "password": "secret123"}).status_code == 303
    assert client.get("/").status_code == 200


def test_vendor_crud_and_validation(client):
    assert client.post("/vendors/new", data={"name": "", "category": "Plumbing"}).status_code == 400
    assert client.post("/vendors/new", data={"name": "X", "category": "Nope"}).status_code == 400
    vid = add_vendor(client, website="lopez.example")
    page = client.get(f"/vendors/{vid}").text
    assert "https://lopez.example" in page and "SECRET-INTERNAL-NOTE" in page
    client.post(f"/vendors/{vid}/edit", data={"name": "Maria L.", "category": "HVAC"})
    assert "Maria L." in client.get("/vendors?category=HVAC").text
    client.post(f"/vendors/{vid}/delete")
    assert client.get(f"/vendors/{vid}").status_code == 404


def test_portal_shows_only_client_visible_vendors_without_notes(client, db_path):
    add_vendor(client, name="Public Plumber")
    add_vendor(client, name="Internal Handyman", category="Handyman", client_visible="")
    cid = add_client(client, client_type="buyer", key_date="2020-01-01")
    page = TestClient(client.app).get(f"/p/{token_for(db_path, cid)}").text  # no staff session
    assert "Public Plumber" in page
    assert "Internal Handyman" not in page
    assert "SECRET-INTERNAL-NOTE" not in page
    assert 'id="path"' not in page and 'id="maintenance"' not in page  # renter-only sections


def test_portal_referral_flow(client, db_path):
    vid = add_vendor(client)
    hidden = add_vendor(client, name="Hidden", client_visible="")
    cid = add_client(client, client_type="buyer")
    portal = TestClient(client.app, follow_redirects=False)
    tok = token_for(db_path, cid)
    assert portal.post(f"/p/{tok}/connect", data={"vendor_id": hidden}).status_code == 404
    assert portal.post(f"/p/{tok}/connect", data={"vendor_id": vid, "message": "Leaky tap"}).status_code == 303
    dash = client.get("/").text
    assert "Leaky tap" in dash and "Morgan Lee" in dash


def test_renter_maintenance_request_and_assignment(client, db_path):
    client.post("/properties/new", data={"address": "412 Maple St"})
    vid = add_vendor(client)
    client.post("/properties/1/call-list", data={"category": "Plumbing", "vendor_id": vid})
    cid = add_client(client, property_id="1", unit="A")
    portal = TestClient(client.app, follow_redirects=False)
    tok = token_for(db_path, cid)
    r = portal.post(f"/p/{tok}/requests", data={"category": "Plumbing", "description": "Sink clogged",
                                                "urgency": "urgent"})
    assert r.status_code == 303
    detail = client.get("/requests/1").text
    assert "Sink clogged" in detail and "Suggested vendor" in detail and "Maria Lopez" in detail
    client.post("/requests/1", data={"status": "new", "vendor_id": vid, "cost": "$1,250.50"})
    row = sqlite3.connect(db_path).execute("SELECT status, vendor_id, cost, property_id FROM service_requests").fetchone()
    assert row == ("assigned", vid, 1250.5, 1)


def test_buyers_cannot_submit_maintenance(client, db_path):
    cid = add_client(client, client_type="buyer")
    portal = TestClient(client.app)
    r = portal.post(f"/p/{token_for(db_path, cid)}/requests",
                    data={"category": "Plumbing", "description": "x"})
    assert r.status_code == 403


def test_readiness_checklist_toggle(client, db_path):
    cid = add_client(client)
    tok = token_for(db_path, cid)
    portal = TestClient(client.app, follow_redirects=False)
    portal.post(f"/p/{tok}/readiness", data={"item_key": "credit", "done": "1"})
    portal.post(f"/p/{tok}/readiness", data={"item_key": "budget", "done": "1"})
    portal.post(f"/p/{tok}/readiness", data={"item_key": "budget", "done": "0"})
    assert portal.post(f"/p/{tok}/readiness", data={"item_key": "bogus", "done": "1"}).status_code == 400
    assert "1/9" in client.get(f"/clients/{cid}").text


def test_regenerating_link_revokes_old_one(client, db_path):
    cid = add_client(client)
    old = token_for(db_path, cid)
    client.post(f"/clients/{cid}/new-link")
    portal = TestClient(client.app)
    assert portal.get(f"/p/{old}").status_code == 404
    assert portal.get(f"/p/{token_for(db_path, cid)}").status_code == 200


def test_bad_dates_rejected(client):
    r = client.post("/clients/new", data={"name": "A", "client_type": "buyer", "key_date": "01/02/2020"})
    assert r.status_code == 400


def test_dashboard_anniversary(client):
    add_client(client, name="Rivera Family", client_type="buyer", key_date="2023-10-20")
    page = client.get("/").text
    assert "Rivera Family" in page and "3 years" in page


def test_referral_handled_rejects_offsite_redirect(client, db_path):
    vid = add_vendor(client)
    cid = add_client(client, client_type="buyer")
    TestClient(client.app).post(f"/p/{token_for(db_path, cid)}/connect", data={"vendor_id": vid})
    r = client.post("/referrals/1/handled", data={"next": "//evil.example"})
    assert r.headers["location"] == "/"


def test_next_anniversary():
    assert next_anniversary("2020-10-10", TODAY) == (date(2026, 10, 10), 6)
    assert next_anniversary("2020-01-01", TODAY) == (date(2027, 1, 1), 7)
    assert next_anniversary("2026-09-01", TODAY) == (date(2027, 9, 1), 1)
    assert next_anniversary("2020-02-29", date(2026, 2, 1)) == (date(2026, 3, 1), 6)
    assert next_anniversary("", TODAY) is None
