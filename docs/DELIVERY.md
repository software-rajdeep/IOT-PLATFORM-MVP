# MVP delivery — 2026-10-12

The single-sensor Rubber Thickness and Generic Monitoring demo now has the complete
catalog → connection → mapping → commissioning → rules → graph layout → activation → live dashboard flow.
The backend was previously pushed as `f94b6b2`; the interrupted frontend work was still local.

## Delivered

- Seven-screen wizard (asset and source share one screen), with draft instances resumable from My applications.
- Gateway simulation, API/database pull and HTTP push connection panels; typed tenant-owned plants.
- Sample capture, suggested mappings, units and numeric transforms, pipeline preview and mapping failure viewer.
- Calibration/verification, trimmed-mean filter, limits and in-app alert configuration.
- Available/locked widget library, draggable/resizable GridStack layouts saved per instance, SVG charts.
- Live dashboard, alert acknowledgment, CSV date-range export, printable shift report, clone and audit history.
- Login lands on My applications. Filter edits save on Continue. Changing source settings invalidates the old
  connection test. Calibration polling clears failed readings. Dashboard polling is stopped on navigation,
  initial errors are visible, and graph reset goes through route cleanup.
- Shift windows display in the report's timezone. The launcher honors APP_HOST/APP_PORT and database initialization
  no longer prints a credential-bearing connection string. Vendored library licenses are included.

## Verification

- 21 pytest checks: mapping, compatibility/widget resolution, calibration, processors and trimmed-mean filtering.
- 40/40 API acceptance checks passed locally. The script covers both tenants and all demo data paths, layout isolation, clone, export/report,
  audit, mapping failures, offline alerts/recovery and Generic Monitoring.
- Browser walkthrough completed: login, catalog, asset/source test, preset mapping, two-point calibration,
  limits/alerts, graph layout save, activation and the live gateway dashboard. Live charts and readings rendered.
- Every frontend module passed Node's module syntax check; Python compilation and `git diff --check` passed.
- API verification uses isolated Mac databases `ucap_verify` and `ucap_verify_customer` on PostgreSQL port 5544,
  with the API on `http://127.0.0.1:8015`. Existing local demos on 8000/8001 were left running.

## Dell copy and synchronization

The Dell is reachable as SandeepLat (`100.126.210.39`) over the existing Tailscale network.
Its original `C:\Users\admin\Documents\IoT Platform` is still on commit `c520ccc` with uncommitted work.
Nine files differ from the copied Mac baseline `b1ede87`:

- `backend/app/drivers/database_api.py`
- `backend/app/routers/devices.py`
- `backend/app/services/dashboard.py`
- `backend/app/services/devices.py`
- `backend/app/services/instances.py`
- `backend/app/services/processing.py`
- `backend/database/schema.sql`
- `frontend/css/styles.css`
- `frontend/js/views/dashboard.js`

These include opposite-sensor gap calculation and reference-sensor drift correction, plus device address handling.
They have not been merged into this single-sensor MVP. The original folder, git state, running API and databases
must be preserved. Its existing API listens on Dell loopback port 8000.

The MVP is installed separately at `C:\Users\admin\Documents\IoT Platform MVP`, using databases
`ucap_mvp_demo` and `ucap_mvp_customer`, with a local-only endpoint `http://127.0.0.1:8015` on the Dell.
Start it from that directory with `python backend/run.py`; local `backend/.env` supplies the configuration.
The checkout's origin is the shared `rajdeep-automation/rajdeep-iot` repository. Git bundle transfer can be used
when the Dell's GitHub login cannot read that repository. No credentials or local database contents belong in git.

## Scope and remaining work

This is the demo scope, not completion of every item in the historical roadmap:

- Real CD22 hardware and multi-sensor timing/calculation remain unverified. Dell's newer multi-sensor work needs
  a separate deliberate merge and hardware tests; the MVP catalog labels those applications coming soon.
- Optional recipe-specific target/limit switching, email/WhatsApp delivery and advanced mapping-transform controls
  are not part of the delivered UI. Product codes currently unlock grouping/reporting, not recipe switching.
- The source model extends devices/connections and existing bindings rather than introducing a separate data_sources
  abstraction. Measurement JSON coexists with legacy columns. Source keys/secrets are not encrypted at rest;
  API secrets are excluded from ordinary device responses. Demo private-source access remains enabled for LAN testing.
- Production authorization hardening, retention/storage scaling and restart-persistent deduplication are follow-up work.
- This delivery preserves the original Dell application; it does not replace its port-8000 service or add an automatic
  startup task. Run the MVP launcher again after a laptop reboot.
