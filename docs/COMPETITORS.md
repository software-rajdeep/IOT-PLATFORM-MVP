# Competitor Research

Last updated: 2026-10-12. Sources are the companies' own websites (fetched 2026-10-12) plus public news. Claims
such as "35% less downtime" are their marketing, not verified.

## 1. The five companies on our list

### CountWONDER (Pentaforce Printing Software, Mumbai)
- **What:** OEE and production monitoring. "AI Insights" add-on: downtime pattern detection, predictive alerts,
  AI-written shift/daily reports with root-cause hints.
- **Hardware:** own AIoT sensors plus "CountWONDER Mesh" wireless network between machines. Counts strokes/sheets
  and run/stop state; no PLC integration claimed.
- **Industry:** printing and packaging (labels, cartons, pharma inserts, corrugation, flexibles).
- **Delivery:** SaaS on their cloud or AWS, or on-prem private cloud. Reports delivered by dashboard, **WhatsApp**
  and PDF. ISO 27001 storage.
- **Takeaway:** narrow vertical, simple signal (count + state), sells the report, not the data.
- https://www.countwonder.com/ , https://www.countwonder.com/ai_reporting.html

### Altizon (Pune, founded 2013; Datonis platform, now "DFX")
- **What:** the most mature Indian player. Started as a horizontal IIoT platform (Datonis: connect, ingest,
  dashboards, predictive maintenance), moved up to an "AI-native MES" (DFX Digital Factory Suite).
- **Modules:** real-time OEE, automated downtime analysis, digital quality checks, energy/water/utility intensity,
  predictive maintenance, GenAI assistant, batch consistency, audit readiness.
- **Connectivity:** Datonis Edge sits on the shop floor, connects PLCs, SCADA, sensors, multi-OEM lines, and ERP.
  Pre-processes at the edge.
- **Industry / customers:** beverages and bottling, alcobev, packaged food. Named: Varun Beverages, Pernod Ricard,
  JK Tyre, Dover, Suhana Spices, Sundaram Clayton. Wipro Ventures-backed.
- **Delivery:** "live in 4 weeks", cloud, standardize across 30+ plants.
- **Takeaway:** shows the trajectory: generic IoT platform did not sell on its own, so they packaged it into
  vertical outcomes (OEE, quality, sustainability) for enterprise multi-plant buyers.
- https://altizon.com/

### Intelify (intelifylabs.in, early-stage, founders ex-Goldman / ex-Google, BITS '24)
- **What:** "AI manager for the factory." Digital twin view of every machine (state, job, OEE, part count, cycle
  time) plus a **WhatsApp chatbot** in English/Hindi/Hinglish answering "how much did we make today?" Anomaly and
  at-risk-order alerts.
- **Data:** connects to the customer's **ERP** (example: 682 tables, refreshed every minute) plus IoT devices on
  machines (spindle load, status, part count).
- **Industry:** tier-1 auto parts (CNC, forging, inspection). One pilot.
- **Delivery:** 4 weeks (week 1 factory mapping + ERP connection, weeks 1 to 3 build + sensor install, week 4
  go-live on WhatsApp). Explicitly "built for your factory, not a template."
- **Takeaway:** the differentiator is ERP + machine data joined, with an LLM interface. Heavily customized per
  customer, which is hard to scale.
- https://www.intelifylabs.in/

### Maya OS (mayaos.in; hardware brand Weneura Future Tech)
- **What:** a full manufacturing ERP (claims Odoo module parity: sales, finance/GST, inventory, MRP, HR, payroll)
  with built-in IIoT and an AI co-pilot.
- **Hardware:** own sensors and gateways retrofitted to legacy machines: vibration, temperature, power, cycle time,
  OEE, andon. Protocols claimed: Modbus, OPC UA, MTConnect, EtherNet/IP, Profinet, digital/analog I/O.
- **Edge:** local gateway buffers during internet outages and syncs on reconnect.
- **Industry:** brass/casting, steel, FMCG, pharma, plastics, packaging, auto.
- **Pricing model (stated):** fixed monthly/annual fee **per plant and per node**, no per-user fees. WhatsApp
  Business messaging billed separately.
- **Delivery:** software in 48 hours, hardware commissioning 5 to 10 working days.
- **Takeaway:** going wide (ERP replacement). Interesting pricing model; very broad claims for a small company.
- https://mayaos.in/

### Soul Automation (Tamil Nadu / Kerala, since 2018)
- **What:** turnkey IoT monitoring and data logging: sensors, controllers, MQTT connectivity, dashboards,
  threshold/offline alerts, history with CSV export for compliance audits.
- **Signals:** temperature, humidity, energy, tank level, pressure, machine state; DG sets, chillers, pumps
  (including auto pump control).
- **Delivery:** requirement study, then pilot on critical points, then install and commission, then dashboards
  and scale line by line. Claims 1,650+ field installations.
- **Takeaway:** closest to our business model: an automation integrator that delivers hardware + software as one
  system. Utility/facility focus, not quality measurement.
- https://soulautomation.com/industrial-iot-data-logging/

## 2. Side by side

| | CountWONDER | Altizon | Intelify | Maya OS | Soul | **Rajdeep (planned)** |
|---|---|---|---|---|---|---|
| Core outcome | OEE, shift reports | MES: OEE, quality, energy | AI manager, ERP + machines | ERP + IoT | Utility monitoring, logging | **In-line quality measurement**, then OEE/energy |
| Own hardware | Yes (counters, mesh) | No (edge software) | Sensors installed | Yes (Weneura) | Yes | **Yes, we distribute it** (OPTEX, etc.) |
| PLC / protocols | Not stated | PLC, SCADA, ERP | ERP, IoT | Modbus, OPC UA, MTConnect, EIP, Profinet | MQTT | RS-485 today; Modbus/OPC UA/MQTT planned |
| ERP integration | No | Yes | Core | Is the ERP | No | Planned (customer DB/API connector exists) |
| WhatsApp | Reports | — | Chatbot (Hinglish) | Notifications | Push alerts | Not yet |
| AI | Pattern/predictive | Predictive + GenAI | LLM Q&A | Co-pilot | No | Not yet |
| Offline / on-prem | On-prem option | Edge | — | Edge buffering | — | **Offline appliance exists** (thickness-monitoring) |
| Go-live claim | — | 4 weeks | 4 weeks | 48 h SW, 5–10 days HW | Pilot first | — |
| Vertical | Printing/packaging | Beverages, food | Auto parts | Broad | Facilities, utilities | Rubber, then customers we already sell sensors to |

## 3. What the market tells us

1. **Nobody sells a "generic IoT platform" to Indian SMEs.** Every one of them sells an **outcome** (OEE number,
   shift report, downtime reasons, quality pass rate, audit log). Altizon is the proof: Datonis (generic) became
   DFX (vertical MES). Our platform should be generic **inside** and sold as **solutions outside**.
2. **The table stakes are now:** live dashboard, threshold + offline alerts, history + CSV/PDF export,
   **WhatsApp delivery** (reports and alerts), shift-wise reporting. We have the first item only.
3. **"Live in 4 weeks" is the standard promise.** A pilot on one line, then scale. Our onboarding flow must make a
   one-line pilot possible in days.
4. **AI is a marketing must-have but a thin layer.** Real value comes from clean, structured data (which our
   template + mapping design gives us). A WhatsApp/LLM "ask your factory" layer on top of structured data is cheap
   to add later and is what Intelify and Maya lead with.
5. **Hardware ownership is common** (CountWONDER, Maya, Soul). Being the sensor supplier is not unique, but our
   **catalog breadth** (laser displacement, vision, encoders, etc.) and existing customer base are.
6. **No one in this set focuses on measurement quality** (thickness, width, gap, profile, SPC, Cp/Cpk, calibration
   traceability). That is the gap that fits the CD22 work and the sensors we sell.
7. **Pricing signals:** Maya charges per plant + per node, not per user. European OEE SaaS runs about EUR 30 to 150
   per machine per month plus EUR 200 to 2,000 hardware per machine ([Teeptrak](https://teeptrak.com/nl/oee-software-kosten/)).
   Indian pricing is not public; expect well below that.

## 4. Others worth tracking (not yet researched in depth)

- **Indian:** Faclon Labs (IOsense), Infinite Uptime (predictive maintenance, Tiger Global-backed), Embedsense
  (wireless sensors + machine monitoring), Zenatix (energy), Hiotron (IIoT gateways).
- **Global reference products:** ThingsBoard (open-source platform, could be a build-vs-buy option), HighByte
  Intelligence Hub (data modelling/mapping, closest to our mapping layer), Litmus Edge, Ignition, Tulip.

## 5. Open research questions

- Actual price points of Indian competitors (ask for quotes as a prospect).
- Which of Rajdeep's existing customers already run any of these, and what they dislike.
- Which sensor families we sell most per industry (db2 `product_industry_affinity`) to choose the next templates.
