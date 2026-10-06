"""HomeBase: a trusted-vendor directory and client portal for realtors and
property managers.

Two sides:
  * Staff app (login required) - manage vendors, clients, managed properties,
    service requests and referral requests.
  * Client portal (/p/<token>) - a private, no-password link per client showing
    the recommended vendors, seasonal home-care tips, and for renters a
    maintenance request form and a path-to-homeownership checklist.
"""

import os
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db
from .auth import hash_password, verify_password
from .content import (
    CLIENT_TYPES,
    OPEN_STATUSES,
    READINESS_KEYS,
    READINESS_STEPS,
    REQUEST_STATUSES,
    URGENCIES,
    VENDOR_CATEGORIES,
    season_for_month,
)

APP_DIR = Path(__file__).parent
DEFAULT_DB = str(APP_DIR.parent / "homebase.db")

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


templates.env.filters["slug"] = slug


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def redirect(url: str, flash: str | None = None, request: Request | None = None):
    if flash and request is not None:
        request.session["flash"] = flash
    return RedirectResponse(url, status_code=303)


def login_redirect():
    raise HTTPException(status_code=303, headers={"Location": "/login"})


def parse_date(value: str) -> str:
    """Return an ISO date string, or '' for blank. Raises ValueError if malformed."""
    value = (value or "").strip()
    if not value:
        return ""
    return date.fromisoformat(value).isoformat()


def checkbox(form, name: str) -> int:
    return 1 if form.get(name) else 0


def clean(form, name: str) -> str:
    return (form.get(name) or "").strip()


def next_anniversary(key_date: str, today: date) -> tuple[date, int] | None:
    """Next occurrence of a closing-date anniversary and how many years it marks."""
    if not key_date:
        return None
    start = date.fromisoformat(key_date)
    for year in (today.year, today.year + 1):
        try:
            candidate = start.replace(year=year)
        except ValueError:  # Feb 29 in a non-leap year
            candidate = date(year, 3, 1)
        if candidate >= today and year > start.year:
            return candidate, year - start.year
    return None


def create_app(db_path: str | None = None, today=date.today) -> FastAPI:
    db_path = db_path or os.environ.get("HOMEBASE_DB", DEFAULT_DB)
    db.init_db(db_path)
    with db.session(db_path) as conn:
        secret = conn.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()[0]

    app = FastAPI(title="HomeBase", docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax",
                       https_only=os.environ.get("HOMEBASE_HTTPS") == "1")
    app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

    def get_conn():
        with db.session(db_path) as conn:
            yield conn

    def current_user(request: Request, conn=Depends(get_conn)):
        user_id = request.session.get("user_id")
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None
        if user is None:
            login_redirect()
        return user

    def render(request: Request, conn, name: str, status_code: int = 200, **ctx):
        ctx.update(
            settings=db.get_settings(conn),
            flash=request.session.pop("flash", None),
            categories=VENDOR_CATEGORIES,
            client_types=CLIENT_TYPES,
            statuses=REQUEST_STATUSES,
            urgencies=URGENCIES,
        )
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def get_or_404(conn, table: str, item_id: int):
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Not found")
        return row

    # ----------------------------------------------------------------------
    # auth
    # ----------------------------------------------------------------------

    @app.get("/setup")
    def setup_form(request: Request, conn=Depends(get_conn)):
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return redirect("/login")
        return render(request, conn, "setup.html", error=None, form={})

    @app.post("/setup")
    async def setup(request: Request, conn=Depends(get_conn)):
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return redirect("/login")
        form = await request.form()
        name, email, password = clean(form, "name"), clean(form, "email"), form.get("password") or ""
        if not name or "@" not in email or len(password) < 8:
            return render(request, conn, "setup.html", status_code=400, form=form,
                          error="Enter your name, a valid email and a password of 8+ characters.")
        cur = conn.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
            (name, email, hash_password(password)),
        )
        for key in ("business_name", "agent_phone"):
            if clean(form, key):
                conn.execute("UPDATE settings SET value=? WHERE key=?", (clean(form, key), key))
        conn.execute("UPDATE settings SET value=? WHERE key='agent_name'", (name,))
        conn.execute("UPDATE settings SET value=? WHERE key='agent_email'", (email,))
        request.session["user_id"] = cur.lastrowid
        return redirect("/", "Your account is ready. Start by adding a few trusted vendors.", request)

    @app.get("/login")
    def login_form(request: Request, conn=Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return redirect("/setup")
        return render(request, conn, "login.html", error=None, email="")

    @app.post("/login")
    async def login(request: Request, conn=Depends(get_conn)):
        form = await request.form()
        email = clean(form, "email")
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user is None or not verify_password(form.get("password") or "", user["password_hash"]):
            return render(request, conn, "login.html", status_code=400, email=email,
                          error="Email or password is incorrect.")
        request.session.clear()
        request.session["user_id"] = user["id"]
        return redirect("/")

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return redirect("/login")

    # ----------------------------------------------------------------------
    # dashboard
    # ----------------------------------------------------------------------

    @app.get("/")
    def dashboard(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        now = today()
        counts = {
            "vendors": conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0],
            "clients": conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
            "properties": conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0],
            "open_requests": conn.execute(
                f"SELECT COUNT(*) FROM service_requests WHERE status IN {OPEN_STATUSES}"
            ).fetchone()[0],
        }
        open_requests = conn.execute(
            f"""SELECT r.*, c.name AS client_name, p.address AS property_address, v.name AS vendor_name
                FROM service_requests r
                LEFT JOIN clients c ON c.id = r.client_id
                LEFT JOIN properties p ON p.id = r.property_id
                LEFT JOIN vendors v ON v.id = r.vendor_id
                WHERE r.status IN {OPEN_STATUSES}
                ORDER BY r.urgency = 'urgent' DESC, r.created_at LIMIT 10"""
        ).fetchall()
        referrals = conn.execute(
            """SELECT f.*, c.name AS client_name, c.id AS cid, v.name AS vendor_name, v.category
               FROM referrals f JOIN clients c ON c.id = f.client_id JOIN vendors v ON v.id = f.vendor_id
               WHERE f.handled = 0 ORDER BY f.created_at DESC LIMIT 10"""
        ).fetchall()

        anniversaries = []
        for c in conn.execute("SELECT * FROM clients WHERE client_type='buyer' AND key_date != ''"):
            nxt = next_anniversary(c["key_date"], now)
            if nxt and (nxt[0] - now).days <= 60:
                anniversaries.append({"client": c, "date": nxt[0], "years": nxt[1],
                                      "days": (nxt[0] - now).days})
        anniversaries.sort(key=lambda a: a["date"])

        lease_endings = []
        for c in conn.execute("SELECT * FROM clients WHERE client_type='renter' AND lease_end != ''"):
            days = (date.fromisoformat(c["lease_end"]) - now).days
            if 0 <= days <= 120:
                lease_endings.append({"client": c, "days": days})
        lease_endings.sort(key=lambda x: x["days"])

        renters = conn.execute(
            """SELECT c.id, c.name, COUNT(p.item_key) AS done FROM clients c
               LEFT JOIN readiness_progress p ON p.client_id = c.id
               WHERE c.client_type = 'renter' GROUP BY c.id ORDER BY done DESC, c.name LIMIT 8"""
        ).fetchall()

        return render(request, conn, "dashboard.html", user=user, counts=counts,
                      open_requests=open_requests, referrals=referrals,
                      anniversaries=anniversaries, lease_endings=lease_endings,
                      renters=renters, total_steps=len(READINESS_STEPS))

    @app.post("/referrals/{ref_id}/handled")
    async def referral_handled(ref_id: int, request: Request, user=Depends(current_user),
                               conn=Depends(get_conn)):
        conn.execute("UPDATE referrals SET handled=1 WHERE id=?", (ref_id,))
        next_url = clean(await request.form(), "next")
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        return redirect(next_url, "Referral marked as handled.", request)

    # ----------------------------------------------------------------------
    # vendors
    # ----------------------------------------------------------------------

    VENDOR_TEXT = ("name", "company", "category", "phone", "email", "website",
                   "service_area", "license_info", "client_perk", "notes")
    VENDOR_FLAGS = ("insured", "is_preferred", "client_visible")

    def vendor_from_form(form) -> tuple[dict, str | None]:
        data = {k: clean(form, k) for k in VENDOR_TEXT}
        data.update({k: checkbox(form, k) for k in VENDOR_FLAGS})
        try:
            data["rating"] = max(0, min(5, int(form.get("rating") or 0)))
        except ValueError:
            data["rating"] = 0
        if data["website"] and not data["website"].startswith(("http://", "https://")):
            data["website"] = "https://" + data["website"]
        if not data["name"]:
            return data, "Vendor name is required."
        if data["category"] not in VENDOR_CATEGORIES:
            return data, "Choose a category."
        return data, None

    @app.get("/vendors")
    def vendor_list(request: Request, q: str = "", category: str = "",
                    user=Depends(current_user), conn=Depends(get_conn)):
        sql, args = "SELECT * FROM vendors WHERE 1=1", []
        if q:
            sql += " AND (name LIKE ? OR company LIKE ? OR service_area LIKE ? OR notes LIKE ?)"
            args += [f"%{q}%"] * 4
        if category:
            sql += " AND category = ?"
            args.append(category)
        sql += " ORDER BY category, is_preferred DESC, rating DESC, name"
        vendors = conn.execute(sql, args).fetchall()
        return render(request, conn, "vendors/list.html", user=user, vendors=vendors, q=q,
                      category=category)

    @app.get("/vendors/new")
    def vendor_new(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        vendor = {"client_visible": 1, "category": request.query_params.get("category", "")}
        return render(request, conn, "vendors/form.html", user=user, vendor=vendor, error=None)

    @app.post("/vendors/new")
    async def vendor_create(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        data, error = vendor_from_form(await request.form())
        if error:
            return render(request, conn, "vendors/form.html", status_code=400, user=user,
                          vendor=data, error=error)
        cols = ", ".join(data)
        cur = conn.execute(f"INSERT INTO vendors ({cols}) VALUES ({', '.join('?' * len(data))})",
                           list(data.values()))
        return redirect(f"/vendors/{cur.lastrowid}", f"Added {data['name']}.", request)

    @app.get("/vendors/{vendor_id}")
    def vendor_detail(vendor_id: int, request: Request, user=Depends(current_user),
                      conn=Depends(get_conn)):
        vendor = get_or_404(conn, "vendors", vendor_id)
        jobs = conn.execute(
            """SELECT r.*, p.address AS property_address, c.name AS client_name
               FROM service_requests r LEFT JOIN properties p ON p.id = r.property_id
               LEFT JOIN clients c ON c.id = r.client_id
               WHERE r.vendor_id = ? ORDER BY r.created_at DESC""", (vendor_id,)
        ).fetchall()
        referrals = conn.execute(
            """SELECT f.*, c.name AS client_name FROM referrals f JOIN clients c ON c.id = f.client_id
               WHERE f.vendor_id = ? ORDER BY f.created_at DESC""", (vendor_id,)
        ).fetchall()
        assigned = conn.execute(
            """SELECT p.*, pv.category AS pv_category FROM property_vendors pv
               JOIN properties p ON p.id = pv.property_id WHERE pv.vendor_id = ?""", (vendor_id,)
        ).fetchall()
        return render(request, conn, "vendors/detail.html", user=user, vendor=vendor, jobs=jobs,
                      referrals=referrals, assigned=assigned)

    @app.get("/vendors/{vendor_id}/edit")
    def vendor_edit(vendor_id: int, request: Request, user=Depends(current_user),
                    conn=Depends(get_conn)):
        vendor = get_or_404(conn, "vendors", vendor_id)
        return render(request, conn, "vendors/form.html", user=user, vendor=vendor, error=None)

    @app.post("/vendors/{vendor_id}/edit")
    async def vendor_update(vendor_id: int, request: Request, user=Depends(current_user),
                            conn=Depends(get_conn)):
        get_or_404(conn, "vendors", vendor_id)
        data, error = vendor_from_form(await request.form())
        if error:
            return render(request, conn, "vendors/form.html", status_code=400, user=user,
                          vendor={**data, "id": vendor_id}, error=error)
        assignments = ", ".join(f"{k}=?" for k in data)
        conn.execute(f"UPDATE vendors SET {assignments} WHERE id=?", [*data.values(), vendor_id])
        return redirect(f"/vendors/{vendor_id}", "Vendor updated.", request)

    @app.post("/vendors/{vendor_id}/delete")
    def vendor_delete(vendor_id: int, request: Request, user=Depends(current_user),
                      conn=Depends(get_conn)):
        vendor = get_or_404(conn, "vendors", vendor_id)
        conn.execute("DELETE FROM vendors WHERE id=?", (vendor_id,))
        return redirect("/vendors", f"Removed {vendor['name']}.", request)

    # ----------------------------------------------------------------------
    # clients
    # ----------------------------------------------------------------------

    CLIENT_TEXT = ("name", "email", "phone", "client_type", "address", "unit", "notes")

    def client_from_form(conn, form) -> tuple[dict, str | None]:
        data = {k: clean(form, k) for k in CLIENT_TEXT}
        prop = clean(form, "property_id")
        data["property_id"] = int(prop) if prop.isdigit() else None
        if data["property_id"] and not conn.execute(
                "SELECT 1 FROM properties WHERE id=?", (data["property_id"],)).fetchone():
            data["property_id"] = None
        if not data["name"]:
            return data, "Client name is required."
        if data["client_type"] not in CLIENT_TYPES:
            return data, "Choose a client type."
        try:
            data["key_date"] = parse_date(form.get("key_date"))
            data["lease_end"] = parse_date(form.get("lease_end"))
        except ValueError:
            return data, "Dates must be in YYYY-MM-DD format."
        if data["client_type"] != "renter":
            data["property_id"], data["unit"], data["lease_end"] = None, "", ""
        return data, None

    def portal_url(request: Request, token: str) -> str:
        return str(request.base_url).rstrip("/") + f"/p/{token}"

    def all_properties(conn):
        return conn.execute("SELECT id, name, address FROM properties ORDER BY address").fetchall()

    @app.get("/clients")
    def client_list(request: Request, q: str = "", type: str = "",
                    user=Depends(current_user), conn=Depends(get_conn)):
        sql, args = "SELECT * FROM clients WHERE 1=1", []
        if q:
            sql += " AND (name LIKE ? OR email LIKE ? OR address LIKE ? OR phone LIKE ?)"
            args += [f"%{q}%"] * 4
        if type in CLIENT_TYPES:
            sql += " AND client_type = ?"
            args.append(type)
        clients = conn.execute(sql + " ORDER BY name", args).fetchall()
        return render(request, conn, "clients/list.html", user=user, clients=clients, q=q,
                      type=type)

    @app.get("/clients/new")
    def client_new(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        client = {"client_type": request.query_params.get("type", "buyer")}
        return render(request, conn, "clients/form.html", user=user, client=client, error=None,
                      properties=all_properties(conn))

    @app.post("/clients/new")
    async def client_create(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        data, error = client_from_form(conn, await request.form())
        if error:
            return render(request, conn, "clients/form.html", status_code=400, user=user,
                          client=data, error=error, properties=all_properties(conn))
        data["portal_token"] = db.new_portal_token()
        cols = ", ".join(data)
        cur = conn.execute(f"INSERT INTO clients ({cols}) VALUES ({', '.join('?' * len(data))})",
                           list(data.values()))
        return redirect(f"/clients/{cur.lastrowid}",
                        f"Added {data['name']}. Share their portal link below.", request)

    @app.get("/clients/{client_id}")
    def client_detail(client_id: int, request: Request, user=Depends(current_user),
                      conn=Depends(get_conn)):
        client = get_or_404(conn, "clients", client_id)
        prop = (conn.execute("SELECT * FROM properties WHERE id=?", (client["property_id"],)).fetchone()
                if client["property_id"] else None)
        requests_ = conn.execute(
            """SELECT r.*, v.name AS vendor_name FROM service_requests r
               LEFT JOIN vendors v ON v.id = r.vendor_id
               WHERE r.client_id = ? ORDER BY r.created_at DESC""", (client_id,)
        ).fetchall()
        referrals = conn.execute(
            """SELECT f.*, v.name AS vendor_name, v.category FROM referrals f
               JOIN vendors v ON v.id = f.vendor_id WHERE f.client_id = ?
               ORDER BY f.created_at DESC""", (client_id,)
        ).fetchall()
        done = {r["item_key"] for r in conn.execute(
            "SELECT item_key FROM readiness_progress WHERE client_id=?", (client_id,))}
        anniversary = next_anniversary(client["key_date"], today()) if client["client_type"] == "buyer" else None
        return render(request, conn, "clients/detail.html", user=user, client=client, prop=prop,
                      requests=requests_, referrals=referrals, steps=READINESS_STEPS, done=done,
                      anniversary=anniversary, portal_url=portal_url(request, client["portal_token"]))

    @app.get("/clients/{client_id}/edit")
    def client_edit(client_id: int, request: Request, user=Depends(current_user),
                    conn=Depends(get_conn)):
        client = get_or_404(conn, "clients", client_id)
        return render(request, conn, "clients/form.html", user=user, client=client, error=None,
                      properties=all_properties(conn))

    @app.post("/clients/{client_id}/edit")
    async def client_update(client_id: int, request: Request, user=Depends(current_user),
                            conn=Depends(get_conn)):
        get_or_404(conn, "clients", client_id)
        data, error = client_from_form(conn, await request.form())
        if error:
            return render(request, conn, "clients/form.html", status_code=400, user=user,
                          client={**data, "id": client_id}, error=error,
                          properties=all_properties(conn))
        assignments = ", ".join(f"{k}=?" for k in data)
        conn.execute(f"UPDATE clients SET {assignments} WHERE id=?", [*data.values(), client_id])
        return redirect(f"/clients/{client_id}", "Client updated.", request)

    @app.post("/clients/{client_id}/new-link")
    def client_new_link(client_id: int, request: Request, user=Depends(current_user),
                        conn=Depends(get_conn)):
        get_or_404(conn, "clients", client_id)
        conn.execute("UPDATE clients SET portal_token=? WHERE id=?", (db.new_portal_token(), client_id))
        return redirect(f"/clients/{client_id}",
                        "New portal link created. The old link no longer works.", request)

    @app.post("/clients/{client_id}/delete")
    def client_delete(client_id: int, request: Request, user=Depends(current_user),
                      conn=Depends(get_conn)):
        client = get_or_404(conn, "clients", client_id)
        conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
        return redirect("/clients", f"Removed {client['name']}.", request)

    # ----------------------------------------------------------------------
    # managed properties
    # ----------------------------------------------------------------------

    def property_from_form(form) -> tuple[dict, str | None]:
        data = {k: clean(form, k) for k in ("name", "address", "owner_name", "notes")}
        try:
            data["unit_count"] = max(1, int(form.get("unit_count") or 1))
        except ValueError:
            data["unit_count"] = 1
        return data, None if data["address"] else "Address is required."

    @app.get("/properties")
    def property_list(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        properties = conn.execute(
            f"""SELECT p.*,
                  (SELECT COUNT(*) FROM clients c WHERE c.property_id = p.id) AS tenants,
                  (SELECT COUNT(*) FROM service_requests r WHERE r.property_id = p.id
                     AND r.status IN {OPEN_STATUSES}) AS open_requests
                FROM properties p ORDER BY p.address"""
        ).fetchall()
        return render(request, conn, "properties/list.html", user=user, properties=properties)

    @app.get("/properties/new")
    def property_new(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        return render(request, conn, "properties/form.html", user=user, prop={}, error=None)

    @app.post("/properties/new")
    async def property_create(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        data, error = property_from_form(await request.form())
        if error:
            return render(request, conn, "properties/form.html", status_code=400, user=user,
                          prop=data, error=error)
        cur = conn.execute(
            "INSERT INTO properties (name, address, owner_name, notes, unit_count) VALUES (?,?,?,?,?)",
            list(data.values()))
        return redirect(f"/properties/{cur.lastrowid}",
                        "Property added. Now pick who to call for each kind of job.", request)

    @app.get("/properties/{prop_id}")
    def property_detail(prop_id: int, request: Request, user=Depends(current_user),
                        conn=Depends(get_conn)):
        prop = get_or_404(conn, "properties", prop_id)
        call_list = conn.execute(
            """SELECT pv.category, v.* FROM property_vendors pv JOIN vendors v ON v.id = pv.vendor_id
               WHERE pv.property_id = ? ORDER BY pv.category""", (prop_id,)
        ).fetchall()
        tenants = conn.execute("SELECT * FROM clients WHERE property_id=? ORDER BY unit, name",
                               (prop_id,)).fetchall()
        history = conn.execute(
            """SELECT r.*, v.name AS vendor_name, c.name AS client_name FROM service_requests r
               LEFT JOIN vendors v ON v.id = r.vendor_id LEFT JOIN clients c ON c.id = r.client_id
               WHERE r.property_id = ? ORDER BY r.created_at DESC""", (prop_id,)
        ).fetchall()
        vendors = conn.execute("SELECT id, name, company, category FROM vendors ORDER BY category, name").fetchall()
        return render(request, conn, "properties/detail.html", user=user, prop=prop,
                      call_list=call_list, tenants=tenants, history=history, vendors=vendors)

    @app.post("/properties/{prop_id}/call-list")
    async def property_set_vendor(prop_id: int, request: Request, user=Depends(current_user),
                                  conn=Depends(get_conn)):
        get_or_404(conn, "properties", prop_id)
        form = await request.form()
        category, vendor_id = clean(form, "category"), clean(form, "vendor_id")
        if category not in VENDOR_CATEGORIES:
            return redirect(f"/properties/{prop_id}", "Choose a category.", request)
        if vendor_id.isdigit():
            get_or_404(conn, "vendors", int(vendor_id))
            conn.execute(
                "INSERT OR REPLACE INTO property_vendors (property_id, category, vendor_id) VALUES (?,?,?)",
                (prop_id, category, int(vendor_id)))
            msg = f"Call list updated for {category}."
        else:
            conn.execute("DELETE FROM property_vendors WHERE property_id=? AND category=?",
                         (prop_id, category))
            msg = f"Removed {category} from the call list."
        return redirect(f"/properties/{prop_id}", msg, request)

    @app.get("/properties/{prop_id}/edit")
    def property_edit(prop_id: int, request: Request, user=Depends(current_user),
                      conn=Depends(get_conn)):
        prop = get_or_404(conn, "properties", prop_id)
        return render(request, conn, "properties/form.html", user=user, prop=prop, error=None)

    @app.post("/properties/{prop_id}/edit")
    async def property_update(prop_id: int, request: Request, user=Depends(current_user),
                              conn=Depends(get_conn)):
        get_or_404(conn, "properties", prop_id)
        data, error = property_from_form(await request.form())
        if error:
            return render(request, conn, "properties/form.html", status_code=400, user=user,
                          prop={**data, "id": prop_id}, error=error)
        conn.execute(
            "UPDATE properties SET name=?, address=?, owner_name=?, notes=?, unit_count=? WHERE id=?",
            [*data.values(), prop_id])
        return redirect(f"/properties/{prop_id}", "Property updated.", request)

    @app.post("/properties/{prop_id}/delete")
    def property_delete(prop_id: int, request: Request, user=Depends(current_user),
                        conn=Depends(get_conn)):
        get_or_404(conn, "properties", prop_id)
        conn.execute("DELETE FROM properties WHERE id=?", (prop_id,))
        return redirect("/properties", "Property removed.", request)

    # ----------------------------------------------------------------------
    # service requests
    # ----------------------------------------------------------------------

    def suggested_vendor(conn, property_id, category):
        """The property's assigned vendor for this category, else the best-rated preferred one."""
        if property_id:
            row = conn.execute(
                """SELECT v.* FROM property_vendors pv JOIN vendors v ON v.id = pv.vendor_id
                   WHERE pv.property_id=? AND pv.category=?""", (property_id, category)).fetchone()
            if row:
                return row
        return conn.execute(
            "SELECT * FROM vendors WHERE category=? ORDER BY is_preferred DESC, rating DESC, name LIMIT 1",
            (category,)).fetchone()

    @app.get("/requests")
    def request_list(request: Request, status: str = "open", user=Depends(current_user),
                     conn=Depends(get_conn)):
        sql = """SELECT r.*, c.name AS client_name, p.address AS property_address, v.name AS vendor_name
                 FROM service_requests r LEFT JOIN clients c ON c.id = r.client_id
                 LEFT JOIN properties p ON p.id = r.property_id LEFT JOIN vendors v ON v.id = r.vendor_id"""
        args = []
        if status == "open":
            sql += f" WHERE r.status IN {OPEN_STATUSES}"
        elif status in REQUEST_STATUSES:
            sql += " WHERE r.status = ?"
            args.append(status)
        sql += " ORDER BY r.urgency = 'urgent' DESC, r.created_at DESC"
        return render(request, conn, "requests/list.html", user=user,
                      requests=conn.execute(sql, args).fetchall(), status=status)

    @app.get("/requests/new")
    def request_new(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        qp = request.query_params
        req = {"property_id": int(qp["property_id"]) if qp.get("property_id", "").isdigit() else None,
               "client_id": int(qp["client_id"]) if qp.get("client_id", "").isdigit() else None,
               "urgency": "normal"}
        return render(request, conn, "requests/new.html", user=user, req=req, error=None,
                      properties=all_properties(conn),
                      clients=conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall())

    @app.post("/requests/new")
    async def request_create(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        form = await request.form()
        req = {
            "category": clean(form, "category"),
            "description": clean(form, "description"),
            "urgency": clean(form, "urgency") or "normal",
            "property_id": int(clean(form, "property_id")) if clean(form, "property_id").isdigit() else None,
            "client_id": int(clean(form, "client_id")) if clean(form, "client_id").isdigit() else None,
        }
        error = None
        if req["category"] not in VENDOR_CATEGORIES:
            error = "Choose a category."
        elif not req["description"]:
            error = "Describe the problem."
        elif req["urgency"] not in URGENCIES:
            error = "Choose an urgency."
        if error:
            return render(request, conn, "requests/new.html", status_code=400, user=user, req=req,
                          error=error, properties=all_properties(conn),
                          clients=conn.execute("SELECT id, name FROM clients ORDER BY name").fetchall())
        if req["property_id"]:
            get_or_404(conn, "properties", req["property_id"])
        if req["client_id"]:
            get_or_404(conn, "clients", req["client_id"])
        cur = conn.execute(
            """INSERT INTO service_requests (category, description, urgency, property_id, client_id, source)
               VALUES (?,?,?,?,?, 'staff')""", list(req.values()))
        return redirect(f"/requests/{cur.lastrowid}", "Request logged.", request)

    @app.get("/requests/{req_id}")
    def request_detail(req_id: int, request: Request, user=Depends(current_user),
                       conn=Depends(get_conn)):
        req = conn.execute(
            """SELECT r.*, c.name AS client_name, c.phone AS client_phone, c.unit AS client_unit,
                      p.address AS property_address
               FROM service_requests r LEFT JOIN clients c ON c.id = r.client_id
               LEFT JOIN properties p ON p.id = r.property_id WHERE r.id = ?""", (req_id,)
        ).fetchone()
        if req is None:
            raise HTTPException(404, "Not found")
        vendors = conn.execute(
            "SELECT * FROM vendors ORDER BY category = ? DESC, is_preferred DESC, rating DESC, name",
            (req["category"],)).fetchall()
        suggestion = suggested_vendor(conn, req["property_id"], req["category"])
        vendor = (conn.execute("SELECT * FROM vendors WHERE id=?", (req["vendor_id"],)).fetchone()
                  if req["vendor_id"] else None)
        return render(request, conn, "requests/detail.html", user=user, req=req, vendors=vendors,
                      suggestion=suggestion, vendor=vendor)

    @app.post("/requests/{req_id}")
    async def request_update(req_id: int, request: Request, user=Depends(current_user),
                             conn=Depends(get_conn)):
        req = get_or_404(conn, "service_requests", req_id)
        form = await request.form()
        status = clean(form, "status")
        vendor_id = int(clean(form, "vendor_id")) if clean(form, "vendor_id").isdigit() else None
        if vendor_id:
            get_or_404(conn, "vendors", vendor_id)
        if status not in REQUEST_STATUSES:
            status = req["status"]
        # Picking a vendor on a brand-new request moves it along automatically.
        if vendor_id and status == "new":
            status = "assigned"
        cost_raw = clean(form, "cost").replace("$", "").replace(",", "")
        try:
            cost = float(cost_raw) if cost_raw else None
        except ValueError:
            cost = req["cost"]
        conn.execute(
            """UPDATE service_requests SET status=?, vendor_id=?, cost=?, staff_notes=?,
               updated_at=datetime('now') WHERE id=?""",
            (status, vendor_id, cost, clean(form, "staff_notes"), req_id))
        return redirect(f"/requests/{req_id}", "Request updated.", request)

    # ----------------------------------------------------------------------
    # settings / team
    # ----------------------------------------------------------------------

    SETTING_KEYS = ("business_name", "agent_name", "agent_phone", "agent_email", "portal_welcome")

    @app.get("/settings")
    def settings_form(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        team = conn.execute("SELECT id, name, email, created_at FROM users ORDER BY name").fetchall()
        return render(request, conn, "settings.html", user=user, team=team, error=None)

    @app.post("/settings")
    async def settings_save(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        form = await request.form()
        for key in SETTING_KEYS:
            conn.execute("UPDATE settings SET value=? WHERE key=?", (clean(form, key), key))
        return redirect("/settings", "Settings saved.", request)

    @app.post("/settings/team")
    async def team_add(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        form = await request.form()
        name, email, password = clean(form, "name"), clean(form, "email"), form.get("password") or ""
        error = None
        if not name or "@" not in email or len(password) < 8:
            error = "Enter a name, a valid email and a temporary password of 8+ characters."
        elif conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            error = "Someone with that email already has an account."
        if error:
            team = conn.execute("SELECT id, name, email, created_at FROM users ORDER BY name").fetchall()
            return render(request, conn, "settings.html", status_code=400, user=user, team=team,
                          error=error)
        conn.execute("INSERT INTO users (name, email, password_hash) VALUES (?,?,?)",
                     (name, email, hash_password(password)))
        return redirect("/settings", f"Added {name} to your team.", request)

    @app.post("/settings/password")
    async def change_password(request: Request, user=Depends(current_user), conn=Depends(get_conn)):
        form = await request.form()
        if not verify_password(form.get("current") or "", user["password_hash"]):
            return redirect("/settings", "Current password is incorrect.", request)
        new = form.get("new") or ""
        if len(new) < 8:
            return redirect("/settings", "New password must be at least 8 characters.", request)
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), user["id"]))
        return redirect("/settings", "Password changed.", request)

    # ----------------------------------------------------------------------
    # client portal (no login - the unguessable token is the key)
    # ----------------------------------------------------------------------

    def portal_client(conn, token: str):
        client = conn.execute("SELECT * FROM clients WHERE portal_token=?", (token,)).fetchone()
        if client is None:
            raise HTTPException(404, "This link is no longer active. Please contact your agent.")
        return client

    @app.get("/p/{token}")
    def portal_home(token: str, request: Request, conn=Depends(get_conn)):
        client = portal_client(conn, token)
        vendors = conn.execute(
            """SELECT id, name, company, category, phone, email, website, service_area, insured,
                      license_info, rating, is_preferred, client_perk
               FROM vendors WHERE client_visible = 1
               ORDER BY category, is_preferred DESC, rating DESC, name""").fetchall()
        directory: dict[str, list] = {}
        for v in vendors:
            directory.setdefault(v["category"], []).append(v)

        now = today()
        season, tips = season_for_month(now.month)
        ctx = dict(client=client, token=token, directory=directory, season=season, tips=tips,
                   referred={r[0] for r in conn.execute(
                       "SELECT vendor_id FROM referrals WHERE client_id=?", (client["id"],))})

        if client["client_type"] == "buyer":
            ctx["anniversary"] = next_anniversary(client["key_date"], now)
            if client["key_date"]:
                ctx["years_owned"] = (now - date.fromisoformat(client["key_date"])).days // 365
        if client["client_type"] == "renter":
            ctx["steps"] = READINESS_STEPS
            ctx["done"] = {r[0] for r in conn.execute(
                "SELECT item_key FROM readiness_progress WHERE client_id=?", (client["id"],))}
            ctx["my_requests"] = conn.execute(
                """SELECT r.*, v.name AS vendor_name FROM service_requests r
                   LEFT JOIN vendors v ON v.id = r.vendor_id
                   WHERE r.client_id=? ORDER BY r.created_at DESC LIMIT 20""", (client["id"],)).fetchall()
        return render(request, conn, "portal/home.html", **ctx)

    @app.post("/p/{token}/connect")
    async def portal_referral(token: str, request: Request, conn=Depends(get_conn)):
        client = portal_client(conn, token)
        form = await request.form()
        vendor_id = clean(form, "vendor_id")
        vendor = conn.execute("SELECT * FROM vendors WHERE id=? AND client_visible=1",
                              (int(vendor_id) if vendor_id.isdigit() else -1,)).fetchone()
        if vendor is None:
            raise HTTPException(404, "Vendor not found")
        conn.execute("INSERT INTO referrals (client_id, vendor_id, message) VALUES (?,?,?)",
                     (client["id"], vendor["id"], clean(form, "message")[:1000]))
        return redirect(f"/p/{token}#{slug(vendor['category'])}",
                        f"Thanks! We'll personally connect you with {vendor['name']} shortly.", request)

    @app.post("/p/{token}/requests")
    async def portal_request(token: str, request: Request, conn=Depends(get_conn)):
        client = portal_client(conn, token)
        if client["client_type"] != "renter":
            raise HTTPException(403, "Maintenance requests are for renters")
        form = await request.form()
        category, description = clean(form, "category"), clean(form, "description")[:4000]
        urgency = clean(form, "urgency") or "normal"
        if category not in VENDOR_CATEGORIES or not description or urgency not in URGENCIES:
            return redirect(f"/p/{token}#maintenance",
                            "Please pick a category and describe the problem.", request)
        conn.execute(
            """INSERT INTO service_requests (client_id, property_id, category, description, urgency, source)
               VALUES (?,?,?,?,?, 'portal')""",
            (client["id"], client["property_id"], category, description, urgency))
        return redirect(f"/p/{token}#maintenance",
                        "Request received. We'll be in touch to schedule it.", request)

    @app.post("/p/{token}/readiness")
    async def portal_readiness(token: str, request: Request, conn=Depends(get_conn)):
        client = portal_client(conn, token)
        form = await request.form()
        key = clean(form, "item_key")
        if key not in READINESS_KEYS:
            raise HTTPException(400, "Unknown step")
        if form.get("done") == "1":
            conn.execute("INSERT OR IGNORE INTO readiness_progress (client_id, item_key) VALUES (?,?)",
                         (client["id"], key))
        else:
            conn.execute("DELETE FROM readiness_progress WHERE client_id=? AND item_key=?",
                         (client["id"], key))
        return redirect(f"/p/{token}#path")

    return app

