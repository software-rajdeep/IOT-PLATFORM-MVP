# Universal Industrial Platform — start everything (UCAP API + embedded project).
# - Initialises the UCAP database (schema + seed)
# - Starts the UCAP API on http://127.0.0.1:8000
# - Starts the thickness-monitoring Flask backend on http://127.0.0.1:5002
#   (serves the embedded React app; superadmin / superadmin123)
# - Starts a demo sensor feed so the embedded dashboards show live data
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$tm   = Join-Path $root "thickness-monitoring"

if (-not (Test-Path (Join-Path $tm "dist\index.html"))) {
    Write-Host "Frontend for thickness-monitoring is not built. Run: npm install && npm run build inside thickness-monitoring/"
    exit 1
}

Push-Location (Join-Path $root "backend")
try { python scripts/init_db.py } finally { Pop-Location }

if (-not (Get-NetTCPConnection -LocalPort 5002 -State Listen -ErrorAction SilentlyContinue)) {
    $env:SUPERADMIN_PASSWORD = "superadmin123"
    $env:SERVER_PORT         = "5002"
    $env:CLOUD_MODE          = "false"
    Start-Process -FilePath python -ArgumentList "backend\merged_server.py" -WorkingDirectory $tm -WindowStyle Hidden
    Start-Process -FilePath python -ArgumentList "backend\demo_feed.py"    -WorkingDirectory $tm -WindowStyle Hidden
    Write-Host "Started thickness-monitoring backend (:5002) + demo feed."
} else {
    Write-Host "Thickness-monitoring backend already running on :5002."
}

if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath python -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Write-Host "Started UCAP API on http://127.0.0.1:8000"
} else {
    Write-Host "UCAP API already running on :8000."
}

Start-Process "http://127.0.0.1:8000"
Write-Host "UCAP: http://127.0.0.1:8000  |  thickness-monitoring app: http://127.0.0.1:5002"