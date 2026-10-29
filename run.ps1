# Universal Industrial Platform — one-command dev run.
# Creates/updates the database, then starts the API + SPA on http://127.0.0.1:8000
param([switch]$Reset)

$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "backend"

$port = 8000
$envFile = Join-Path $backend ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*APP_PORT\s*=\s*(\d+)') { $port = [int]$Matches[1]; break }
    }
}

Push-Location $backend
try {
    if ($Reset) {
        Write-Host "Resetting database ucap_platform ..."
        $env:PGPASSWORD = if ($env:PGPASSWORD) { $env:PGPASSWORD } else { "linux" }
        psql -U postgres -h localhost -c "DROP DATABASE IF EXISTS ucap_platform;"
    }
    Write-Host "Applying schema + seed ..."
    python scripts/init_db.py
    Write-Host "Starting API on http://127.0.0.1:$port ..."
    python -m uvicorn app.main:app --host 127.0.0.1 --port $port
}
finally {
    Pop-Location
}
