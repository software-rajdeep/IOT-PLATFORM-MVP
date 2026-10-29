# Universal Industrial Platform (UCAP) — V1: CD22 → Rubber Thickness

A generalised, configuration-driven industrial platform. The first real device is
the **OPTEX FA CD22-35-485M12** over **RS-485**; the first real application is
**Rubber Thickness Measurement**. Adding another device, purpose, template or
customer is **data**, not code.

Built with **FastAPI + psycopg2 + PostgreSQL** and a dependency-free browser SPA
(no build step). No hardcoded customer logic: tolerances, calibration and labels
live in configuration.

---

## The six blocks (strictly separated — do not mix)

```
CD22-35 RS485
     │
     ▼
[1] CD22 CONNECTOR          backend/app/drivers/cd22_connector.py
     │   (the ONLY code that knows the OPTEX protocol)
     ▼
[2] DEVICE PROFILE          core.device_profiles / core.device_models
     │   (what the hardware is — never customer tolerance)
     ▼
[3] NORMALIZATION           backend/app/services/normalization.py
     │   raw count -> displacement.value_mm  (standard field)
     ▼
[4] RUBBER THICKNESS TEMPLATE  core.application_templates + processors/rubber_thickness.py
     │   displacement -> thickness (calibrated), deviation, PASS/FAIL
     ▼
[5] DATABASE + RULE ENGINE  core.template_instances/config, metrics.measurements,
     │                      metrics.raw_device_messages (pipeline + rules)
     ▼
[6] DASHBOARD               template dashboard metadata + services/dashboard.py
```

| Block | Responsibility | Files / tables |
|---|---|---|
| 1 Connector | bytes ↔ raw counts, RS-485 framing | `app/drivers/cd22_connector.py` |
| 2 Profile | manufacturer, series, model, range, resolution | `core.device_profiles`, `core.device_models` |
| 3 Normalization | raw → `displacement.value_mm` | `app/services/normalization.py` |
| 4 Template | reusable app + configurable calibration | `core.application_templates`, `processors/rubber_thickness.py` |
| 5 DB + rules | store + deviation/PASS-FAIL/statistics | `core.template_instances`, `core.template_config`, `metrics.measurements`, `metrics.raw_device_messages`, `app/services/processing.py` |
| 6 Dashboard | metadata widgets + values | `app/services/dashboard.py`, template `dashboard` JSON |

**Connector output (customer-agnostic):**
```json
{ "device_id": "DEV001", "timestamp": "...", "measurement_raw": 151, "communication_status": "online" }
```
**Standard field after normalization:** `displacement.value_mm = 151 × 0.01 = 1.51`

**Calibration (never hardcoded):** `thickness.actual_mm = calibration.scale × displacement + calibration.offset`

---

## Onboarding flow (the app)

```
Login → My applications (resume drafts or open live dashboards)
  → New solution (compatible device + application catalog)
  → Asset & data source (gateway, customer API, database or HTTP push)
  → Map data (preset or sample → paths, units, scale/offset, preview)
  → Commission (two-point calibration + verification, input filter)
  → Limits & alerts
  → Graphs (available/locked widgets, drag/resize, saved per instance)
  → Activate (checklist)
  → Live dashboard → CSV export / shift report / clone / change history
```

The customer dashboard never shows RS-485 addresses or registers — only
thickness, target, pass rate and device health.

---

Current implementation, validation and the separate Dell/Mac copies: [delivery notes](docs/DELIVERY.md).

## Requirements

- Python 3.12+ with: `fastapi`, `uvicorn`, `psycopg2-binary`, `bcrypt`, `itsdangerous`
- PostgreSQL 14+ (localhost)
- Optional (real hardware only): `pyserial`
- Optional (Gmail OAuth email alerts): `google-auth-oauthlib`, `google-api-python-client`

## Run

```powershell
# from the project root
./run.ps1            # creates the DB (if needed), applies schema+seed, starts the API
```

Or manually:

```powershell
cd backend
python scripts/init_db.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** — the SPA and the API are served from the same origin.

**Demo login:** `admin@demo.local` / `demo1234` (company *ABC Rubber*).
A second seeded tenant for the "zero code change" test: `admin@xyz.local` / `demo1234` (*XYZ Polymers*).
API docs: **http://127.0.0.1:8000/docs**

Config is read from `backend/.env` (see `.env.example`). `ENABLE_SIMULATOR=1`
makes the connector generate readings so the whole platform runs without a CD22.

**Gmail OAuth email alerts.** Create an OAuth 2.0 Client ID (Web application) in
Google Cloud Console and add the redirect URI shown in the Email alerts panel
(defaults to `PUBLIC_BASE_URL` + `/api/v1/email-alerts/oauth-callback`; with the
`run.ps1` default that is `http://127.0.0.1:8000/api/v1/email-alerts/oauth-callback`).
Then set `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` in `backend/.env`.
The *Limits & alerts* step of an application shows **Email alerts (Gmail)** for
company administrators. Settings and tokens are stored per company under
`backend/data/email_alerts/` (gitignored).

## Real hardware

1. Install `pyserial`, wire the CD22 to the gateway's RS-485 port.
2. In the onboarding *Device connection* step, untick **Simulate** and set the
   serial port (`COMx` / `/dev/ttyUSB0`), baud rate (default 38400) and address.
3. Your edge gateway posts raw readings:

```bash
curl -X POST http://<server>/api/v1/gateway/measurements \
  -H "X-Ingest-Key: <device ingest_key>" -H "Content-Type: application/json" \
  -d '{"device_id":"DEV001","measurement_raw":151}'
```
   Everything above the connector is unchanged between simulation and hardware —
   that is the V1 acceptance test.

## Key API endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/auth/login` | sign in |
| GET | `/api/v1/catalog/device-profiles` | BLOCK 2 profiles |
| GET | `/api/v1/catalog/device-models?search=` | device catalogue search |
| GET | `/api/v1/catalog/device-models/{key}/purposes` | purposes for a device |
| GET | `/api/v1/catalog/purposes/{key}/templates` | templates for a purpose |
| POST | `/api/v1/devices` | register a device + connection |
| POST | `/api/v1/devices/{id}/test` | test the connector |
| POST | `/api/v1/instances` | create a template instance |
| POST | `/api/v1/instances/{id}/calibrate` | two-point calibration |
| PATCH | `/api/v1/instances/{id}` | set quality limits |
| POST | `/api/v1/instances/{id}/activate` | activate |
| GET | `/api/v1/instances/{id}/dashboard` | BLOCK 6 dashboard data |
| GET/PUT | `/api/v1/email-alerts/config` | company email-alert settings (Gmail OAuth) |
| POST | `/api/v1/email-alerts/test` | send a test email |
| GET | `/api/v1/email-alerts/auth-url` | start Google sign-in |
| GET | `/api/v1/email-alerts/oauth-callback` | Google OAuth callback |
| GET | `/api/v1/events/active` | current out-of-range / offline incidents |
| GET | `/api/v1/events/incidents` | historical incidents (filters, `from`/`to`) |
| GET | `/api/v1/events/incidents/{id}` | incident details + queued notifications |
| GET | `/api/v1/events/counts` | daily incident counts and summaries |
| GET | `/api/v1/events/connectivity` | machine / source connectivity status |
| POST | `/api/v1/events/connectivity/check` | run the freshness check now |
| POST | `/api/v1/gateway/measurements` | gateway ingest (raw) |

## Event engine (Phase 3)

Out-of-range incidents are evaluated after each processed measurement. Thresholds
come from the existing limit configuration (`thickness.lower_limit_mm` /
`thickness.upper_limit_mm`, or the template's `field_roles.lower` / `upper`).
Per-sensor overrides can be set with `event.sensor_limits` (JSON keyed by sensor
field) or `<field>.lower_limit` / `<field>.upper_limit`.

Optional recovery configuration is set through the normal instance config API:
`event.hysteresis_mm`, `event.debounce_readings`, `event.debounce_seconds`,
`event.recovery_readings`, `event.recovery_seconds`, `event.severity`;
connectivity uses `connectivity.heartbeat_seconds` (or
`connectivity.freshness_seconds`). Incidents are stored independently of
`metrics.measurements`, and opened / closed events are queued in
`metrics.event_notifications` for future WhatsApp delivery. No messages are sent.

## Rajdeep AI Assistant (read-only)

The assistant answers questions about the current company's applications,
readings, statistics, limits, alerts, calibration, connectivity and reports. The
web UI is the floating **Rajdeep AI Assistant** panel; the same channel-neutral
service is used by the WhatsApp adapter.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/ai/chat` | authenticated web chat |
| POST | `/api/v1/whatsapp/link-code` | generate a 15-minute code to link a WhatsApp number |
| GET | `/api/v1/whatsapp/identities` | list linked WhatsApp numbers (company-scoped) |
| DELETE | `/api/v1/whatsapp/identities/{id}` | block a linked number (admin) |
| GET | `/api/v1/whatsapp/webhook` | Meta webhook verification |
| POST | `/api/v1/whatsapp/webhook` | signed Meta webhook |
| POST | `/api/v1/whatsapp/test-message` | local end-to-end test in `mock` mode (admin) |

Configuration is in `backend/.env.example`: `AI_PROVIDER` (openai-compatible,
anthropic, or disabled), `AI_API_KEY`, `AI_MODEL`, `AI_BASE_URL`, and the
`WHATSAPP_*` variables. Keys stay server-side and are never returned by the API.
While the Meta developer account is not ready, keep `WHATSAPP_DRIVER=mock`:
replies are stored in `core.ai_outbox` and logged, not sent. After the account is
ready, set `WHATSAPP_DRIVER=graph`, the verify token, app secret, access token and
phone-number id, then point the Meta webhook at
`https://<host>/api/v1/whatsapp/webhook`.

The assistant is read-only: it cannot change limits, calibrate, acknowledge
alerts or control equipment.

## Acceptance criteria (V1)

- **A** Connect a physical CD22-35 over RS-485 and obtain measurements.
  *(simulated end-to-end verified; set `simulate=false` + `pyserial` for hardware)*
- **B** The connector knows nothing about thickness tolerances or customer rules.
  *(it returns only `measurement_raw`; rules live in BLOCK 4/5)*
- **C** Thickness is computed by a reusable template with configurable calibration.
  *(verified: scale/offset derived from two references)*
- **D** A second rubber customer is created purely by configuration.
  *(verified: ABC Rubber @ 8.5 mm and XYZ Polymers @ 4.0 mm, same code)*
- **E** Approved widgets are arranged without touching code.
  *(widget layout is stored as JSON metadata in the template dashboard)*

## Layout

```
backend/
  app/
    drivers/cd22_connector.py     BLOCK 1
    services/normalization.py     BLOCK 3
    processors/rubber_thickness.py BLOCK 4
    services/processing.py        BLOCK 5 pipeline + rules
    services/dashboard.py         BLOCK 6
    services/{devices,instances,calibration}.py
    services/event_engine.py      reusable incident state machine
    services/incidents.py         incident persistence, connectivity, summaries
    services/ai_assistant/        read-only AI chat, analytics tools, WhatsApp adapter
    services/email_alerts.py      Gmail OAuth delivery (optional)
    routers/{auth,catalog,devices,instances,gateway,meta,email_alerts,events,ai,whatsapp}.py
    simulator.py                  runs the real connector in simulation
  database/{schema.sql,seed.sql}
  scripts/init_db.py
frontend/                         dependency-free SPA
run.ps1
```

## Verification

From `backend/`, run `python -m pytest tests -q` (install `pytest` for development).
With the demo API running, run `python scripts/e2e_mvp.py --base http://127.0.0.1:8000`.
The acceptance script creates demo devices and instances for both seeded tenants; run it against a demo database.
It covers gateway simulation, customer API pull, HTTP push, mapping errors, widget restrictions,
layout persistence, cloning, CSV, reports, audit, tenant isolation, offline recovery and Generic Monitoring.

`python backend/run.py` respects `APP_HOST` and `APP_PORT` from the environment or `backend/.env`.
The vendored GridStack files and licenses support offline dashboard editing; charts use local SVG renderers.
