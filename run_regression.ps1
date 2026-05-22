# run_regression.ps1
# Runs the CIP regression harness — all weeks in parallel, one process per week.
# Each week writes to regression_results/regression_<week>.csv (no shared file conflicts).
# After all weeks finish, merges into regression_results.csv.
#
# Usage:
#   .\run_regression.ps1                   # all weeks in parallel
#   .\run_regression.ps1 -Weeks w1326      # single week
#   .\run_regression.ps1 -Weeks w1326,w1426
#   .\run_regression.ps1 -Limit 5          # test run (5 agencies per week)
#   .\run_regression.ps1 -Resume           # skip already-ok agencies

param(
    [string[]]$Weeks = @("w1326","w1426","w1526","w1626","w1726","w1826","w1926","w2026"),
    [int]$Limit      = 0,
    [switch]$Resume
)

$RepoRoot = $PSScriptRoot
$WeekList = $Weeks

Write-Host "=== CIP Regression Runner (parallel) ===" -ForegroundColor Cyan
Write-Host "Weeks: $($WeekList -join ', ')"
Write-Host "Repo:  $RepoRoot"
if ($Resume) { Write-Host "Mode:  --resume (skipping already-ok agencies)" }
Write-Host ""

# Launch one process per week, each writing to its own output log
$procs = @()
New-Item -ItemType Directory -Path "$RepoRoot\regression_results" -Force | Out-Null

foreach ($week in $WeekList) {
    Write-Host "Launching $week ..." -ForegroundColor Yellow

    $logFile = "$RepoRoot\regression_results\log_$week.txt"

    # Build argument list as array — avoids space-splitting issues in Start-Process
    $dopplerArgList = @(
        "run", "--project", "firmographs", "--config", "prd", "--",
        "python", "$RepoRoot\regression.py",
        "--week", $week,
        "--commit"
    )
    if ($Resume) { $dopplerArgList += "--resume" }
    if ($Limit -gt 0) { $dopplerArgList += @("--limit", "$Limit") }

    $proc = Start-Process -FilePath "doppler" `
        -ArgumentList $dopplerArgList `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError "$RepoRoot\regression_results\err_$week.txt" `
        -NoNewWindow -PassThru

    $procs += [pscustomobject]@{ Week = $week; Proc = $proc; Log = $logFile }
}

Write-Host ""
Write-Host "All $($procs.Count) weeks launched. Waiting for completion..." -ForegroundColor Cyan
Write-Host ""

# Poll every 60s and report as each week finishes
$pending = [System.Collections.Generic.List[object]]($procs)
while ($pending.Count -gt 0) {
    Start-Sleep -Seconds 60
    $done = $pending | Where-Object { $_.Proc.HasExited }
    foreach ($item in $done) {
        $color = if ($item.Proc.ExitCode -eq 0) { "Green" } else { "Red" }
        Write-Host "--- $($item.Week) finished (exit $($item.Proc.ExitCode)) ---" -ForegroundColor $color
        if (Test-Path $item.Log) {
            Get-Content $item.Log | Select-Object -Last 10 | ForEach-Object { Write-Host "  $_" }
        }
        Write-Host ""
        $pending.Remove($item) | Out-Null
    }
    if ($pending.Count -gt 0) {
        Write-Host "  Still running: $($pending.Week -join ', ')" -ForegroundColor DarkGray
    }
}

Write-Host "=== All weeks done. Merging results... ===" -ForegroundColor Cyan
& doppler run --project firmographs --config prd -- python "$RepoRoot\regression.py" --merge

Write-Host ""
Write-Host "=== Complete ===" -ForegroundColor Cyan
