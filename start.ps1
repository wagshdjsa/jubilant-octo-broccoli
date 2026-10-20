# ================================================================
# Agent Platform - one-click start script (PowerShell 5.1 compatible)
# Usage: .\start.ps1
#   -Rebuild       force rebuild of docker images
#   -NoUi          skip the web console (backend only)
#   -Logs          follow container logs after startup
# ================================================================

param(
    [switch]$Rebuild = $false,
    [switch]$NoUi = $false,
    [switch]$Logs = $false
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "==> Working dir: $ProjectRoot" -ForegroundColor Cyan

# ---------- 0. Normalize environment ----------
# Windows 上常见 http_proxy 与 HTTP_PROXY 同时存在（仅大小写不同）。
# PowerShell 5.1 的 Start-Process 在复制环境变量时会因字典键冲突抛
# "已添加了具有相同键的项"，导致启动子进程直接失败。这里先去掉重复项。
try {
    $envAll = [System.Environment]::GetEnvironmentVariables()
    $seenKeys = @{}
    $dupeKeys = @()
    foreach ($k in $envAll.Keys) {
        $lk = $k.ToString().ToLower()
        if ($seenKeys.ContainsKey($lk)) { $dupeKeys += $k.ToString() } else { $seenKeys[$lk] = $k }
    }
    foreach ($d in $dupeKeys) {
        [System.Environment]::SetEnvironmentVariable($d, $null)
    }
} catch { }

# ---------- 1. Prerequisites ----------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Docker not installed. Please install Docker Desktop." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "[ERROR] .env not found. Copy from .env.example and fill OPENAI_API_KEY." -ForegroundColor Red
    exit 1
}

# read MANAGER_ADMIN_TOKEN from .env
$AdminToken = "ChangeMe!2026"
Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*MANAGER_ADMIN_TOKEN\s*=\s*(.+?)\s*$') { $AdminToken = $Matches[1] }
}

# ---------- 2. docker compose up ----------
Write-Host ""
Write-Host "==> Starting docker compose..." -ForegroundColor Cyan

$composeArgs = @("compose", "up", "-d")
if ($Rebuild) { $composeArgs += "--build" }

& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] docker compose failed." -ForegroundColor Red
    exit 1
}

# ---------- 3. Wait for health ----------
Write-Host ""
Write-Host "==> Waiting for services..." -ForegroundColor Cyan

$services = @(
    @{ Name = "api-gateway";   Port = 8000 },
    @{ Name = "agent-manager"; Port = 8001 },
    @{ Name = "agent-runtime"; Port = 8002 },
    @{ Name = "knowledge-svc"; Port = 8003 },
    @{ Name = "tool-svc";      Port = 8004 },
    @{ Name = "model-svc";     Port = 8005 }
)

$allOk = $true
foreach ($svc in $services) {
    $url = "http://localhost:" + $svc.Port + "/health"
    $ok = $false
    for ($i = 1; $i -le 20; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if ($ok) {
        Write-Host ("  [OK]   " + $svc.Name + " :" + $svc.Port) -ForegroundColor Green
    } else {
        Write-Host ("  [FAIL] " + $svc.Name + " :" + $svc.Port) -ForegroundColor Red
        $allOk = $false
    }
}

if (-not $allOk) {
    Write-Host ""
    Write-Host "[WARN] Some services are not healthy. Check: docker compose logs" -ForegroundColor Yellow
}

# ---------- 4. Web console (Vite dev server) ----------
$PidFile = Join-Path $ProjectRoot ".vite.pid"
if (-not $NoUi) {
    Write-Host ""
    Write-Host "==> Web console..." -ForegroundColor Cyan

    $viteUp = $false
    try {
        $probe = Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 3 -UseBasicParsing
        if ($probe.StatusCode -eq 200) { $viteUp = $true }
    } catch { }

    if ($viteUp) {
        Write-Host "  [OK]   already running :5173" -ForegroundColor Green
        # 若不是本脚本启动的，记下 PID，方便 stop.ps1 关闭
        if (-not (Test-Path $PidFile)) {
            try {
                $c = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction Stop | Select-Object -First 1
                if ($c) { ($c.OwningProcess | Out-File -FilePath $PidFile -Encoding ascii -Force) }
            } catch { }
        }
    } else {
        $consoleDir = Join-Path $ProjectRoot "web\console"
        $viteEntry = Join-Path $consoleDir "node_modules\vite\bin\vite.js"

        if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
            Write-Host "  [SKIP] node not found in PATH" -ForegroundColor Yellow
            Write-Host "         Install Node.js 18+, or open a shell where node is available." -ForegroundColor Yellow
        } elseif (-not (Test-Path $viteEntry)) {
            Write-Host "  [SKIP] frontend deps missing" -ForegroundColor Yellow
            Write-Host "         Run once: cd web\console ; npm install" -ForegroundColor Yellow
        } else {
            # 注意：这里不能加 -RedirectStandardOutput。
            # 一旦重定向，PowerShell 会用 UseShellExecute=false 启动，
            # 子进程绑定到本脚本的控制台；脚本退出时控制台关闭，Vite 被一起杀掉。
            # 用 Minimized 独立窗口，进程才能真正存活，日志也能在窗口里看到。
            $proc = Start-Process -FilePath "node" `
                -ArgumentList "node_modules/vite/bin/vite.js" `
                -WorkingDirectory $consoleDir `
                -PassThru -WindowStyle Minimized
            ($proc.Id | Out-File -FilePath $PidFile -Encoding ascii -Force)

            $ready = $false
            for ($i = 1; $i -le 25; $i++) {
                Start-Sleep -Seconds 1
                try {
                    $probe = Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 2 -UseBasicParsing
                    if ($probe.StatusCode -eq 200) { $ready = $true; break }
                } catch { }
            }

            if ($ready) {
                # 复检：某些环境（受限 Job Object / 父进程退出）会把子进程一起清理，
                # 首次探测成功不代表能长期存活，这里再确认一次，避免误报。
                Start-Sleep -Seconds 3
                $stillUp = $false
                try {
                    $re = Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 3 -UseBasicParsing
                    if ($re.StatusCode -eq 200) { $stillUp = $true }
                } catch { }

                if ($stillUp) {
                    Write-Host ("  [OK]   started :5173 (pid " + $proc.Id + ")") -ForegroundColor Green
                    Write-Host "         check the minimized 'node' window for Vite logs" -ForegroundColor Gray
                } else {
                    Write-Host "  [WARN] web console exited right after start." -ForegroundColor Yellow
                    Write-Host "         Start it manually in another window:" -ForegroundColor Yellow
                    Write-Host "           cd web\console ; npm run dev" -ForegroundColor Gray
                }
            } else {
                Write-Host "  [WARN] :5173 not reachable, check the minimized window" -ForegroundColor Yellow
                Write-Host "         Or start manually: cd web\console ; npm run dev" -ForegroundColor Gray
            }
        }
    }
}

# ---------- 5. Prepare demo agent + api key ----------
Write-Host ""
Write-Host "==> Preparing demo agent and API key..." -ForegroundColor Cyan

$Headers = @{ "Authorization" = "Bearer $AdminToken"; "Content-Type" = "application/json" }
$AgentId = $null
$ApiKey = $null

try {
    # reuse existing agent named demo-agent
    # NOTE: GET /api/v1/agents returns a bare JSON array, not {items:[...]}
    $list = Invoke-RestMethod -Uri "http://localhost:8001/api/v1/agents" -Headers $Headers -TimeoutSec 15
    if ($list -is [array]) {
        $items = $list
    } elseif ($list.PSObject.Properties.Name -contains "items") {
        $items = @($list.items)
    } else {
        $items = @($list)
    }
    foreach ($a in $items) {
        if ($a -and $a.name -eq "demo-agent") { $AgentId = $a.id; break }
    }

    if (-not $AgentId) {
        $body = @{ name = "demo-agent"; description = "demo"; config = @{} } | ConvertTo-Json -Depth 5
        $agent = Invoke-RestMethod -Uri "http://localhost:8001/api/v1/agents" -Method POST -Headers $Headers -Body $body -TimeoutSec 15
        $AgentId = $agent.id
        Write-Host ("  [OK]   created agent: " + $AgentId) -ForegroundColor Green
    } else {
        Write-Host ("  [OK]   reuse agent:   " + $AgentId) -ForegroundColor Green
    }

    # publish a version
    $pub = Invoke-RestMethod -Uri ("http://localhost:8001/api/v1/agents/" + $AgentId + "/publish") -Method POST -Headers $Headers -Body "{}" -TimeoutSec 20
    Write-Host ("  [OK]   published v" + $pub.version) -ForegroundColor Green

    # issue api key
    $kb = @{ agent_id = $AgentId; name = "demo-key" } | ConvertTo-Json -Depth 5
    $key = Invoke-RestMethod -Uri "http://localhost:8001/api/v1/apikeys" -Method POST -Headers $Headers -Body $kb -TimeoutSec 15
    $ApiKey = $key.key
    Write-Host ("  [OK]   api key:      " + $ApiKey) -ForegroundColor Green
} catch {
    Write-Host ("  [WARN] setup failed: " + $_.Exception.Message) -ForegroundColor Yellow
    Write-Host "         Open http://localhost:8001/docs to create manually." -ForegroundColor Yellow
}

# ---------- 6. Smoke test: model gateway directly ----------
Write-Host ""
Write-Host "==> Smoke test 1/2: model-svc -> zhipu..." -ForegroundColor Cyan
try {
    $body = @{
        model    = "chat-default"
        messages = @(@{ role = "user"; content = "Reply with exactly: OK" })
    } | ConvertTo-Json -Depth 5

    $r = Invoke-RestMethod -Uri "http://localhost:8005/v1/chat/completions" -Method POST -ContentType "application/json" -Body $body -TimeoutSec 40
    Write-Host ("  [OK]   zhipu reply: " + $r.choices[0].message.content) -ForegroundColor Green
} catch {
    Write-Host ("  [FAIL] " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "         Check OPENAI_API_KEY / OPENAI_API_BASE in .env" -ForegroundColor Yellow
}

# ---------- 7. Smoke test: full chain through gateway ----------
if ($ApiKey) {
    Write-Host ""
    Write-Host "==> Smoke test 2/2: gateway -> runtime -> model -> zhipu..." -ForegroundColor Cyan
    try {
        $auth = @{ "Authorization" = "Bearer $ApiKey" }
        $body = @{
            model    = "agent:$AgentId"
            messages = @(@{ role = "user"; content = "Reply in one short sentence: who are you?" })
        } | ConvertTo-Json -Depth 5

        $r = Invoke-RestMethod -Uri "http://localhost:8000/v1/chat/completions" -Method POST -ContentType "application/json" -Headers $auth -Body $body -TimeoutSec 60
        Write-Host ("  [OK]   agent reply: " + $r.choices[0].message.content) -ForegroundColor Green
    } catch {
        Write-Host ("  [FAIL] " + $_.Exception.Message) -ForegroundColor Red
        Write-Host "         Check: docker compose logs agent_runtime" -ForegroundColor Yellow
    }
}

# ---------- 8. Summary ----------
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Stack is UP" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Manager API   : http://localhost:8001/docs" -ForegroundColor White
Write-Host " Model gateway : http://localhost:8005/docs" -ForegroundColor White
Write-Host " Gateway       : http://localhost:8000/docs" -ForegroundColor White
if (-not $NoUi) { Write-Host " Web console   : http://localhost:5173" -ForegroundColor White }
if ($ApiKey) {
    Write-Host ""
    Write-Host " Demo agent id : $AgentId" -ForegroundColor White
    Write-Host " Demo api key  : $ApiKey" -ForegroundColor White
}
Write-Host ""
Write-Host " Logs : docker compose logs -f" -ForegroundColor Gray
Write-Host " Stop : .\stop.ps1" -ForegroundColor Gray
Write-Host ""

if ($Logs) {
    Write-Host "==> Following logs (Ctrl+C to exit)..." -ForegroundColor Cyan
    & docker compose logs -f
}
