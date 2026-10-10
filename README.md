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
Login
  → Select device model (catalogue search)
  → Select purpose
  → Select application (rubber_thickness_v1)
  → Device connection (gateway, RS-485 address, baud, poll; TEST DEVICE)
  → Calibration (two known references → derive scale/offset)
  → Quality limits (nominal / lower / upper)
  → Activate
  → Dashboard (Current, Target, Trend, Statistics, Quality, Device health)
```

The customer dashboard never shows RS-485 addresses or registers — only
thickness, target, pass rate and device health.

---

## Requirements

- Python 3.12+ with: `fastapi`, `uvicorn`, `psycopg2-binary`, `bcrypt`, `itsdangerous`
- PostgreSQL 14+ (localhost)
- Optional (real hardware only): `pyserial`

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
| POST | `/api/v1/gateway/measurements` | gateway ingest (raw) |

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
    routers/{auth,catalog,devices,instances,gateway,meta}.py
    simulator.py                  runs the real connector in simulation
  database/{schema.sql,seed.sql}
  scripts/init_db.py
frontend/                         dependency-free SPA
run.ps1
```
