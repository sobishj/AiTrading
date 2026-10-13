<#
  Keep the AiTrading backend running in the background (no window): it learns, researches and watches
  your holdings even when the browser isn't open.

  - Started hidden by start-aitrading.ps1, and at Windows log-in when "Start with Windows" is on
    (Settings -> General -> Background learning).
  - Restarts the backend if it ever exits, after a short pause, and records why in
    backend\logs\backend-runner.log (the backend's own output, including any crash).
  - stop-aitrading.ps1 leaves a stop marker (backend\.stop-background) before stopping the backend,
    so a deliberate stop stays stopped until AiTrading is started again.
  - Only one runner at a time; if a backend is already serving (e.g. one you started by hand), it
    just watches and takes over if that one stops.
#>
$ErrorActionPreference = "Continue"

$Root       = $PSScriptRoot
$BackendDir = Join-Path $Root "backend"
$Python     = Join-Path $BackendDir "venv\Scripts\python.exe"
$StopFlag   = Join-Path $BackendDir ".stop-background"
$LogDir     = Join-Path $BackendDir "logs"
$Log        = Join-Path $LogDir "backend-runner.log"
# 8000 first; if another program holds it (Docker Desktop restarts other projects' containers, e.g. one
# publishing 8000, as soon as this script starts Docker), the next free one. start-aitrading.ps1 and
# stop-aitrading.ps1 look on the same ports.
$Ports      = 8000, 8010, 8020
$Port       = $Ports[0]
$DockerExe  = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# One runner per PC, whichever way it was started (log-in launcher, start script, ...): a Global name
# is shared across Windows sessions; fall back to a per-session name if Windows refuses it.
try { $mutex = New-Object System.Threading.Mutex($false, "Global\AiTradingBackendRunner") }
catch { $mutex = New-Object System.Threading.Mutex($false, "Local\AiTradingBackendRunner") }
try { $owned = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $owned = $true }
if (-not $owned) { exit 0 }      # another runner is already keeping the backend alive

# Belt and braces: a PID file naming the live runner (a lock can be left "abandoned" by a killed runner).
$PidFile = Join-Path $BackendDir ".runner.pid"
if (Test-Path $PidFile) {
    $other = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($other -and [int]$other -ne $PID) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$other)" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -like "*run-backend.ps1*") { exit 0 }
    }
}
Set-Content -Path $PidFile -Value $PID

function Write-RunnerLog([string]$Text) {
    Add-Content -Path $Log -Encoding UTF8 -Value ("{0:yyyy-MM-dd HH:mm:ss} | RUNNER | {1}" -f (Get-Date), $Text)
}

function Test-Backend {
    # An AiTrading backend serving on any of our ports (ours, or one started by hand).
    foreach ($p in $Ports) {
        try { if ((Invoke-RestMethod -Uri "http://localhost:$p/health" -TimeoutSec 3).app -eq "AiTrading") { return $true } }
        catch { }
    }
    return $false
}

function Select-FreePort {
    foreach ($p in $Ports) {
        if (-not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)) { return $p }
    }
    return $null
}

function Wait-Database {
    # The database runs in Docker; start Docker Desktop and the container if needed (up to ~3 minutes).
    for ($i = 0; $i -lt 36; $i++) {
        & docker info --format "{{.ServerVersion}}" *> $null
        if ($LASTEXITCODE -eq 0) {
            & docker compose -f (Join-Path $Root "docker-compose.yml") up -d postgres *> $null
            $ready = (& docker exec aitrading-pg pg_isready -U aitrading 2>$null) -match "accepting connections"
            if ($ready) { return $true }
        } elseif ($i -eq 0 -and (Test-Path $DockerExe)) {
            Start-Process $DockerExe
        }
        Start-Sleep -Seconds 5
    }
    return $false
}

Remove-Item $StopFlag -ErrorAction SilentlyContinue   # being started = the user wants it running
Write-RunnerLog "runner started"
$failures = 0
while (-not (Test-Path $StopFlag)) {
    if (Test-Backend) { Start-Sleep -Seconds 30; continue }    # someone else's backend is serving: watch it
    if (-not (Wait-Database)) { Write-RunnerLog "database not available yet; retrying"; Start-Sleep -Seconds 30; continue }

    # Chosen after Docker is up, so a container that grabbed 8000 on Docker's start is already visible.
    $Port = Select-FreePort
    if ($null -eq $Port) { Write-RunnerLog "ports $($Ports -join ', ') are all in use; retrying"; Start-Sleep -Seconds 30; continue }
    Write-RunnerLog "starting backend on port $Port"
    $started = Get-Date
    $env:AITRADING_RUNNER = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONWARNINGS = "ignore::DeprecationWarning"   # library notices flooded the log and hid real errors
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 20MB) { Move-Item $Log "$Log.1" -Force }   # keep it small
    Push-Location $BackendDir
    # cmd's redirection writes the backend's output as-is (UTF-8); PowerShell 5.1's would write UTF-16.
    # Per-request access lines are left out: the app's own log (logs\aitrading.log) has what matters.
    & cmd.exe /c "`"$Python`" -m uvicorn main:app --port $Port --no-access-log >> `"$Log`" 2>&1"
    $code = $LASTEXITCODE
    Pop-Location
    if (Test-Path $StopFlag) { break }

    # Back off if it keeps failing straight away (e.g. a broken install), so the log isn't flooded.
    $failures = if (((Get-Date) - $started).TotalMinutes -lt 2) { $failures + 1 } else { 0 }
    $wait = [Math]::Min(300, 10 * [Math]::Pow(2, [Math]::Min($failures, 5)))
    Write-RunnerLog "backend exited (code $code); restarting in $wait s"
    Start-Sleep -Seconds $wait
}
Write-RunnerLog "stopped on request"
Remove-Item $PidFile -ErrorAction SilentlyContinue
try { $mutex.ReleaseMutex() } catch { }
