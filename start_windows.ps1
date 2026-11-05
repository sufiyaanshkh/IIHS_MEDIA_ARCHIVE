<#
.SYNOPSIS
    IIHS Media Archive - Start Windows Services (Local ML Bridge + Docker Stack)
.DESCRIPTION
    Starts the host Local ML Service (local_ml_service.py) on port 8765 and launches
    PostgreSQL/pgvector, Alembic migrations, FastAPI backend, ingestion worker, and React UI.
#>
$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Starting IIHS Media Archive Stack on Windows" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (-not (Test-Path ".\.env")) {
    Write-Host ".env not found. Running .\setup_windows.ps1 first..." -ForegroundColor Yellow
    & .\setup_windows.ps1
}

$pythonExe = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

# 1. Start Local ML Service on port 8765 if not already listening
$mlHealthy = $false
try {
    $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8765/healthz" -TimeoutSec 2 -ErrorAction Stop
    if ($resp.status -eq "ready") {
        $mlHealthy = $true
        Write-Host "[1/3] Local ML Service is already running on 127.0.0.1:8765 ($($resp.device))." -ForegroundColor Green
    }
} catch {}

if (-not $mlHealthy) {
    Write-Host "[1/3] Launching Local ML Service on 127.0.0.1:8765..." -ForegroundColor Yellow
    $proc = Start-Process -FilePath $pythonExe -ArgumentList "local_ml_service.py" -WorkingDirectory (Get-Location).Path -PassThru -WindowStyle Hidden
    Set-Content -Path ".\.local_ml.pid" -Value $proc.Id -Encoding ASCII
    Start-Sleep -Seconds 3
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:8765/healthz" -TimeoutSec 5 -ErrorAction Stop
        Write-Host "      Local ML Service ready (PID=$($proc.Id), Backend=$($resp.model.backend), Device=$($resp.device))" -ForegroundColor Green
    } catch {
        Write-Host "      Warning: Local ML Service did not respond on :8765 yet." -ForegroundColor Yellow
    }
}

# 2. Start Docker Compose stack
Write-Host "[2/3] Starting Docker Compose services (postgres, migrate, api, worker, web)..." -ForegroundColor Yellow
docker-compose up --build -d

# 3. Verify API readiness
Write-Host "[3/3] Waiting for Backend API readiness on http://127.0.0.1:8010/readyz ..." -ForegroundColor Yellow
for ($i = 0; $i -lt 15; $i++) {
    try {
        $ready = Invoke-RestMethod -Uri "http://127.0.0.1:8010/readyz" -TimeoutSec 3 -ErrorAction Stop
        if ($ready.status -eq "ready") {
            Write-Host "      Backend API is READY!" -ForegroundColor Green
            break
        }
    } catch {
        Start-Sleep -Seconds 2
    }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Web UI:      http://127.0.0.1:5174" -ForegroundColor Green
Write-Host " Backend API: http://127.0.0.1:8010" -ForegroundColor Green
Write-Host " Local ML:    http://127.0.0.1:8765/healthz" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

