"""Load demo data so you can click around: python -m app.seed

Creates a demo login (demo@homebase.test / homebase123) plus sample vendors,
clients and a managed property. Dates are relative to today so the dashboard
always has upcoming anniversaries and lease endings to show.
"""

import os
import sys
from datetime import date, timedelta

from . import db
from .auth import hash_password
from .main import DEFAULT_DB

VENDORS = [
    # name, company, category, phone, email, website, area, preferred, rating, visible, perk
    ("Maria Lopez", "Lopez Plumbing & Drain", "Plumbing", "555-201-3344", "maria@lopezplumbing.example", "https://lopezplumbing.example", "Citywide", 1, 5, 1, "Free drain inspection for our clients"),
    ("Dan Whitfield", "Whitfield Heating & Air", "HVAC", "555-201-7788", "service@whitfieldhvac.example", "", "North & East side", 1, 5, 1, "$50 off first tune-up"),
    ("Priya Natarajan", "Bright Spark Electric", "Electrical", "555-201-9012", "priya@brightspark.example", "", "Citywide", 0, 4, 1, ""),
    ("Sam Okafor", "Okafor Roofing", "Roofing", "555-201-4455", "", "", "Metro area", 1, 5, 1, ""),
    ("Jenna Park", "Summit Home Lending", "Mortgage Lender", "555-201-6600", "jenna@summitlending.example", "https://summitlending.example", "Statewide", 1, 5, 1, "Free pre-approval consult"),
    ("Chris Bell", "Clear View Inspections", "Home Inspection", "555-201-2121", "chris@clearview.example", "", "Metro area", 0, 4, 1, ""),
    ("Tony Russo", "Russo Handyman Services", "Handyman", "555-201-5050", "", "", "Downtown", 0, 4, 0, ""),
    ("Green Leaf Landscaping", "", "Landscaping", "555-201-8080", "hello@greenleaf.example", "", "South side", 0, 3, 1, "10% off spring cleanup"),
]


def seed(path: str) -> None:
    db.init_db(path)
    today = date.today()
    with db.session(path) as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            sys.exit("Database already has an account; seed only runs on a fresh database.")
        conn.execute("INSERT INTO users (name, email, password_hash) VALUES (?,?,?)",
                     ("Demo Agent", "demo@homebase.test", hash_password("homebase123")))
        for key, value in {"business_name": "Hometown Realty Group", "agent_name": "Demo Agent",
                           "agent_phone": "555-201-0000", "agent_email": "demo@homebase.test"}.items():
            conn.execute("UPDATE settings SET value=? WHERE key=?", (value, key))

        ids = {}
        for v in VENDORS:
            cur = conn.execute(
                """INSERT INTO vendors (name, company, category, phone, email, website, service_area,
                   is_preferred, rating, client_visible, client_perk, insured) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""", v)
            ids[v[2]] = cur.lastrowid

        prop = conn.execute(
            "INSERT INTO properties (name, address, owner_name, unit_count, notes) VALUES (?,?,?,?,?)",
            ("Maple Street Duplex", "412 Maple St", "R. Chen", 2, "Water shut-off in garage, left wall.")).lastrowid
        for cat in ("Plumbing", "HVAC", "Electrical", "Handyman"):
            conn.execute("INSERT INTO property_vendors VALUES (?,?,?)", (prop, cat, ids[cat]))

        def add_client(**c):
            c["portal_token"] = db.new_portal_token()
            cols = ", ".join(c)
            return conn.execute(f"INSERT INTO clients ({cols}) VALUES ({', '.join('?' * len(c))})",
                                list(c.values())).lastrowid

        add_client(name="Alex & Jordan Rivera", email="rivera@example.com", phone="555-301-1111",
                   client_type="buyer", address="88 Birch Lane",
                   key_date=(today + timedelta(days=12)).replace(year=today.year - 3).isoformat())
        add_client(name="Taylor Brooks", email="taylor@example.com", client_type="buyer",
                   address="9 Harbor View Ct", key_date=(today - timedelta(days=200)).isoformat())
        renter = add_client(name="Morgan Lee", email="morgan@example.com", phone="555-301-2222",
                            client_type="renter", address="412 Maple St", property_id=prop, unit="A",
                            key_date=(today - timedelta(days=300)).isoformat(),
                            lease_end=(today + timedelta(days=65)).isoformat())
        for key in ("credit", "budget", "savings"):
            conn.execute("INSERT INTO readiness_progress (client_id, item_key) VALUES (?,?)", (renter, key))
        conn.execute(
            """INSERT INTO service_requests (client_id, property_id, category, description, urgency, source)
               VALUES (?,?,?,?,?, 'portal')""",
            (renter, prop, "Plumbing", "Kitchen sink is draining very slowly.", "normal"))
        conn.execute("INSERT INTO referrals (client_id, vendor_id, message) VALUES (?,?,?)",
                     (renter, ids["Mortgage Lender"], "Would love to know what I could afford."))
    print("Demo data loaded. Sign in with demo@homebase.test / homebase123")


if __name__ == "__main__":
    seed(os.environ.get("HOMEBASE_DB", DEFAULT_DB))
