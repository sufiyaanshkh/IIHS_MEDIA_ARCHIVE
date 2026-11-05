<#
.SYNOPSIS
    IIHS Media Archive - Windows Setup & Dependency Installer
.DESCRIPTION
    Creates the Python virtual environment, installs backend & ML runtime dependencies,
    initializes .env with secure random tokens if missing, and prepares storage directories.
#>
$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " IIHS Media Archive - Windows Workstation Setup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Create virtual environment if not present
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "[1/4] Creating Python virtual environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv --system-site-packages
} else {
    Write-Host "[1/4] Using existing Python virtual environment (.venv)." -ForegroundColor Green
}

$venvPython = ".\.venv\Scripts\python.exe"

# 2. Install backend & ML service dependencies
Write-Host "[2/4] Installing Python dependencies..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r backend\requirements-dev.txt

# 3. Generate .env if missing
if (-not (Test-Path ".\.env")) {
    Write-Host "[3/4] Generating .env with secure cryptographic tokens..." -ForegroundColor Yellow
    $adminToken = & $venvPython -c "import secrets; print(secrets.token_urlsafe(32))"
    $mlToken = & $venvPython -c "import secrets; print(secrets.token_urlsafe(32))"
    $content = Get-Content ".\.env.example" -Raw
    $content = $content -replace "INITIAL_ADMIN_TOKEN=replace-with-a-32-plus-character-random-secret", "INITIAL_ADMIN_TOKEN=$adminToken"
    $content = $content -replace "LOCAL_ML_TOKEN=replace-with-a-second-32-plus-character-random-secret", "LOCAL_ML_TOKEN=$mlToken"
    if ($content -notmatch "COPY_SOURCE_ORIGINALS=") {
        $content += "`r`nCOPY_SOURCE_ORIGINALS=false`r`n"
    }
    Set-Content -Path ".\.env" -Value $content -Encoding UTF8
    Write-Host "      Created .env (INITIAL_ADMIN_TOKEN=$adminToken)" -ForegroundColor Green
} else {
    Write-Host "[3/4] Existing .env preserved." -ForegroundColor Green
}

# 4. Ensure runtime directories exist
Write-Host "[4/4] Ensuring runtime storage directories exist..." -ForegroundColor Yellow
foreach ($dir in @(".\data\storage", ".\data\approved_sources", ".\data\backups")) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Setup complete! Run .\start_windows.ps1 to launch services." -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

