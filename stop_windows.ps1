<#
.SYNOPSIS
    IIHS Media Archive - Stop Windows Services
.DESCRIPTION
    Gracefully stops Docker Compose services and the background Local ML Service.
#>
$ErrorActionPreference = "Continue"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Stopping IIHS Media Archive Stack" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

Write-Host "[1/2] Stopping Docker Compose containers..." -ForegroundColor Yellow
docker-compose down

if (Test-Path ".\.local_ml.pid") {
    $mlPid = Get-Content ".\.local_ml.pid" -ErrorAction SilentlyContinue
    if ($mlPid) {
        Write-Host "[2/2] Stopping Local ML Service (PID=$mlPid)..." -ForegroundColor Yellow
        Stop-Process -Id ([int]$mlPid) -Force -ErrorAction SilentlyContinue
    }
    Remove-Item ".\.local_ml.pid" -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[2/2] No .local_ml.pid file found." -ForegroundColor Green
}

Write-Host "All services stopped." -ForegroundColor Green

