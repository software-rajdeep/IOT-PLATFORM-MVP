# UCAP — Status Review and Roadmap

Historical baseline review: 2026-10-12. **Current MVP delivery status: [docs/DELIVERY.md](docs/DELIVERY.md).** Catalog compatibility, source polling, mapping, per-instance dashboards, alerts and reports have since been implemented. The gaps below describe the earlier baseline unless listed as remaining in the delivery note.

Review date: 2026-10-12. Covers the code in this repo (laptop copy, uncommitted work included) measured against
the design agreed with the team (the GPT design conversation: device profile, then template, then mapping, then
telemetry, then dashboard). Read `README.md` first for the architecture.

---

## 1. Where we are

The six-block skeleton exists and runs end to end, **in simulation only**:

| Block | Design intent | State in code |
|---|---|---|
| 1 Connector | Only code that knows the protocol | `drivers/cd22_connector.py` (RS-485 via pyserial), `drivers/database_api.py` (our DB / customer HTTP API). Good separation. |
| 2 Device profile | What the hardware is | `core.device_profiles`, `core.device_models`. **Seed ranges are wrong** (see 3.1). |
| 3 Normalization | raw to standard field | `services/normalization.py`, hard-coded to `measurement_raw * resolution -> displacement.value_mm`. |
| 4 Template | Reusable app maths + config | `processors/rubber_thickness.py` (`thickness = A*d + B`, deviation, PASS/FAIL). Three layout templates seeded. |
| 5 DB + rules | Store raw + processed | `raw_device_messages` stored from day 1. `measurements` has thickness-specific columns. |
| 6 Dashboard | Widget metadata, customer can rearrange | Layout JSON exists on the template. **No per-customer layout, no drag/resize.** |

Onboarding UI works: login, model, purpose, template, connection test, two-point calibration, limits, activate,
dashboard. Two tenants (ABC Rubber 8.5 mm, XYZ Polymers 4.0 mm) run on the same code, which proves acceptance
criterion D in principle.

Against the five V1 acceptance criteria:

| | Criterion | Verdict |
|---|---|---|
| A | Physical CD22-35 over RS-485 | **Not met.** Never run on hardware, and nothing polls a non-simulated device (see 2.1). |
| B | Connector knows nothing about rules | Met. |
| C | Thickness from reusable template with configurable calibration | Met for one sensor. |
| D | Second customer by config only | Met (simulated). |
| E | Customer rearranges widgets without code | **Not met.** Layout is per template, read-only. |

---

## 2. Structural gaps (fix before building more features)

### 2.1 Nothing polls real devices
The only loop that reads connectors is `simulator.py`, and it skips any device with `simulate=false`. A real
RS-485 CD22 or a real customer API (`database_api`, customer mode) therefore produces **no data** unless an
external gateway POSTs to `/gateway/measurements`, and no such gateway exists in this repo.

Needed: an **edge agent / poller runtime** that, per device, instantiates the connector, polls at
`poll_interval_ms`, buffers when offline, and posts raw payloads with the ingest key. Same process runs on a
gateway box (direct RS-485) or on the server (cloud pull from customer APIs). Simulation becomes one connector
option, not a separate loop.

`thickness-monitoring/agent/` already does this for CD22 (persistent socket, ACK check, reconnect backoff,
trimmed mean). Port it rather than rewrite.

### 2.2 The mapping layer is the main missing piece
`core.data_mappings` only links device to instance and always maps `displacement.value_mm` to itself. The gateway
accepts only `{device_id, measurement_raw}`. The `database_api` connector's `value_field` is a one-field, untyped
mapping hidden inside connector config. So a customer sending their own JSON (field names, units, timestamps)
cannot be onboarded without code.

**Update (laptop pull, 2026-10-12 afternoon):** a first step exists. `POST /devices/discover-source` lists the
tables and columns (direct DB) or flattens a sample JSON response (customer API), and the connection screen lets
you pick a table/column or JSON field for `measurement_raw`. It still maps one field to `measurement_raw` only:
no units, no timestamp/asset mapping, no versioning, no failures table.

Build the design already agreed (details in section 4):
`raw payload -> mapping engine (path + transforms) -> canonical template input -> processor`.

### 2.3 Templates cannot combine several sensors
Each reading is processed alone, per device. But:
- **Opposite-side** thickness is `gap - (offset + dA) - (offset + dB)`: two sensors at the same instant.
- **Side-by-side with reference** subtracts a drifting baseline sensor.

All three seeded layout templates call the same single-sensor `rubber_thickness` processor, so the layout choice
is currently **cosmetic**. The processor contract has to change from `(config, one_reading)` to
`(config, {role: value}, time window)`. Devices bind to an instance with a **role** (`sensor_a`, `sensor_b`,
`reference`), and the pipeline aligns readings by timestamp (nearest within N ms) before calling the processor.
thickness-monitoring already has this maths, including auto-gap; reuse it.

### 2.4 Storage is thickness-shaped
`metrics.measurements` has `thickness_mm`, `deviation_mm` columns, and `dashboard.py` queries them by name. A
second application (height, position, AP20, energy) would need schema and code changes. Move to:

```
metrics.measurements(id, company_id, instance_id, ts, template_id, template_version,
                     mapping_version, raw_message_id, fields JSONB, quality_status)
```

Keep `quality_status` and `ts` as real columns for indexing. Dashboard widgets read `fields->>'<field_key>'`
using the field named in widget metadata. Add TimescaleDB (hypertable plus continuous aggregates for 1-min and
1-hour stats) before any customer runs at 5 Hz for months.

### 2.5 Dashboard customization is per template, not per customer
**Update (laptop pull, 2026-10-12 afternoon):** per-instance show/hide toggles for each dashboard section and a
graph type choice (line / deviation / bars) now exist, stored as `ui.*` keys in `template_config`. Calibration and
limits moved into the dashboard (their separate onboarding screens were removed). Drag, resize and a widget
library are still missing.

`build_dashboard` returns `application_templates.dashboard`. Add
`core.instance_dashboards(instance_id, user_id NULL, layout JSONB, updated_at)`: template layout is the default,
saved layout overrides it. Front end needs a grid library (gridstack.js has no build step, which fits the current
SPA) plus a "widget library" panel that shows only widgets whose required fields the template produces.

---

## 3. Bugs and risks found in review

### 3.1 Correctness
- **Device model ranges are wrong in `seed.sql`.** All three CD22 models are seeded `-15..+15`. Actual:
  CD22-15 is +/-5 mm, CD22-35 is +/-15 mm, CD22-100 is +/-50 mm. Resolution also differs per model, and 0.01 mm
  for all three should be checked against the OPTEX manual. Range is what out-of-range validation will rely on.
- **CD22 framing not verified against the manual.** The connector reads 8 bytes for a 6-byte frame, does not
  check the ACK byte (0x06) or BCC, and ignores the configured RS-485 address. The thickness-monitoring agent's
  stricter version (exact-length read, `len==6`, ACK check) is proven in the field.
- **Transport mismatch.** Our field installs talk to the CD22 through a serial-to-Ethernet converter on TCP
  port 8234 (see thickness-monitoring). UCAP only supports a local serial port. Add a TCP transport to the same
  connector.
- CD22 is **single-client**. Two readers interleave bytes and break framing. The "Test device" button and the
  poller must not open the same sensor at the same time (a lock per device, or tests go through the poller).
- No filtering. The field app uses a 5 Hz trimmed mean. Raw single samples will look noisy and cause false
  FAILs. Add filtering as a template config (`filter.type`, `filter.window`).

### 3.2 Security (must fix before any customer-facing deploy)
- `serialize_device` returns `connection_parameters` in full, so **customer API secrets and DB passwords are
  sent to the browser** (`GET /devices`, dashboard device list). Store secrets encrypted in a separate column
  or table and never serialize them.
- `database_api` customer mode fetches any URL the user types (SSRF into our own network). Allow-list schemes,
  block private IP ranges, enforce a timeout and size limit.
- Defaults: `AUTH_SECRET=dev-secret-change-me`, DB password `linux`, demo API key and secret, CORS `*`. Fail
  startup if `AUTH_SECRET` is the default outside dev.
- Ingest key is stored and compared in plaintext. Hash it and use `hmac.compare_digest`.
- No roles enforced: any logged-in user can recalibrate, change limits or activate. Calibration and limit
  changes are quality-critical, so add roles (operator, engineer, admin) and write them to `core.audit_logs`
  (the table exists but nothing writes to it).
- Calibration changes overwrite config in place. Keep calibration **history** (who, when, references, before and
  after) because it is needed for ISO/IATF audits and to reprocess raw data.

### 3.3 Engineering hygiene
- No tests. The mapping engine and processors are pure functions, which makes them easy to unit-test. Start
  there.
- Schema changes are `ALTER ... IF NOT EXISTS` in `schema.sql`. Adopt a migration tool (Alembic or plain numbered
  SQL files) before the schema starts moving.
- Each reading costs about 6 to 8 DB round-trips (load device, mappings, config per instance, inserts). Cache
  device, mapping and config in memory and invalidate on change. Batch inserts.
- Not under shared version control. The laptop repo (`software-rajdeep/IOT-PLATFORM`) is not reachable from the
  `rajdeep-automation` account. Create one repo both machines can push to.

---

## 4. Data mapping: concrete build plan

This is the agreed design reduced to tables and steps that fit the current code.

### 4.1 Template input contract
Extend `core.template_fields` with `required BOOLEAN`, `canonical_unit` (from a unit registry),
`valid_min/valid_max`, and `role` (for multi-sensor templates). The processor reads **only** canonical keys.

### 4.2 Tables
```
core.units(unit_key, dimension, to_base_factor, to_base_offset)   -- mm, um, cm, m, in; degC, degF; ...
core.data_sources(source_id, company_id, type, params JSONB, secret_ref, ingest_key_hash, status)
      type = direct_device | http_push | http_pull | (later) mqtt | modbus_tcp | opcua
core.mapping_profiles(profile_id, name, template_id, source_type, company_id NULL)
core.mapping_profile_versions(id, profile_id, version, status, sample_schema JSONB, definition JSONB, created_by)
core.instance_bindings(instance_id, source_id, role, mapping_version_id, active_from, active_to)
metrics.mapping_failures(id, raw_message_id, source_id, instance_id, mapping_version_id,
                         error_code, field, message, created_at, resolved_at)
```
`core.data_mappings` is replaced by `instance_bindings`. `devices` stays for physical hardware, and a direct
device is simply a `data_source` of type `direct_device`.

### 4.3 Mapping definition (JSON, versioned)
```json
{
  "identity": {"path": "$.machine"},
  "timestamp": {"path": "$.eventTime", "format": "%Y-%m-%d %H:%M:%S", "tz": "Asia/Kolkata"},
  "fields": [
    {"target": "displacement.value", "path": "$.measurements.laser_um",
     "transforms": [{"cast": "decimal"}, {"unit": {"from": "um", "to": "mm"}}, {"valid_range": [-15, 15]}]}
  ]
}
```
V1 transforms: `cast`, `scale`, `offset`, `unit`, `datetime`, `default`, `valid_range`, `enum_map`. No formula
language yet.

The CD22 becomes a **built-in preset**: `$.measurement_raw` then `scale: <model resolution>` then
`displacement.value`. `normalization.py` goes away, and the direct and HTTP paths share one runtime.

### 4.4 Runtime (`services/mapping.py`)
`map_payload(raw, mapping_version, contract) -> {values, context, mapping_version}` or a list of errors. Pure
function, unit-tested. On error: write to `mapping_failures`, do not drop silently, do not call the processor.

### 4.5 Onboarding steps (inserted after "Application")
1. **Data source**: direct device (auto-preset, step skipped) / HTTP push (we issue URL + key) / HTTP pull
   (their URL + key + secret).
2. **Sample capture**: last 20 raw messages, flattened to JSON paths with type and example values.
3. **Field mapping**: one row per required template field, with a path dropdown, transforms and a live preview.
   Suggestions are rule-based (name similarity, type, unit). An LLM can suggest later, but an engineer always
   confirms.
4. **Preview / validate**: one sample through the full pipeline (raw, then mapped, then calibrated thickness,
   then PASS/FAIL). Unmapped required fields block activation.

### 4.6 Acceptance test
A physical CD22 (preset) and a fake customer posting arbitrary JSON (manual mapping) both feed the same Rubber
Thickness instance. Everything after `displacement.value` is byte-identical.

---

## 5. Suggested order of work

| # | Work | Why first |
|---|---|---|
| 1 | Shared git repo; fix seed ranges; strip secrets from API responses | Cheap, removes risk |
| 2 | Edge agent / poller (port from thickness-monitoring), TCP + serial transport, test on a real CD22-35 | Criterion A, nothing is real until this works |
| 3 | Generic `measurements.fields` JSONB + TimescaleDB | Every later feature depends on it |
| 4 | Mapping engine + unit registry + data sources (direct + HTTP push/pull) + failures table | Main generalization goal |
| 5 | Multi-sensor roles + time alignment; real opposite/reference processors | Makes the three layouts real |
| 6 | Per-instance dashboard layout with drag/resize | Criterion E |
| 7 | Roles, audit log, calibration history, alerts (email/WhatsApp on FAIL streaks) | Needed to sell to quality teams |
| 8 | Second device (AP20 or a Modbus device) with no core changes | Proves the platform |

---

## 6. Relationship to `thickness-monitoring/`

thickness-monitoring is the **field-proven** product: real CD22 protocol over TCP, side-by-side and opposite maths
with auto-gap, 5 Hz trimmed-mean, edge agent `.deb`, offline Windows/Linux appliance with Ed25519 licensing,
alerts and CSV export. UCAP is the cleaner, generalized architecture but is functionally behind it.

Recommendation: do not maintain two CD22 stacks. Treat thickness-monitoring as the reference implementation, and
port its protocol, agent, maths, filtering and appliance packaging into UCAP's blocks. Once UCAP reaches parity,
the existing customers move over and thickness-monitoring is frozen.

---

## 7. Market context (what customers will compare us to)

Detailed research on the Indian competitors (CountWONDER, Altizon, Intelify, Maya OS, Soul Automation):
`docs/COMPETITORS.md`. End-to-end flow, customization layers and template library: `docs/PRODUCT_FLOW.md`.

| Category | Examples | What they do | Our angle |
|---|---|---|---|
| Horizontal IoT platforms | ThingsBoard, AWS IoT SiteWise, Azure IoT, Losant | Generic devices, rules, dashboards; the customer builds the application | We ship the finished application (template) plus the sensor |
| Industrial data ops / mapping | HighByte Intelligence Hub, Litmus Edge, Kepware | Connectors, data modelling, mapping to a standard model (UNS) | Our mapping layer is the same idea, scoped to our templates |
| SCADA / HMI | Ignition (Inductive Automation), Siemens WinCC / Insights Hub | Plant-floor visualization, PLC-centric | Lighter, cloud plus offline, cheaper for one-line quality use cases |
| Frontline apps | Tulip | No-code shop-floor apps | Measurement-specific templates with calibration and SPC built in |
| Indian IIoT | Faclon Labs (IOsense), Altizon Datonis, Infinite Uptime, Zenatix | Monitoring, energy, predictive maintenance SaaS | We are a sensor distributor and integrator: hardware, commissioning and software in one PO |

Differentiators to lean into:
- **Device-plus-application templates** ("CD22-35 plus Rubber Thickness, live in a day"), which horizontal
  platforms do not offer out of the box.
- **Commissioning built in**: guided calibration, verification, audit trail.
- **Offline appliance plus cloud** from one codebase (thickness-monitoring already does this).
- **SPC / quality features** quality managers buy: Cp/Cpk, X-bar/R charts, shift and batch reports, recipe
  (product-wise target and limits switching, already present in the customer demo DB as `products`).
- Follow standards early so we are not boxed in: **Sparkplug B / MQTT** for ingest, **OPC UA** client, ISA-95
  asset hierarchy (company, plant, area, line, asset).

---

## 8. Open decisions

1. V1 source types: recommendation is direct device + HTTP push + HTTP pull. MQTT next.
2. Mapping ownership: reusable versioned profile, bound per instance (recommended) vs per instance only.
3. Edge runtime: Python agent on a Linux gateway (matches thickness-monitoring) vs an off-the-shelf edge (Node-RED,
   Litmus) feeding HTTP/MQTT.
4. Deployment: cloud on Server4You (inside `rajdeep-apps.slice`) vs per-customer appliance vs both.
5. Pricing unit: per sensor/month, per line, or bundled with hardware.
