<#
  Stop the AiTrading backend and frontend started by start-aitrading.ps1, and close
  their console windows. Docker (database) and Bionic are left running.
#>
function Get-ConsoleRoot([int]$ProcessId) {
    # Walk up from a server process to the outermost cmd.exe window that hosts it
    # (npm/vite retitle their window, so windows can't be found by title alone).
    $root = $ProcessId
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    while ($current) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($current.ParentProcessId)"
        if (-not $parent -or $parent.Name -notin @("cmd.exe", "node.exe", "python.exe")) { break }
        $root = $parent.ProcessId
        $current = $parent
    }
    return $root
}

function Stop-Server([int]$Port, [string]$Name) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return $false }
    $root = Get-ConsoleRoot $conn.OwningProcess
    & taskkill.exe /PID $root /T /F *> $null   # the whole tree: window, npm/uvicorn, workers
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped $Name (port $Port)." -ForegroundColor Green
    return $true
}

# Tell the background runner (run-backend.ps1) this stop is deliberate, so it doesn't restart the backend.
$StopFlag = Join-Path $PSScriptRoot "backend\.stop-background"
Set-Content -Path $StopFlag -Value (Get-Date -Format o)

# Only stop a backend port that is actually AiTrading (8000 may belong to another program).
$backendStopped = $false
foreach ($port in 8000, 8010) {
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:$port/health" -TimeoutSec 3
        if ($health.app -eq "AiTrading") { $backendStopped = (Stop-Server $port "AiTrading backend") -or $backendStopped }
    } catch { }
}
if (-not $backendStopped) { Write-Host "Backend was not running." }
if (-not (Stop-Server 5173 "AiTrading frontend")) { Write-Host "Frontend was not running." }

Write-Host "AiTrading stopped (including background learning) until you start it again."
Write-Host "If 'Start with Windows' is on, it starts again at your next log-in."
