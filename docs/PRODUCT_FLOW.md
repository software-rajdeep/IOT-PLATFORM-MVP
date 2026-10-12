# Product Flow and Generalization Strategy

Last updated: 2026-10-12. Answers two questions: what the end-to-end flow is (sales to live system), and how to
generalize when every customer needs some customization. Builds on `../README.md` (architecture),
`../ROADMAP.md` (code status) and `COMPETITORS.md` (market).

---

## 1. The core idea: generic inside, solution outside

The customer never buys "an IoT platform". They buy a solution: "live rubber thickness with PASS/FAIL and shift
reports on Line 2". Inside, that solution is assembled from reusable parts. Customization happens in **layers**,
and each layer has a different cost.

| Layer | What it is | Who changes it | Cost per new customer |
|---|---|---|---|
| L0 Platform core | Auth, tenants, assets, ingest, storage, alerts, reports, dashboard engine | Dev team only | Zero |
| L1 Libraries | Device profiles (CD22-15/35/100...), connectors, solution templates, widget library | Dev team, once per device/solution | Zero once built |
| L2 Customer configuration | Assets, data sources, mappings, calibration, limits, recipes, dashboard layout, alert rules | Rajdeep engineer (later the customer) through the UI | Hours |
| L3 Customer extensions | A custom formula, a one-off widget, an ERP pull, a custom report | Rajdeep engineer with low-code tools | Days, billed |
| L4 Custom code | Genuinely new device protocol or new solution | Dev team | Weeks, becomes a new L1 item |

**Rules:**
- Every customer request is first answered at the lowest layer possible.
- Anything built at L3 or L4 for one customer is built so it can become an L1 library item. The second customer
  who asks for it pays nothing extra.
- Target: 80% of a deployment is L0+L1, 15% L2, at most 5% L3. Track this per project. If L3 keeps growing, a
  library item is missing.

This is the answer to "it isn't a plug-and-play SaaS but we want to generalize": customization is allowed, but
only as **configuration and extensions on top of fixed libraries**, never as forked code per customer.

---

## 2. End-to-end flow

Two flows run in parallel: the **business engagement** (what our team does) and the **software flow** (what the
platform does). Every engagement step has a matching screen or tool.

### 2.1 Engagement flow

```
1 Lead              existing sensor customer, inbound, or industry campaign
2 Discovery         site survey: lines, machines, existing PLC/ERP, pain point, what "good" looks like
3 Solution design   pick solution template(s) + sensors from catalog + gateway; list what is L2/L3
4 Proposal          hardware BOM + commissioning fee + monthly subscription; pilot scope (1 line)
5 Install           mount sensors, wire RS-485/PLC, install gateway (edge agent), network check
6 Commission        onboard in the platform: connect, map, calibrate, set limits, verify, activate
7 Pilot (2-4 wks)   customer uses dashboard + reports; we tune limits, alerts, layout
8 Go-live + scale   sign-off, more lines/plants are cloned from the pilot configuration
9 Support/expand    remote health monitoring, recalibration reminders, upsell next template
```

Target: steps 5 to 7 in under 2 weeks for a template we already have. That beats the "4 weeks" competitors promise.

### 2.2 Software flow (what the onboarding UI must do)

Summary only. The detailed customer-facing version (product, then application, then connect, map, graph options) is
section 3.

```
A  Tenant setup       company -> plant -> area -> line -> asset   (ISA-95 hierarchy)
B  Solution pick      choose a solution template (e.g. Rubber Thickness, Opposite-side)
                      -> shows required inputs, sensor options and roles (sensor A, sensor B, reference)
C  Device / source    per role: catalog device (CD22-35) via our edge agent,
                      OR external source (PLC tag, MQTT topic, customer API, customer DB)
D  Mapping            direct device: automatic preset (skipped)
                      external: capture samples -> map fields -> units/transforms -> preview
E  Commissioning      connection test, calibration wizard (2+ reference pieces), verification piece
F  Rules              target/limits (or a recipe table per product), warning bands, alert rules
G  Activate           validation gate: all required inputs mapped, calibrated, limits set
H  Dashboard          template default layout -> customer drags/resizes/removes approved widgets
I  Outputs            shift/daily report (PDF + WhatsApp), alerts (WhatsApp/email), CSV export
J  Clone              copy a working line config to another line (only device IDs + calibration change)
```

Steps A to G are what exists today, partially (see `../ROADMAP.md`). D, H (customization), I and J are missing.

**Step J matters most commercially.** Line 2 to Line 10 must be a clone plus recalibration, not a new project.

### 2.3 Who does what in the software

| Role | Sees | Does |
|---|---|---|
| Rajdeep engineer | Everything incl. connection, mapping, raw data | Steps A to G, L3 extensions, remote support |
| Customer admin / quality head | Dashboards, rules, reports, users | Limits, recipes, alert recipients, dashboard layout |
| Operator | One line's live screen | Acknowledge alerts, record reasons (later) |
| Management | Plant summary, reports | Read only, WhatsApp reports |

Early on, Rajdeep engineers do all commissioning. Self-service for customers comes later and is not needed to sell.

---

## 3. Catalog-driven onboarding (agreed model, 2026-10-12)

The model: we keep **100+ ready solutions** to show the customer. Each customer goes through an onboarding flow:
select product, select application, connect data, we map it to our template, and they then see the graph options
that apply to them.

### 3.1 The one change that makes 100+ maintainable

Do not hand-build 100+ templates. Build **three small libraries** and let the platform **compute** the 100+
solutions as the compatible combinations.

```
Device library        ~30 product families   each declares the SIGNALS it emits
Application library   ~15-20 applications    each declares the SIGNALS it needs + what it computes
Widget library        ~25 graph types        each declares the FIELDS it needs to draw

Solution catalog  =  every (device, application) pair where the device's signals satisfy the
                     application's required signals          ->  30 x 15 easily gives 100+ listings
Graph options     =  every widget whose required fields exist in (mapped inputs + computed outputs + config)
```

So the customer sees "OPTEX CD22-35 · Rubber Thickness", "OPTEX CD22-35 · Height Check",
"OPTEX CD22-100 · Edge Position", "Energy Meter · Line Energy", ... as ready products. Behind them we maintain
about 15 application templates, not 100. Adding one device family adds a whole row of solutions automatically.
Adding one application adds a column.

A solution listing can still be **curated**: display name, photo, "recommended", "field-tested", hidden. Curation
is a row of data, not a new template.

### 3.2 Signals: the shared vocabulary

Everything matches through a fixed list of signal kinds with a dimension (from the unit registry):

| Signal kind | Example | Emitted by |
|---|---|---|
| `displacement` (length) | 1.502 mm | CD22, CD33, any laser/ultrasonic distance sensor |
| `presence` (boolean) | object yes/no | photoelectric, proximity |
| `count` (counter) | parts, strokes | photoelectric, PLC counter, encoder |
| `machine_state` (enum) | run / idle / down | digital input, PLC tag, current sensor |
| `speed` (rate) | 12 m/min | encoder, PLC |
| `temperature`, `pressure`, `level` | 78.3 degC | analog transmitters, Modbus |
| `power`, `energy` | kW, kWh | energy meters |
| `inspection_result` (pass/fail + values) | OK, 4.98 mm | vision sensors (Cognex etc.) |

Device profile example: `optex_cd22_rs485 emits displacement(mm), output_state(bool)`.
Application example: `rubber_thickness_opposite needs displacement x2 (roles sensor_a, sensor_b); optional speed, product_code`.

### 3.3 Customer onboarding flow (screens)

```
1 Select product      search our catalog (CD22-35...)
                      or "Other brand sensor" (generic profile: pick signal kind + unit)
                      or "I already have the data" (PLC / SCADA / database / API / CSV)
2 Select application  only applications compatible with that product's signals
                      each card previews the dashboard they will get
3 Asset + sensors     plant / line / machine; how many sensors and which role each plays
4 Connect data        a) via Rajdeep gateway   -> auto-connected, preset mapping, step 5 skipped
                      b) their own data        -> HTTP push (we give URL+key), API pull, MQTT, DB, CSV upload
5 Map                 required fields first (block activation until mapped)
                      optional fields show what they unlock:
                        "map $.line_speed to unlock: Thickness vs Speed, Material Usage"
                      live preview: raw value -> mapped -> computed result
6 Configure           calibration wizard, target/limits or recipe table, alert rules
7 Graph options       widget catalog filtered for THIS customer:
                        Available  - all needed fields present   (default layout pre-placed)
                        Locked     - "needs line speed" with a link back to step 5
                      customer adds / removes / drags / resizes
8 Activate            then reports + alerts (WhatsApp / email)
```

### 3.4 How graph options are resolved

Each widget type declares what it needs. Example for a thickness customer:

| Widget | Needs | Shown when |
|---|---|---|
| Current value | 1 numeric output | always |
| Trend with tolerance band | numeric output + target + limits | limits configured |
| Histogram / distribution | numeric output, 100+ samples | always (empty state until data) |
| Pass / fail donut, pass % | `quality.status` | limits configured |
| Cp / Cpk card | numeric output + both limits | limits configured |
| Cross-profile (A vs B) | 2+ sensor roles | opposite / multi-sensor layouts |
| Thickness vs line speed | `speed` mapped | optional field mapped |
| Per-product / per-batch report | `product_code` or `batch` mapped | optional field mapped |
| Shift summary | timestamp + shift calendar | shift calendar configured |
| Device health | device heartbeat | data comes via our gateway |

Rule: `available = widget.requires is a subset of (mapped inputs + template outputs + config keys)`. No widget is
ever coded for a specific customer. More data mapped means more graphs, which is also a natural upsell.

### 3.5 Safety nets

- **Generic Monitoring application:** any numeric signal gets trend, min/max/avg, limits and alerts. Every customer
  gets a working dashboard on day one even when no specific application fits. Their request then tells us which
  application to build next.
- **"Other brand" devices:** the generic profile means a competitor's sensor or a PLC value still matches
  applications by signal kind. The application does not care who made the sensor.
- **Compatibility is checked twice:** at catalog time (device signals vs application) and at activation time
  (actually mapped fields vs required fields).

### 3.6 Data model additions (on top of ROADMAP section 4)

```
core.signal_kinds(kind, dimension, default_unit)
core.device_profiles      + signals JSONB          [{kind, unit, role_hint}]
core.application_templates+ requires JSONB, optional JSONB, outputs JSONB
core.widget_types(widget_type, requires JSONB, config_schema JSONB, renderer)
core.solution_catalog(profile_id, template_id, display_name, image, status, sort)  -- curation only;
                      the compatible pairs themselves are computed by a query/view
core.instance_dashboards(instance_id, user_id NULL, layout JSONB)                  -- customer layout
```

Today's code already has the start of this: `device_model_purposes` (product to purpose), templates per purpose,
and dashboard JSON per template. The change is to match by **signals** instead of hand-wired rows, and to resolve
widgets by **fields** instead of a fixed list.

### 3.7 Worked examples

**A. Our sensor, our gateway.** Customer picks CD22-35, then "Rubber Thickness, opposite side". Asset: Extruder 2,
2 sensors (A, B). Connect: Rajdeep gateway, auto-detected. Mapping skipped (preset). Calibration with 2 reference
pieces, limits 8.3 to 8.7. Graph options: 9 available, 2 locked ("line speed", "product code"). Live in one visit.

**B. Their existing data.** Customer already logs thickness from another brand's gauge into their SCADA database.
Picks "I already have the data", then "Generic displacement / thickness", then "Rubber Thickness, single value".
Connect: API pull from their system. Samples captured, `$.gauge.thk_um` mapped to `thickness` with um to mm,
`$.prod` mapped to `product_code` (unlocks per-product report). No hardware sold, software subscription only.

**C. No matching application.** Customer wants to watch a chiller's inlet temperature. No chiller application yet,
so Generic Monitoring gives trend, limits and alerts. Logged as demand for a "Utility / chiller" application.

---

## 3A. What an application template contains

An application template is the unit we build once and reuse across all compatible devices. Everything in it is data,
not code, except the processor function.

```
solution template  rubber_thickness_opposite_v1
  inputs        roles: sensor_a, sensor_b   canonical field: displacement.value (mm)
  compatible    device profiles: optex_cd22_rs485 (any model) | any source mapped to mm
  processor     thickness = gap - dA - dB        (code, registered once)
  config        gap / calibration, filter window, target, limits, warning bands, recipes
  outputs       thickness.actual, deviation, quality.status, stats, Cp/Cpk
  widgets       current value, trend with bands, histogram, pass/fail, stats, device health
  default dash  layout JSON
  alerts        FAIL streak, out-of-range, device offline
  reports       shift report template (PDF/WhatsApp)
  commissioning calibration wizard type, verification steps
```

### Candidate template library (to validate against sales data)

| Template | Sensors we sell | Signal | Likely buyers | Effort |
|---|---|---|---|---|
| Thickness (side-by-side / opposite / reference) | OPTEX CD22, CD33 | displacement | rubber, plastics film, sheet, tyre | In progress |
| Width / edge position | laser / line sensors | displacement x2 | sheet, web, textile | Low (reuse) |
| Height / presence / fill check | displacement, photoelectric | displacement / boolean | packaging, bottling | Low |
| Machine state + count + OEE | photoelectric, proximity, PLC tags, digital I/O | boolean + counter | every discrete manufacturer | Medium, highest demand |
| Energy monitoring | energy meters (Modbus) | kW, kWh | everyone | Medium (Modbus connector) |
| Vision inspection results | Cognex / smart cameras | pass/fail + measurements | packaging, pharma, auto | Medium |
| Utility / environment logging | temperature, pressure, level | analog | pharma, food, facilities | Low once Modbus/analog exists |

**How to pick the order:** query db2 `product_industry_affinity` and `product_master_unified` for which sensor
families we sell the most and to which industries. Build the template for the biggest overlap of "we already sell
the sensor" and "industry needs the outcome". OEE (state + count) is what every competitor leads with, so we need
it as a second template whatever the data says.

---

## 4. Platform features beyond templates (table stakes)

From `COMPETITORS.md`, the minimum every Indian competitor offers, in priority order:

1. **Alerts:** threshold, FAIL streak, device offline, delivered by WhatsApp + email, with acknowledge.
2. **Shift and daily reports:** auto-generated PDF, sent on WhatsApp at shift end.
3. **History + export:** date-range CSV/PDF for audits.
4. **Edge buffering:** gateway stores data during internet outages, syncs later (thickness-monitoring has the
   offline appliance already).
5. **Multi-line / multi-plant view:** plant summary across all instances.
6. **Recipes:** product code to target/limits, switched by operator or PLC/ERP signal.
7. **AI layer (later):** "ask your line" on WhatsApp, anomaly explanations. Only after the data is clean.

---

## 5. Commercial packaging (proposal, to discuss)

| Item | Model |
|---|---|
| Hardware | Sold as today (sensor + gateway + cabling) |
| Commissioning | One-time fee per line, fixed price per template |
| Subscription | Per line (or per instance) per month, includes cloud, alerts, reports, support |
| Offline appliance | Annual license (existing Ed25519 licensing in thickness-monitoring) |
| L3 extensions | Fixed quote, and the work becomes a library item |

Avoid per-user pricing (Maya OS markets against it). Per-line pricing matches how customers think and lets the
pilot-then-clone motion grow revenue.

---

## 6. How to start (sequence)

**Phase 0: decide (this week)**
- Agree the layering rules (section 1) and the first two templates (Thickness + OEE).
- Pick one real pilot customer and line for thickness (an existing CD22 customer is the fastest path).

**Phase 1: make thickness real (ROADMAP items 1 to 3)**
- Shared repo, edge agent on real CD22, generic storage. Pilot runs on the existing thickness-monitoring app
  meanwhile, so customers are not waiting on the rebuild.

**Phase 2: generalize (ROADMAP items 4 to 6)**
- Mapping engine + data sources, multi-sensor roles, per-customer dashboard, clone-a-line.
- Signal vocabulary, computed solution catalog, widget resolution by fields (section 3). Start with the
  device and application libraries we have (CD22 x thickness layouts + Generic Monitoring), so the mechanism is
  proven before the catalog grows.

**Phase 3: table stakes (section 4, items 1 to 3)**
- WhatsApp/email alerts, shift reports, export. This is the point where the product is sellable beyond pilots.

**Phase 4: second template**
- OEE (machine state + count) with a Modbus/digital-input connector. The test: no changes to L0.

**Phase 5: scale + AI**
- Plant summary, recipes, ERP pull, WhatsApp Q&A.

---

## 7. Open questions for the team

1. First pilot customer and line for the UCAP thickness pilot?
2. Do we keep shipping thickness-monitoring to new customers until UCAP reaches parity? (Recommendation: yes.)
3. WhatsApp Business API provider (Meta direct, Gupshup, Interakt, AiSensy)? Cost per conversation is extra.
4. Cloud hosting: Server4You (shared with Supabase, capped slice) vs a separate box for telemetry volume?
5. Pricing numbers per line: needs input from sales.
