"""SQLite storage: schema, connection helper and small query helpers.

Plain sqlite3 on purpose - one file on disk, no ORM, every query visible.
"""

import secrets
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Staff accounts: the realtor / property manager and their team.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vendors (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    company        TEXT NOT NULL DEFAULT '',
    category       TEXT NOT NULL,
    phone          TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL DEFAULT '',
    website        TEXT NOT NULL DEFAULT '',
    service_area   TEXT NOT NULL DEFAULT '',
    license_info   TEXT NOT NULL DEFAULT '',
    insured        INTEGER NOT NULL DEFAULT 0,
    rating         INTEGER NOT NULL DEFAULT 0,      -- 0 = unrated, 1-5
    is_preferred   INTEGER NOT NULL DEFAULT 0,
    client_visible INTEGER NOT NULL DEFAULT 1,      -- 0 = internal / property-management only
    client_perk    TEXT NOT NULL DEFAULT '',        -- e.g. "10% off for our clients"
    notes          TEXT NOT NULL DEFAULT '',        -- internal, never shown in the portal
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Managed rental properties.
CREATE TABLE IF NOT EXISTS properties (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    address    TEXT NOT NULL,
    owner_name TEXT NOT NULL DEFAULT '',
    unit_count INTEGER NOT NULL DEFAULT 1,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which vendor to call for each kind of job at a given property.
CREATE TABLE IF NOT EXISTS property_vendors (
    property_id INTEGER NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    vendor_id   INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    PRIMARY KEY (property_id, category)
);

-- Past buyers, current renters and owners/landlords.
CREATE TABLE IF NOT EXISTS clients (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',
    client_type  TEXT NOT NULL CHECK (client_type IN ('buyer', 'renter', 'owner')),
    address      TEXT NOT NULL DEFAULT '',
    property_id  INTEGER REFERENCES properties(id) ON DELETE SET NULL,  -- renters
    unit         TEXT NOT NULL DEFAULT '',
    key_date     TEXT NOT NULL DEFAULT '',   -- closing date (buyers) or lease start (renters)
    lease_end    TEXT NOT NULL DEFAULT '',
    portal_token TEXT NOT NULL UNIQUE,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Maintenance / service jobs, raised by staff or by a renter in the portal.
CREATE TABLE IF NOT EXISTS service_requests (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    property_id INTEGER REFERENCES properties(id) ON DELETE SET NULL,
    category    TEXT NOT NULL,
    description TEXT NOT NULL,
    urgency     TEXT NOT NULL DEFAULT 'normal' CHECK (urgency IN ('low', 'normal', 'urgent')),
    status      TEXT NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'assigned', 'scheduled', 'completed', 'cancelled')),
    vendor_id   INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
    cost        REAL,
    staff_notes TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'staff' CHECK (source IN ('staff', 'portal')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A client asked, from their portal, to be connected with a vendor.
CREATE TABLE IF NOT EXISTS referrals (
    id         INTEGER PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    vendor_id  INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    message    TEXT NOT NULL DEFAULT '',
    handled    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Renter progress on the "path to homeownership" checklist.
CREATE TABLE IF NOT EXISTS readiness_progress (
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    item_key  TEXT NOT NULL,
    done_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (client_id, item_key)
);
"""

DEFAULT_SETTINGS = {
    "business_name": "HomeBase Realty",
    "agent_name": "",
    "agent_phone": "",
    "agent_email": "",
    "portal_welcome": "Welcome home! Here are the local pros we trust and recommend.",
}


def connect(path: str) -> sqlite3.Connection:
    # One connection per request; FastAPI may open it in a worker thread and
    # use it on the event loop thread, so allow cross-thread use.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(path: str):
    """Open a connection, commit on success, always close."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str) -> None:
    with session(path) as conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )
        # Session-signing key, generated once per installation.
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('secret_key', ?)",
            (secrets.token_urlsafe(32),),
        )


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM settings WHERE key != 'secret_key'")
    return {r["key"]: r["value"] for r in rows}


def new_portal_token() -> str:
    return secrets.token_urlsafe(16)
