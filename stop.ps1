# ================================================================
# Agent Platform - stop script
# Usage: .\stop.ps1 [-Clean]
#   -Clean  also remove volumes (DB/cache will be reset on next start)
# ================================================================

param(
    [switch]$Clean = $false
)

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

# ---------- 1. Web console ----------
$PidFile = Join-Path $ProjectRoot ".vite.pid"
$killed = $false

if (Test-Path $PidFile) {
    $raw = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($raw) {
        $vpid = 0
        if ([int]::TryParse($raw.Trim(), [ref]$vpid)) {
            $proc = Get-Process -Id $vpid -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $vpid -Force -ErrorAction SilentlyContinue
                Write-Host "  [OK] web console stopped (pid $vpid)" -ForegroundColor Green
                $killed = $true
            }
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

if (-not $killed) {
    $conn = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK] web console stopped (pid $($conn.OwningProcess))" -ForegroundColor Green
        $killed = $true
    }
}

if (-not $killed) {
    Write-Host "  [--] web console not running" -ForegroundColor Yellow
}

# ---------- 2. Docker stack ----------
Write-Host ""
Write-Host "==> Stopping services..." -ForegroundColor Cyan
& docker compose down

if ($Clean) {
    Write-Host "==> Removing volumes (DB/cache will be reset)..." -ForegroundColor Yellow
    Write-Host "    Note: this deletes agents, versions and API keys." -ForegroundColor Yellow
    & docker compose down -v
}

Write-Host ""
Write-Host "DONE." -ForegroundColor Green
