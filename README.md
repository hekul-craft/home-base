# HomeBase

A trusted-vendor directory and client portal for realtors and property managers.

- **Stay in touch with past buyers.** Give each past client a private link to
  your list of trusted local pros: plumbers, HVAC, roofers, lenders and so on.
  Each vendor has one-tap call/email buttons and a "Connect me" button that
  asks you for an introduction. Every request lands on your dashboard, so you
  have a reason to reach out.
- **Run property management.** Keep a "who to call" list for each managed
  property, log service requests, assign vendors (the property's assigned vendor
  is suggested automatically) and track job status, cost and history.
- **Turn renters into buyers.** Renters get a maintenance request form, plus a
  step-by-step "path to homeownership" checklist (credit, budget, savings,
  pre-approval…) that you can see their progress on.
- **Get prompted to follow up.** The dashboard shows upcoming home-purchase
  anniversaries, leases ending soon, pending introductions and open jobs.

## Run it

```bash
pip install -r requirements.txt
python -m app.seed            # optional: demo data, login demo@homebase.test / homebase123
uvicorn app.main:create_app --factory --reload
```

Open http://localhost:8000. If you skip the seed step, the first visit takes
you to a setup page to create your own account.

Data is stored in a single SQLite file (`homebase.db`, or set `HOMEBASE_DB`).
To back up, copy that file.

## How the client portal works

Each client gets a link like `https://your-site/p/Xk3...`. There's no password:
the long random token in the link is the key, which keeps it frictionless for
clients. Copy, email or text the link from the client's page. If a link ever
gets shared by mistake, click **Create new link** and the old one stops working.

Vendors marked **Show in client portals** appear in the portal. Uncheck it for
vendors you only use for property management. **Internal notes** are never
shown to clients.

## Project layout

| Path | What it is |
| --- | --- |
| `app/main.py` | All routes: staff app + client portal (`/p/<token>`) |
| `app/db.py` | SQLite schema and connection helpers |
| `app/content.py` | Vendor categories, homeownership checklist, seasonal tips |
| `app/auth.py` | Password hashing (PBKDF2) |
| `app/seed.py` | Demo data |
| `app/templates/` | Jinja2 HTML templates (mobile-friendly, light/dark) |
| `tests/` | Run with `pip install -r requirements-dev.txt && pytest` |

## Deploying

Any host that runs Python works (Render, Railway, Fly.io, a small VPS). Run
`uvicorn app.main:create_app --factory --host 0.0.0.0 --port $PORT`, keep
`homebase.db` on a persistent disk, serve over HTTPS and set `HOMEBASE_HTTPS=1`
so session cookies are marked secure.

## Ideas for next steps

- Automated emails/texts: anniversary notes, seasonal reminders, a newsletter.
- CSV import/export of vendors and clients (from a CRM or spreadsheet).
- Vendor reviews left by clients after a job.
- Photo uploads on maintenance requests.
- Rent-vs-buy and mortgage calculators in the renter portal.
- Installable mobile app (PWA) or native wrapper.
