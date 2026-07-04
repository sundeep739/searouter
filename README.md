# SeaRouter

Mobile-first web app for real navigable **sea-route distances** — not straight-line —
between ports worldwide, with voyage planning, distance matrices, and reported-distance
verification. A modern rebuild of the original Streamlit `seadistance` tool, with
invite-only access control and per-user saved data.

Built on the UN/LOCODE port database and the offline `searoute` maritime routing network,
with automatic canal/chokepoint alternatives (Suez vs Cape of Good Hope, Panama vs Suez, …).

## What's inside

| Path | Stack | Role |
|---|---|---|
| `backend/` | FastAPI + `searoute` + `rapidfuzz` | Routing engine API (reuses the original `core.py` unchanged) |
| `web/` | Vite + React + TypeScript + Tailwind + MapLibre GL | Installable PWA — map-first UI |
| Supabase | Postgres + Auth | Invite-only auth, roles, saved routes/voyages/vessels, verification history |

Features: **Route planner** (avoid-passage chips, waypoints, avoid-areas, ETA, schedule
mode, shareable URLs), **Voyage planner** (multi-port rotations with dwell time),
**Distance matrix**, **Batch verify** (CSV upload), **Vessels** (per-user fleet), and an
**Admin** panel for invites. Weather-aware speed optimization is scaffolded for a later
phase (the `backend/weather/` package is present but dormant).

## Architecture

```
Browser (PWA, Vercel)  ──JWT──►  FastAPI (Render)  ──►  searoute + UN/LOCODE (bundled)
        │
        └──►  Supabase  (auth, profiles, saved_routes, vessels, verifications)
```

The frontend authenticates with Supabase and sends the resulting JWT to the FastAPI
backend, which verifies it (asymmetric JWKS or legacy HS256) before serving any route
data. Avoid-areas are enforced client-side (a warning if the chosen route crosses them),
matching the original app's behaviour.

## Local development

**Backend**

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
# Run with auth off for quick local UI work:
AUTH_DISABLED=1 uvicorn main:app --reload --port 8000
pytest                             # 13 routing/endpoint tests
```

**Frontend**

```bash
cd web
npm install
cp .env.example .env               # fill in Supabase URL + anon key + VITE_API_URL
npm run dev                        # http://localhost:5173
```

## Environment variables

**Frontend (`web/.env`, and Vercel project settings)**

| Var | Value |
|---|---|
| `VITE_SUPABASE_URL` | `https://<project>.supabase.co` |
| `VITE_SUPABASE_ANON_KEY` | Supabase publishable/anon key |
| `VITE_API_URL` | The deployed backend URL (e.g. `https://searouter-api.onrender.com`) |

**Backend (Render service settings)**

| Var | Value |
|---|---|
| `SUPABASE_URL` | `https://<project>.supabase.co` |
| `SUPABASE_JWT_SECRET` | Supabase → Project Settings → API → JWT secret (needed only for legacy HS256 tokens) |
| `SUPABASE_SERVICE_ROLE_KEY` | Service-role key (used for admin invites + role lookups) |
| `ALLOWED_ORIGINS` | Comma-separated, e.g. `https://searouter.vercel.app` (any `*.vercel.app` is already allowed) |

## Deploying the backend to Render (one-time, manual)

The backend can't be auto-deployed for you — connect the repo once:

1. Go to <https://dashboard.render.com> → **New** → **Blueprint**.
2. Connect this GitHub repo (`sundeep739/searouter`). Render reads `backend/render.yaml`
   and proposes a free web service named `searouter-api`.
3. When prompted, fill the four backend env vars from the table above
   (`SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`, `ALLOWED_ORIGINS`).
4. Click **Apply**. First build takes a few minutes; health check is `GET /health`.
5. Copy the service URL (e.g. `https://searouter-api.onrender.com`) into the Vercel
   project's `VITE_API_URL` and redeploy the frontend.

> Render's free tier sleeps after ~15 min idle; the first request then takes ~30–60 s to
> wake. The UI shows a "server is waking up" notice during that window.

## Access control

Invite-only: there is no public sign-up. An **admin** invites users by email from the
Admin tab (or the Supabase dashboard). Invited users click the emailed link, set a
password, and land in the app as a **member**. Members see everything except the Admin
tab; only admins can invite users or change roles. Row-Level Security ensures each user
only reads/writes their own saved routes, vessels, and verification history (admins can
read all).

The first admin (`sundeepshaw@gmail.com`) is seeded in the database — sign in and change
the temporary password immediately.

## Data

UN/LOCODE CSVs (~7 MB) ship inside `backend/unlocode/csv/`. Distances are theoretical
shortest sea routes; real voyage logs typically run a few percent higher. A reported
distance *below* the shortest viable route is a stronger red flag than one slightly above.
