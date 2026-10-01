<#
  Start AiTrading: database (Docker), local LLM (Bionic), backend, frontend, then open the UI in Chrome.

  Safe to run again at any time: anything already running is left alone.
  Double-click start-aitrading.bat, or run:
      powershell -ExecutionPolicy Bypass -File C:\VSCode\AiTrading\start-aitrading.ps1

  The backend runs in the background with no window (run-backend.ps1 keeps it running and restarts it
  if it ever stops; its output is in backend\logs\backend-runner.log). The frontend opens in a
  minimized console window titled "AiTrading Frontend". Run stop-aitrading.bat to stop both.
#>
# Not "Stop": in Windows PowerShell 5.1 that turns any stderr output from native
# tools (docker prints warnings there) into terminating errors. Failures are
# checked explicitly via exit codes instead.
$ErrorActionPreference = "Continue"

$Root        = $PSScriptRoot
$BackendDir  = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$Python      = Join-Path $BackendDir "venv\Scripts\python.exe"
$FrontendUrl = "http://localhost:5173"
$DockerExe   = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$BionicExe   = Join-Path $env:LOCALAPPDATA "Programs\Bionic\Bionic.exe"
$ChromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
)

function Step($text) { Write-Host "`n>> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "   $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   $text" -ForegroundColor Yellow }

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    # Run a native command quietly; return $true when it exits with code 0.
    & $Exe @Arguments *> $null
    return ($LASTEXITCODE -eq 0)
}

function Test-PortListening([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Test-AiTradingBackend([int]$Port) {
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:$Port/health" -TimeoutSec 3
        return $health.app -eq "AiTrading"
    } catch { return $false }
}

function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds, [string]$What) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Seconds 2
    }
    Warn "Timed out after ${TimeoutSeconds}s waiting for $What."
    return $false
}

Write-Host "Starting AiTrading..." -ForegroundColor White

# ---------------------------------------------------------------- 1. Database (Docker)
Step "Database (PostgreSQL in Docker)"
$dockerReady = { Invoke-Native "docker" @("info", "--format", "{{.ServerVersion}}") }
if (-not (& $dockerReady)) {
    if (Test-Path $DockerExe) {
        Warn "Docker Desktop is not running - starting it (this can take a minute)..."
        Start-Process $DockerExe
        if (-not (Wait-Until $dockerReady 180 "Docker Desktop")) { throw "Docker Desktop did not start. Open it manually and run this script again." }
    } else {
        throw "Docker Desktop not found at $DockerExe"
    }
}
Push-Location $Root
try {
    if (-not (Invoke-Native "docker" @("compose", "up", "-d", "postgres"))) { Warn "docker compose reported a problem." }
} finally { Pop-Location }
$dbReady = { (docker exec aitrading-pg pg_isready -U aitrading 2>$null) -match "accepting connections" }
if (Wait-Until $dbReady 60 "PostgreSQL") { Ok "PostgreSQL is up (port 5433)." }

# ---------------------------------------------------------------- 2. Local LLM (Bionic) - optional
Step "Local AI model (Bionic on port 1234)"
$llmReady = { try { Invoke-RestMethod -Uri "http://localhost:1234/v1/models" -TimeoutSec 3 | Out-Null; $true } catch { $false } }
if (& $llmReady) {
    Ok "Bionic is already serving."
} elseif (Test-Path $BionicExe) {
    Start-Process $BionicExe
    if (Wait-Until $llmReady 60 "Bionic's server") {
        Ok "Bionic is serving."
    } else {
        Warn "Bionic opened but its server isn't answering yet. Load the model and start its server;"
        Warn "AiTrading picks it up automatically. Rankings and plans work without it."
    }
} else {
    Warn "Bionic not found - chat and AI features will be offline; everything else works."
}

# ---------------------------------------------------------------- 3. Backend
Step "Backend (FastAPI)"
$BackendPort = $null
foreach ($p in 8000, 8010) { if (Test-AiTradingBackend $p) { $BackendPort = $p; break } }
if ($BackendPort) {
    Ok "Already running on port $BackendPort."
} else {
    if (Test-PortListening 8000) { throw "Port 8000 is in use by another program - close it and run this again." }
    $BackendPort = 8000
    # Hidden background runner: keeps the backend (and so learning) going without a window, and
    # restarts it if it ever stops. stop-aitrading.bat stops it for good.
    $Runner = Join-Path $Root "run-backend.ps1"
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", "`"$Runner`"")
    if (Wait-Until { Test-AiTradingBackend $BackendPort } 120 "the backend") { Ok "Backend is up on port $BackendPort (running in the background)." }
}

# ---------------------------------------------------------------- 4. Frontend
Step "Frontend (Vite)"
if (Test-PortListening 5173) {
    Ok "Already running on port 5173."
} else {
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        Warn "Installing frontend dependencies (first run only)..."
        Push-Location $FrontendDir
        try { npm install --no-fund --no-audit | Out-Null } finally { Pop-Location }
    }
    $env:VITE_API_URL = "http://localhost:$BackendPort"
    $env:VITE_WS_URL  = "ws://localhost:$BackendPort/api/ws/updates"
    $cmd = "title AiTrading Frontend && cd /d `"$FrontendDir`" && npm run dev"
    Start-Process cmd.exe -ArgumentList "/k", $cmd -WindowStyle Minimized
    if (Wait-Until { Test-PortListening 5173 } 60 "the frontend") { Ok "Frontend is up on port 5173." }
}

# ---------------------------------------------------------------- 5. Open the UI in Chrome
Step "Opening AiTrading in Chrome"
$chrome = $ChromePaths | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($chrome) {
    Start-Process $chrome -ArgumentList $FrontendUrl
    Ok "Opened $FrontendUrl"
} else {
    Warn "Chrome not found - opening in your default browser."
    Start-Process $FrontendUrl
}

Write-Host "`nAiTrading is running.  UI: $FrontendUrl   API: http://localhost:$BackendPort/docs" -ForegroundColor White
Write-Host "To stop it: double-click stop-aitrading.bat (Docker and Bionic are left running)." -ForegroundColor Gray
