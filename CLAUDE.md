# Rajdeep IoT Platform (UCAP)

Universal Industrial Platform: configuration-driven IoT platform. V1 = OPTEX FA CD22-35-485M12 (RS-485)
-> Rubber Thickness Measurement. Goal: grow this into a complete IoT platform. Full architecture, onboarding
flow, API table and acceptance criteria: `README.md` (read it first). Status review, known bugs, data-mapping
build plan and priorities: `ROADMAP.md` (2026-10-12). Product strategy docs (keep updated as research/decisions
come in): `docs/PRODUCT_FLOW.md` (sales-to-live flow, customization layers L0-L4, template library, phases),
`docs/COMPETITORS.md` (competitor research), `docs/MVP_PLAN.md` (one-product end-to-end MVP: review, milestones
M0-M6, acceptance test; proposal awaiting go-ahead).

## Origin and sync (important)

- Shared MVP repository: `https://github.com/rajdeep-automation/rajdeep-iot.git`, branch `main`, remote `origin`.
- The original Dell folder is `C:\Users\admin\Documents\IoT Platform`; its remote is retained locally as
  `laptop-origin` (`software-rajdeep/IOT-PLATFORM`). Do not overwrite or reset that folder.
- A fresh SSH comparison on 2026-10-12 found nine files changed on the Dell since the Mac baseline `b1ede87`,
  including opposite-sensor/reference-sensor processing and UI. Those changes are separate from this single-sensor MVP.
- The completed MVP is delivered to a separate Dell checkout, `C:\Users\admin\Documents\IoT Platform MVP`.
  Deployment details and test results live in `docs/DELIVERY.md`.
- Before any future synchronization of the original folder, pull and compare its current files first. Keep its
  uncommitted work and database. Credentials stay in local memory/environment, never in git.
- `thickness-monitoring/` is a separate ignored Flask + React app, outside this MVP delivery.

## Layout

```
backend/app/{drivers,processors,routers,services}   FastAPI; six strictly separated blocks (see README)
backend/database/{schema,seed,customer_demo}.sql
backend/scripts/init_db.py                          creates DBs, applies schema+seed
frontend/                                           dependency-free SPA (js/views/*.js)
thickness-monitoring/                               separate app (see above)
run.ps1 / run-all.ps1                               Windows launch scripts
```

Baseline laptop work (now committed in the shared repo): "Customer database (API)" connection mode (`drivers/database_api.py`), a demo
customer server + DB (`routers/demo_customer.py`, DB `ucap_customer_corp`), a much larger dashboard
(`frontend/js/views/dashboard.js`), and updates to devices/instances/processing services.

## Running locally on this Mac

Demo login: `admin@demo.local` / `demo1234` (ABC Rubber); `admin@xyz.local` / `demo1234` (XYZ Polymers).
Docs at `http://127.0.0.1:8000/docs`.

`backend/.env` (copied from the laptop) points at a Postgres `postgres:linux@localhost:5432`. This Mac already runs
a Postgres on 5432 used by other apps, and the harness blocked reading `.env` / probing it — **don't touch it**.
Use an isolated throwaway cluster on 5544 (env vars override `.env`):

```bash
initdb -D /tmp/pg-ucap -U postgres --auth=trust && mkdir -p /tmp/pg5544
pg_ctl -D /tmp/pg-ucap -o "-p 5544 -k /tmp/pg5544" -l /tmp/pg-ucap.log -w start   # short socket dir is required
cd backend
export DATABASE_URL=postgresql://postgres@127.0.0.1:5544/ucap_platform \
       CUSTOMER_DEMO_DATABASE_URL=postgresql://postgres@127.0.0.1:5544/ucap_customer_corp
python3 scripts/init_db.py
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Deps already installed in pyenv 3.13: fastapi, uvicorn, psycopg2, bcrypt, itsdangerous. `ENABLE_SIMULATOR=1` generates
readings, no hardware needed. As of 2026-10-12 a session left this running (API :8000, Postgres :5544 with cluster in
that session's scratchpad); the data was demo seed only. If ports are busy, check `lsof -i :8000 -i :5544`.

## Rules for this project

- Follow `~/.claude/CLAUDE.md` (concise, no emojis, ask before destructive commands, no paid-API calls without asking).
- Commit author for any `rajdeep-automation` repo: `rajdeep-automation <299466890+rajdeep-automation@users.noreply.github.com>`.
- Never commit `.env` or plaintext passwords. Don't add the laptop password to this repo.
- Respect the block separation in README: the connector knows only the protocol; tolerances/calibration live in
  template config, not code. New devices/purposes/templates/customers should be data, not code.
