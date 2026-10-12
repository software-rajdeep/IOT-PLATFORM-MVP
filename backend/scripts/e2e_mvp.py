"""End-to-end MVP acceptance test (docs/MVP_PLAN.md section 6), run against a live API.

    python scripts/e2e_mvp.py [--base http://127.0.0.1:8000] [--skip-offline]

ABC Rubber  : CD22-35 via the Rajdeep gateway (simulated), preset mapping.
XYZ Polymers: same product, their OWN line API (different names, micrometres, product code,
              line speed) mapped in step 4; plus an HTTP-push line to test failures.
No code changes between customers: product, application, mapping and widgets are data.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
RESULTS: list[tuple[str, bool, str]] = []


def call(method: str, path: str, token: str | None = None, body=None, headers=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read().decode()
            return data if raw else (json.loads(data) if data else None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from None


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return bool(ok)


def wait_for(fn, timeout=30, every=1.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(every)
    return last


def login(email):
    return call("POST", "/api/v1/auth/login", body={"email": email, "password": "demo1234"})["token"]


def calibrate_and_limits(tok, inst, target, lower, upper):
    # thickness = -1 * displacement + 10  (reference pieces 10.000 mm @ 0.000 and 8.000 mm @ 2.000)
    cal = call("POST", f"/api/v1/instances/{inst}/calibrate", tok, {
        "reference_1_thickness": 10, "reference_1_displacement": 0,
        "reference_2_thickness": 8, "reference_2_displacement": 2})
    call("PATCH", f"/api/v1/instances/{inst}", tok, {"config": {
        "thickness.target_mm": target, "thickness.lower_limit_mm": lower, "thickness.upper_limit_mm": upper}})
    return cal


def widget_status(tok, inst):
    return {w["widget_type"]: w["status"] for w in call("GET", f"/api/v1/instances/{inst}/widgets", tok)["widgets"]}


def main(skip_offline: bool) -> int:
    run = time.strftime("%H%M%S")
    abc, xyz = login("admin@demo.local"), login("admin@xyz.local")

    # ---------------------------------------------------------------- 1. ABC: catalog -> gateway -> preset
    sols = call("GET", "/api/v1/catalog/solutions", abc)["solutions"]
    sol = next((s for s in sols if s["status"] == "available" and s["model"].get("model_key") == "optex_cd22_35_485m12"
                and s["template"]["template_id"] == "side_by_side_sensors_v1"), None)
    check("catalog lists CD22-35 · Rubber Thickness", sol is not None)
    check("catalog lists Generic Monitoring for CD22-35",
          any(s["template"].get("template_id") == "generic_monitoring_v1" and s["model"].get("model_key") == "optex_cd22_35_485m12"
              for s in sols))
    check("catalog shows coming-soon cards", any(s["status"] == "coming_soon" for s in sols))

    dev = call("POST", "/api/v1/devices", abc, {"model_key": "optex_cd22_35_485m12", "plant_key": "MUMBAI",
               "asset_id": f"Extruder 2 ({run})", "connection": {"interface": "RS485", "address": 1, "simulate": True}})
    test = call("POST", f"/api/v1/devices/{dev['device_id']}/test", abc)
    check("ABC device test connects", test.get("connected"), str(test.get("displacement.value_mm")))
    inst_a = call("POST", "/api/v1/instances", abc, {"name": f"Extruder 2 thickness {run}",
                  "template_id": "side_by_side_sensors_v1", "device_id": dev["device_id"]})["instance_id"]
    m = call("GET", f"/api/v1/instances/{inst_a}/mapping", abc)
    check("ABC gets the CD22 preset mapping automatically", (m.get("current") or {}).get("origin") == "preset")
    calibrate_and_limits(abc, inst_a, 8.5, 8.3, 8.7)
    ws = widget_status(abc, inst_a)
    check("ABC: per-product summary is LOCKED (no product code)", ws.get("group_summary") == "locked")
    check("ABC: capability chart available", ws.get("capability") == "available")
    got = wait_for(lambda: call("GET", f"/api/v1/instances/{inst_a}/dashboard", abc)["current"]["value"], 20)
    check("ABC: live thickness flowing", got is not None, f"{got} mm")
    ready = call("GET", f"/api/v1/instances/{inst_a}/checklist", abc)
    check("ABC: activation checklist ready", ready["ready"], json.dumps([i["key"] for i in ready["items"] if not i["ok"]]))
    call("POST", f"/api/v1/instances/{inst_a}/activate", abc)

    # ---------------------------------------------------------------- 2. XYZ: their own API, mapped
    api_url = f"{BASE}/api/v1/demo-customer/api/v2/latest?line=EXT-07"
    dev_x = call("POST", "/api/v1/devices", xyz, {"model_key": "optex_cd22_35_485m12", "plant_key": "PUNE",
                 "asset_id": f"EXT-07 ({run})", "connection": {"interface": "database_api", "simulate": False,
                 "poll_interval_ms": 1000, "database": {"mode": "customer", "api_url": api_url,
                 "api_key": "demo-customer-key", "api_secret": "demo-customer-secret"}}})
    check("XYZ device response hides the API secret",
          "api_secret" not in json.dumps(dev_x["connection"]["parameters"]) and "api_secret" in dev_x["connection"]["secrets_set"])
    t = call("POST", f"/api/v1/devices/{dev_x['device_id']}/test", xyz)
    check("XYZ source test returns their payload", t.get("connected") and "laser" in (t.get("payload") or {}))
    inst_x = call("POST", "/api/v1/instances", xyz, {"name": f"EXT-07 thickness {run}",
                  "template_id": "side_by_side_sensors_v1", "device_id": dev_x["device_id"]})["instance_id"]
    check("XYZ starts with NO mapping (unknown shape)", call("GET", f"/api/v1/instances/{inst_x}/mapping", xyz)["current"] is None)
    try:
        call("POST", f"/api/v1/instances/{inst_x}/activate", xyz)
        check("XYZ cannot activate before mapping", False)
    except RuntimeError as e:
        check("XYZ cannot activate before mapping", "Not ready" in str(e))

    samples = call("POST", f"/api/v1/instances/{inst_x}/mapping/samples", xyz)
    sug = samples["suggestions"]
    check("suggestion: laser.value_um -> displacement", sug.get("displacement.value_mm", {}).get("path") == "laser.value_um")
    check("suggestion: prod -> product code", sug.get("context.product_code", {}).get("path") == "prod")
    check("suggestion: speed_mpm -> line speed", sug.get("line.speed_m_min", {}).get("path") == "speed_mpm")
    check("unit hint: micrometres detected",
          next(p for p in samples["paths"] if p["path"] == "laser.value_um")["unit_hint"] == "um")
    definition = {
        "fields": [
            {"target": "displacement.value_mm", "path": "laser.value_um",
             "transforms": [{"type": "cast", "to": "decimal"}, {"type": "unit", "from": "um", "to": "mm"}]},
            {"target": "context.product_code", "path": "prod"},
            {"target": "line.speed_m_min", "path": "speed_mpm", "transforms": [{"type": "cast", "to": "decimal"}]},
        ],
        "timestamp": {"path": "ts", "format": "auto", "tz": "Asia/Kolkata"},
    }
    calibrate_and_limits(xyz, inst_x, 8.5, 8.3, 8.7)
    pv = call("POST", f"/api/v1/instances/{inst_x}/mapping/preview", xyz, {"definition": definition,
              "payload": samples["samples"][0]["payload"]})
    check("XYZ preview maps without errors", not pv["errors"], json.dumps(pv["values"]))
    call("POST", f"/api/v1/instances/{inst_x}/mapping", xyz, {"definition": definition})
    ws_x = widget_status(xyz, inst_x)
    check("XYZ: per-product summary UNLOCKED by mapping product code", ws_x.get("group_summary") == "available")
    check("XYZ: value-vs-speed UNLOCKED by mapping line speed", ws_x.get("speed_scatter") == "available")
    got = wait_for(lambda: call("GET", f"/api/v1/instances/{inst_x}/dashboard", xyz)["current"]["value"], 20)
    check("XYZ: live thickness flowing from their API", got is not None, f"{got} mm")
    call("POST", f"/api/v1/instances/{inst_x}/activate", xyz)
    groups = wait_for(lambda: call("GET", f"/api/v1/instances/{inst_x}/dashboard", xyz)["groups"], 10)
    check("XYZ: per-product groups computed", bool(groups), ",".join(g["group"] for g in groups or []))

    # ---------------------------------------------------------------- 3. same displacement -> same result
    a = call("POST", f"/api/v1/instances/{inst_a}/mapping/preview", abc,
             {"definition": call("GET", f"/api/v1/instances/{inst_a}/mapping", abc)["current"]["definition"],
              "payload": {"measurement_raw": 152}})
    b = call("POST", f"/api/v1/instances/{inst_x}/mapping/preview", xyz,
             {"definition": definition, "payload": {"ts": "2026-10-12 10:00:00", "laser": {"value_um": 1520},
                                                    "prod": "RUB-EXTR-1", "speed_mpm": 12}})
    same = {k: a["outputs"][k] for k in a["outputs"]} == {k: b["outputs"][k] for k in a["outputs"]}
    check("identical outputs for the same displacement (gateway vs customer API)", same,
          f"{a['outputs']} vs {b['outputs']}")

    # ---------------------------------------------------------------- 4. HTTP push + malformed payload
    dev_p = call("POST", "/api/v1/devices", xyz, {"model_key": "optex_cd22_35_485m12", "plant_key": "PUNE",
                 "asset_id": f"EXT-08 push ({run})", "connection": {"interface": "http_push"}})
    inst_p = call("POST", "/api/v1/instances", xyz, {"name": f"EXT-08 push {run}",
                  "template_id": "side_by_side_sensors_v1", "device_id": dev_p["device_id"]})["instance_id"]
    info = call("GET", f"/api/v1/devices/{dev_p['device_id']}/ingest-info", xyz)
    push_def = {"fields": [{"target": "displacement.value_mm", "path": "thk_disp_mm",
                            "transforms": [{"type": "cast", "to": "decimal"}]}]}
    call("POST", f"/api/v1/instances/{inst_p}/mapping", xyz, {"definition": push_def})
    ok = call("POST", f"/api/v1/ingest/{dev_p['device_id']}", None, {"thk_disp_mm": "1.49"},
              headers={"X-Ingest-Key": info["ingest_key"]})
    check("HTTP push: good message mapped", ok["mapped"] == 1)
    bad = call("POST", f"/api/v1/ingest/{dev_p['device_id']}", None, {"something_else": 1},
               headers={"X-Ingest-Key": info["ingest_key"]})
    fails = call("GET", f"/api/v1/instances/{inst_p}/mapping/failures", xyz)["failures"]
    check("HTTP push: malformed message rejected and logged", bad["mapped"] == 0 and fails and fails[0]["errors"][0]["code"] == "MISSING_FIELD")
    try:
        call("POST", f"/api/v1/ingest/{dev_p['device_id']}", None, {"thk_disp_mm": 1}, headers={"X-Ingest-Key": "wrong"})
        check("HTTP push: wrong key refused", False)
    except RuntimeError as e:
        check("HTTP push: wrong key refused", "401" in str(e))

    # ---------------------------------------------------------------- 5. layouts are per customer
    layout = call("GET", f"/api/v1/instances/{inst_a}/widgets", abc)["layout"]["widgets"]
    trimmed = [w for w in layout if w["type"] not in ("history_table", "capability")]
    trimmed[0]["w"] = 6
    call("PUT", f"/api/v1/instances/{inst_a}/layout", abc, {"widgets": trimmed})
    again = call("GET", f"/api/v1/instances/{inst_a}/dashboard", abc)["layout"]["widgets"]
    check("ABC layout saved (2 widgets removed, first resized)", len(again) == len(layout) - 2 and again[0]["w"] == 6)
    x_layout = call("GET", f"/api/v1/instances/{inst_x}/dashboard", xyz)["layout"]["widgets"]
    check("XYZ layout unaffected", any(w["type"] == "history_table" for w in x_layout))
    try:
        call("PUT", f"/api/v1/instances/{inst_a}/layout", abc, {"widgets": [{"type": "group_summary", "x": 0, "y": 0, "w": 6, "h": 3}]})
        check("ABC cannot place a locked widget", False)
    except RuntimeError as e:
        check("ABC cannot place a locked widget", "not available" in str(e))

    # ---------------------------------------------------------------- 6. clone a line
    clone = call("POST", f"/api/v1/instances/{inst_a}/clone", abc, {"name": f"Extruder 3 thickness {run}", "asset_id": f"Extruder 3 ({run})"})
    cm = call("GET", f"/api/v1/instances/{clone['instance_id']}/mapping", abc)
    check("clone: draft, mapping copied, needs recalibration",
          clone["status"] == "draft" and (cm.get("current") or {}).get("origin") == "clone" and not clone["steps"]["calibrated"])
    check("clone: limits copied", clone["config"].get("thickness.lower_limit_mm") == "8.3")

    # ---------------------------------------------------------------- 7. export + report + audit
    # Polling is asynchronous: wait for two source rows before checking the export.
    def csv_with_rows():
        text = call("GET", f"/api/v1/instances/{inst_x}/export.csv", xyz, raw=True)
        return text if len(text.strip().splitlines()) > 2 else None
    csv_text = wait_for(csv_with_rows, 15) or call("GET", f"/api/v1/instances/{inst_x}/export.csv", xyz, raw=True)
    lines = csv_text.strip().splitlines()
    check("CSV export has header + rows", lines[0].startswith("timestamp,device_id") and len(lines) > 2, f"{len(lines) - 1} rows")
    rep = call("GET", f"/api/v1/instances/{inst_x}/report", xyz)
    check("shift report returns 3 shifts", len(rep["shifts"]) == 3 and sum(s["samples"] for s in rep["shifts"]) > 0)
    hist = call("GET", f"/api/v1/instances/{inst_x}/history", xyz)["history"]
    check("audit trail records calibration + mapping + activation",
          {"calibration.save", "mapping.save", "instance.activate"} <= {h["action"] for h in hist})

    # ---------------------------------------------------------------- 8. tenant isolation
    try:
        call("GET", f"/api/v1/instances/{inst_a}/dashboard", xyz)
        check("XYZ cannot read ABC's instance", False)
    except RuntimeError as e:
        check("XYZ cannot read ABC's instance", "404" in str(e))

    # ---------------------------------------------------------------- 9. offline alert
    if not skip_offline:
        call("PATCH", f"/api/v1/devices/{dev['device_id']}/connection", abc, {"interface": "http_push"})  # stops polling
        alert = wait_for(lambda: [a for a in call("GET", f"/api/v1/instances/{inst_a}/alerts", abc)["alerts"]
                                  if a["rule_type"] == "offline" and not a["resolved_at"]], 35, 2)
        check("offline alert fires when the source stops", bool(alert))
        call("PATCH", f"/api/v1/devices/{dev['device_id']}/connection", abc, {"interface": "RS485"})
        resolved = wait_for(lambda: all(a["resolved_at"] for a in call("GET", f"/api/v1/instances/{inst_a}/alerts", abc)["alerts"]
                                        if a["rule_type"] == "offline"), 20, 2)
        check("offline alert resolves when data returns", bool(resolved))

    # Generic Monitoring must use the same source/runtime without thickness-specific UI.
    generic = call("POST", "/api/v1/instances", abc, {"name": f"Generic monitoring {run}",
                   "template_id": "generic_monitoring_v1", "device_id": dev["device_id"]})["instance_id"]
    call("PATCH", f"/api/v1/instances/{generic}", abc, {"config": {
        "signal.target": 1.5, "signal.lower_limit": 1.0, "signal.upper_limit": 2.0}})
    generic_check = call("GET", f"/api/v1/instances/{generic}/checklist", abc)
    check("Generic Monitoring needs no calibration", not any(i["key"] == "calibration" for i in generic_check["items"]))
    call("POST", f"/api/v1/instances/{generic}/activate", abc)
    generic_value = wait_for(lambda: call("GET", f"/api/v1/instances/{generic}/dashboard", abc)["current"]["value"], 15)
    check("Generic Monitoring produces live values", generic_value is not None)
    check("Generic Monitoring excludes calibration widgets", widget_status(abc, generic).get("calibration_status") == "not_applicable")

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    print(f"instances: ABC {inst_a}, XYZ {inst_x}, push {inst_p}, clone {clone['instance_id']}")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--skip-offline", action="store_true")
    args = ap.parse_args()
    BASE = args.base.rstrip("/")
    sys.exit(main(args.skip_offline))
