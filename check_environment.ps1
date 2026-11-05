<#
.SYNOPSIS
    IIHS Media Archive - Windows Workstation Environment Check
.DESCRIPTION
    Checks Python, Docker, FFmpeg, FFprobe, NVIDIA GPU/CUDA availability, disk space,
    and .env configuration required for 3-4 TB multimodal HDD archive ingestion.
#>
$ErrorActionPreference = "Continue"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " IIHS Media Archive - Windows Environment Verification" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$allReady = $true

# 1. Python Check
$pythonExe = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
try {
    $pyVer = & $pythonExe --version 2>&1
    Write-Host "[OK] Python: $pyVer ($pythonExe)" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Python 3.11+ not found in PATH or .venv" -ForegroundColor Red
    $allReady = $false
}

# 2. PyTorch & CUDA Check
try {
    $cudaReport = & $pythonExe -c "import torch; print(f'torch={torch.__version__} cuda={torch.cuda.is_available()} device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"CPU\"}')" 2>&1
    Write-Host "[OK] PyTorch Runtime: $cudaReport" -ForegroundColor Green
} catch {
    Write-Host "[WARN] PyTorch not detected in current Python environment" -ForegroundColor Yellow
}

# 3. NVIDIA GPU Check
try {
    $gpuInfo = nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] NVIDIA GPU: $gpuInfo" -ForegroundColor Green
    } else {
        Write-Host "[INFO] NVIDIA GPU not detected via nvidia-smi (CPU fallback will be used)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[INFO] nvidia-smi not available (CPU fallback will be used)" -ForegroundColor Yellow
}

# 4. FFmpeg / FFprobe Check
foreach ($tool in @("ffmpeg", "ffprobe")) {
    $cmd = Get-Command $tool -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host "[OK] ${tool}: $($cmd.Source)" -ForegroundColor Green
    } else {
        Write-Host "[WARN] ${tool} not found on host PATH (Docker containers include ffmpeg/ffprobe)" -ForegroundColor Yellow
    }
}

# 5. Docker & Docker Compose Check
try {
    $dockerVer = docker --version 2>&1
    $composeVer = docker-compose --version 2>&1
    Write-Host "[OK] Docker: $dockerVer | $composeVer" -ForegroundColor Green
} catch {
    Write-Host "[FAIL] Docker Desktop / docker-compose is not installed or not in PATH" -ForegroundColor Red
    $allReady = $false
}

# 6. Storage Free Space Check
$drive = (Get-Location).Drive.Name
$disk = Get-PSDrive $drive
$freeGB = [math]::Round($disk.Free / 1GB, 2)
Write-Host "[OK] Workspace Drive (${drive}:) Free Space: ${freeGB} GB" -ForegroundColor Green
if ($freeGB -lt 50) {
    Write-Host "[WARN] Less than 50 GB free on workspace drive; ensure COPY_SOURCE_ORIGINALS=false for 3-4 TB HDD ingestion." -ForegroundColor Yellow
}

# 7. .env Configuration Check
if (Test-Path ".\.env") {
    Write-Host "[OK] .env configuration file found" -ForegroundColor Green
} else {
    Write-Host "[WARN] .env file missing; run .\setup_windows.ps1 to generate one" -ForegroundColor Yellow
}

Write-Host "============================================================" -ForegroundColor Cyan
if ($allReady) {
    Write-Host " Environment check passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host " Environment check found missing prerequisites." -ForegroundColor Red
    exit 1
}

