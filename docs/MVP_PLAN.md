# MVP Plan: one product, complete flow

Status: **MVP demo implemented and verified** (2026-10-12). See `DELIVERY.md` for the current implementation, checks, deployment locations and remaining scope. The review below records the starting state; it is not the current bug list. Goal: a working, end-to-end example of what the final product looks
like, using one product. Every screen and backend mechanism should be the "real" design from `PRODUCT_FLOW.md`
section 3, only with a small library behind it. When this works, adding product number 2 is data, not code.

**MVP product:** OPTEX CD22-35-485M12, application **Rubber Thickness (single sensor)**.
Plus **Generic Monitoring** as the fallback application, because it is cheap and proves that graph options come
from fields, not from hard-coded screens.

---

## 1. Review of the current code (laptop pull, 2026-10-12 afternoon)

### Works and is worth keeping
- Clean block separation: connector, profile, normalization, processor, pipeline, dashboard.
- Catalog-driven wizard: device, then purpose, then template, then connection. Data-driven cards.
- Connectors: CD22 RS-485 (simulated) and `database_api` (direct DB or customer API) behind one driver registry.
- **Discovery:** `POST /devices/discover-source` lists tables/columns or flattens a sample JSON. The connection
  screen maps `measurement_raw`, `timestamp`, `device_id` onto them. This is the right idea in an early form.
- Two-point calibration + verification, now inside the dashboard.
- Dashboard: per-instance show/hide of sections and graph type, stored in `template_config` as `ui.*`.
- Multi-tenant (two demo companies), raw messages stored from day 1, demo customer server + database.

### What blocks the "final product" picture
| # | Issue | Where |
|---|---|---|
| 1 | **The dashboard is hand-coded for thickness** (47 thickness references, fixed section IDs). The template's widget JSON is only used for titles. Not metadata-driven, so no graph options and no second application. | `frontend/js/views/dashboard.js` |
| 2 | **Mapping is one field into `measurement_raw`**, inside the connection form. No units, transforms, preview, versioning or failure log. Customer timestamp is read but then dropped (the simulator calls `process_measurement` without it). | `drivers/database_api.py`, `simulator.py` |
| 3 | **Nothing polls non-simulated sources.** A real customer API/DB or real CD22 produces no data. Polling "latest row" every 0.5 s would also store the same row again and again (no dedupe). | `simulator.py` |
| 4 | **Storage is thickness-shaped** (`thickness_mm`, `deviation_mm` columns). | `metrics.measurements` |
| 5 | **No compatibility logic.** Product to purpose is a cross-join of every purpose; templates are hand-wired. | `seed.sql` |
| 6 | **Security:** secrets returned to the browser; `discover-source` lets any logged-in user point us at any Postgres host (including our own) and list every table; customer URL fetch is open SSRF. | `services/devices.py`, `routers/devices.py` |
| 7 | Seed ranges wrong (all CD22 models +/-15). No filtering (raw samples cause false FAILs). No alerts, reports, export, clone. No tests. | various |

### Approach: build on the existing code, no rewrite
Every item below extends what is there. Nothing is started from scratch.

| Existing piece | What happens to it |
|---|---|
| Wizard pages (`device.js`, `purpose.js`, `template.js`) | Kept. Purpose/template cards are filtered by signal compatibility instead of the cross-join. A catalog grid view is added on top. |
| `connection.js` + discovery | Kept. Its discover + field selects grow into the mapping step (more fields, units, preview), moved into its own step after connection. |
| `database_api.py`, `cd22_connector.py`, driver registry | Kept as is. They become source types in `data_sources`. HTTP push is one new endpoint. |
| `simulator.py` | Grows into the poller: same loop, also runs non-simulated sources, with dedupe. |
| `normalization.py` | Its logic becomes the CD22 preset mapping (same maths, run by the mapping engine). |
| `processing.py`, `rubber_thickness.py`, processor registry | Kept. Pipeline gets a mapping step before the processor; the processor reads canonical keys. |
| Calibration (API + dashboard dialog) | Kept. Also linked from onboarding step 5. |
| `metrics.measurements` | Columns kept. A `fields` JSONB column is added alongside, so nothing breaks during the switch. |
| `dashboard.js` (hand-coded sections) | Its existing cards, graphs, history table and calibration panel are split into widget renderers. The page then places them from the saved layout. The `ui.*` show/hide toggles become the first version of the layout. Reuse, not rewrite. |
| Schema/seed, demo customer server + DB, auth, tenants | Kept. Changes go in as new migration files on top. |

---

## 2. The MVP flow (what the user will see)

```
Login
 1  Solution catalog      cards computed from device signals x application needs
                          "OPTEX CD22-35 · Rubber Thickness"   "OPTEX CD22-35 · Generic Monitoring"
                          + curated "coming soon" cards (CD22-100 Edge Position, Energy, OEE) for the final look
 2  Asset                 plant / line / machine (ISA-95), instance name
 3  Connect data          (a) Rajdeep gateway: CD22 over RS-485 (simulated in MVP; real when hardware is there)
                          (b) Existing data:   API pull (key + secret)  |  HTTP push (we issue URL + key)
 4  Map                   (a) preset shown read-only: measurement_raw x 0.01 -> displacement (mm)
                          (b) sample capture -> table of JSON paths with example values
                              required rows: displacement, timestamp       optional rows: product_code, line_speed
                              per row: path, cast, unit (um/mm/in), scale/offset, valid range
                              live preview: raw -> mapped -> thickness -> PASS/FAIL
                              "map line_speed to unlock: Thickness vs Speed"
 5  Commission            calibration wizard (2 reference pieces + verify piece), filter (trimmed mean, window)
 6  Rules                 target / limits / warning band; optional recipe table (product_code -> target, limits);
                          alert rules: FAIL streak, out of range, source offline
 7  Graph options         widget library filtered for this instance:
                            Available (fields present)   Locked (needs X -> back to step 4)
                          default layout pre-placed; add / remove / drag / resize; saved per instance
 8  Activate              checklist gate: required fields mapped, calibrated, limits set, preview OK
 9  Live dashboard        rendered from the saved layout; alerts panel; CSV export; shift report (print/PDF)
10  Clone                 "Copy to another line": new asset + source, keeps mapping/rules/layout, asks to recalibrate
```

Demo story the MVP must support (also the acceptance test, section 6):
- **ABC Rubber** uses our CD22 through the Rajdeep gateway (simulated).
- **XYZ Polymers** already logs readings in their own system and gives us API access (the existing demo customer
  server). Their payload uses different names, micrometres and a product code.
- Both reach a working thickness dashboard. XYZ gets an extra unlocked graph (per-product report) because they
  mapped `product_code`.

---

## 3. Backend work

### B1 Foundation fixes
- Fix CD22 model ranges (15: +/-5, 35: +/-15, 100: +/-50).
- Never serialize secrets. Store connection secrets in their own column and return them masked.
- `discover-source` and API pull: block private/loopback hosts and our own DB host, enforce timeout and size limit.
- Pass the source timestamp through the pipeline.
- Numbered SQL migrations (`backend/database/migrations/NNN_*.sql`, applied by `init_db.py`), instead of growing
  `schema.sql`.

### B2 Source runtime (poller)
- One background worker that runs every active data source on its own interval: direct-device connectors
  (simulated or real), API pull. HTTP push needs no worker.
- Dedupe pulled rows by (source, source timestamp or row ID).
- Source health: `last_seen`, `last_error`, `online` (feeds the device-health widget and the offline alert).
- The current simulator becomes "a direct device whose connector is in simulate mode". There is no separate loop.

### B3 Signals and catalog
- `core.signal_kinds` + `core.units` (mm, um, cm, m, in, m/min, ...).
- `device_profiles.signals`, `application_templates.requires / optional / outputs`.
- `GET /catalog/solutions`: computed compatible (product, application) pairs + curated listing rows
  (name, image, status `available | coming_soon`).

### B4 Data sources + mapping engine (the core of the MVP)
- Tables: `data_sources`, `mapping_versions` (definition JSON, version, status), `instance_bindings`
  (instance, source, role, mapping version), `source_samples` (last N raw payloads), `mapping_failures`.
- `services/mapping.py`: pure function `map_payload(payload, mapping, contract) -> values | errors`.
  Transforms: cast, scale, offset, unit, datetime, default, valid_range, enum_map.
- Endpoints: capture samples, suggest mapping (rule-based name/type/unit match), preview (runs the full pipeline on
  one sample without storing anything), save new mapping version.
- `POST /ingest/{source_id}` for HTTP push (hashed key, any JSON body).
- CD22 preset mapping replaces `normalization.py`.

### B5 Processing
- Processor contract: `process(config, canonical_values, context) -> outputs`.
- `rubber_thickness` reads only canonical fields. Add a filter stage (trimmed mean / moving average) as config.
- `generic_monitoring` processor: pass-through + limits + PASS/FAIL.
- `metrics.measurements.fields JSONB` + `mapping_version`, `template_version`, `raw_message_id`. Old columns kept
  until the dashboard is switched over, then dropped.

### B6 Widgets and dashboards
- `core.widget_types(type, requires, config_schema)`. About 10 for the MVP: current value, target/deviation,
  trend with bands, deviation trend, histogram, pass/fail + pass %, statistics, Cp/Cpk, device health, reading
  history, per-product summary (needs `product_code`), value vs speed (needs `line_speed`).
- `GET /instances/{id}/widget-options`: available + locked with the missing fields.
- `core.instance_dashboards(instance_id, layout)`. Default layout comes from the template.
- Generic data endpoints driven by the widget's field and type: latest, series, stats, histogram, grouped-by
  field. No widget-specific SQL.

### B7 Alerts, export, reports
- `alert_rules` (per instance, from template defaults) and `alert_events`, evaluated in the pipeline; source
  offline checked by the poller.
- Email delivery optional in the MVP (in-app list first). WhatsApp is a later phase.
- CSV export with a date range (streamed). Shift report: an HTML page per shift/date, printable to PDF.

### B8 Clone instance
- `POST /instances/{id}/clone`: copies bindings (with a new source), mapping version, rules and layout; status
  `draft` until recalibrated.

### B9 Tests
- pytest unit tests: mapping transforms, processors, widget resolution, catalog compatibility.
- One end-to-end script: spins both demo customers through the API and asserts the acceptance criteria.

---

## 4. Frontend work

Keep the no-build vanilla JS SPA (fast to change, already working). Add two vendored libraries: **gridstack.js**
(drag/resize) and a small chart library (uPlot or Chart.js), served from `frontend/vendor/` so the offline
appliance keeps working.

- **F1 Wizard shell:** one stepper component for steps 1 to 8 with saved progress (resume a draft instance).
- **F2 Solution catalog:** a catalog grid view added on top of the existing device / purpose / template pages,
  which stay and get filtered by compatibility (coming-soon cards for the final look).
- **F3 Connect:** gateway vs existing data; API pull form; HTTP push panel (URL, key, a curl example, "waiting for
  first message" indicator).
- **F4 Mapping screen:** sample viewer, rows per template field, transform editor, live preview panel,
  unlock hints.
- **F5 Commission + rules:** the existing calibration dialog is also linked from onboarding (stays in the dashboard),
  limits, recipe table, alert rules.
- **F6 Graph options:** widget library sidebar (available / locked), grid canvas, save.
- **F7 Dashboard renderer:** the existing `dashboard.js` sections are split into widget renderers and placed from
  the saved layout. Existing charts and cards are reused.
- **F8 Alerts panel, export, shift report page, clone dialog.**

---

## 5. Milestones (order of implementation)

| M | Contents | Done when |
|---|---|---|
| M0 | Shared git repo (`rajdeep-automation`), commit current laptop state as baseline | History exists before big changes |
| M1 | B1 + B2 + migrations | Customer API source produces data without the simulator flag; secrets not in responses |
| M2 | B3 + B4 + B5 (backend only) + B9 unit tests | Both demo customers produce identical canonical values from different payloads (API test) |
| M3 | F1 to F5 | Full onboarding in the UI up to Activate, both data paths |
| M4 | B6 + F6 + F7 | Graph options screen and metadata-rendered dashboard; XYZ sees the extra per-product widget |
| M5 | B7 + B8 + F8 | Alerts, CSV, shift report, clone |
| M6 | E2E script, demo data polish, README/docs update | Acceptance test in section 6 passes in one run |

M2 is the riskiest part (it is the whole generalization idea), so it comes before the new UI.

---

## 6. Acceptance test (demo script)

1. ABC Rubber: catalog, then CD22-35 · Rubber Thickness, then Extruder 2, Rajdeep gateway (simulated), preset
   mapping, calibrate with 2 references, limits 8.3 to 8.7, accept default graphs, activate. Live dashboard within
   5 minutes of starting.
2. XYZ Polymers: same solution, "Existing data", API pull from the demo customer server. Sample shows
   `laser.value_um`, `ts`, `prod`. Map with um to mm, map `prod` to `product_code`. Preview shows the right
   thickness. Activate. Graph options show "Per-product summary" as available. For ABC it shows as locked.
3. Feed the same displacement into both. Both instances store the same thickness, deviation and status (same
   calibration).
4. Send a malformed payload to XYZ. It appears in mapping failures, and no bad measurement is stored.
5. Stop the ABC source. The device-offline alert fires and the health widget shows OFFLINE.
6. ABC rearranges and removes widgets. The layout survives reload. XYZ's layout is unaffected.
7. Clone ABC Extruder 2 to Extruder 3. It needs only a new source and recalibration.
8. Export CSV for a time range and open the shift report.
9. No code changes between steps 1 and 8. Product, application, widgets and mapping are all data.

---

## 7. Out of scope for the MVP
Real-hardware validation (the poller and connector are ready; test when a sensor is on the bench), multi-sensor
layouts (opposite / reference), WhatsApp, AI, roles and permissions beyond the current login, TimescaleDB,
MQTT/Modbus/OPC UA, self-service customer signup, deployment to a server.

## 8. Decisions needed before starting
1. MVP application: single-sensor Rubber Thickness (proposed), or opposite-side (two sensors, more realistic for
   our field installs, more work)?
2. Include Generic Monitoring as the second application? (Proposed: yes.)
3. Where it runs for the demo: local Mac only, or also deployed (Server4You, inside `rajdeep-apps.slice`)?
4. Repo: create `rajdeep-automation/rajdeep-iot` and work here, pushing back to the laptop only when asked?
5. ~~Replace the current onboarding screens?~~ Decided 2026-10-12: build on top of the existing screens and code,
   no rewrite (see "Approach" in section 1).
