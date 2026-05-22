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
    [string]$Weeks  = "w1326,w1426,w1526,w1626,w1726,w1826,w1926,w2026",
    [int]$Limit     = 0,
    [switch]$Resume
)

$RepoRoot = $PSScriptRoot
$WeekList = $Weeks -split ","

Write-Host "=== CIP Regression Runner (parallel) ===" -ForegroundColor Cyan
Write-Host "Weeks: $($WeekList -join ', ')"
Write-Host "Repo:  $RepoRoot"
if ($Resume) { Write-Host "Mode:  --resume (skipping already-ok agencies)" }
Write-Host ""

$jobs = @()
foreach ($week in $WeekList) {
    Write-Host "Launching $week ..." -ForegroundColor Yellow

    $scriptArgs = @(
        "$RepoRoot\regression.py",
        "--week", $week,
        "--commit"
    )
    if ($Resume) { $scriptArgs += "--resume" }
    if ($Limit -gt 0) {
        $scriptArgs += "--limit"
        $scriptArgs += "$Limit"
    }

    $job = Start-Job -ScriptBlock {
        param($RepoRoot, $scriptArgs)
        Set-Location $RepoRoot
        & doppler run --project firmographs --config prd -- python @scriptArgs 2>&1
    } -ArgumentList $RepoRoot, $scriptArgs

    $jobs += [pscustomobject]@{ Week = $week; Job = $job }
}

Write-Host ""
Write-Host "All $($jobs.Count) weeks launched. Waiting for completion..." -ForegroundColor Cyan
Write-Host ""

# Poll and report as each week finishes
$pending = [System.Collections.Generic.List[object]]($jobs)
while ($pending.Count -gt 0) {
    Start-Sleep -Seconds 30
    $done = $pending | Where-Object { $_.Job.State -ne 'Running' }
    foreach ($item in $done) {
        $out = Receive-Job -Job $item.Job
        $exitOk = $item.Job.State -eq 'Completed'
        $color = if ($exitOk) { "Green" } else { "Red" }
        Write-Host "--- $($item.Week) finished ---" -ForegroundColor $color
        $out | Select-Object -Last 8 | ForEach-Object { Write-Host "  $_" }
        Write-Host ""
        $pending.Remove($item) | Out-Null
    }
}

Write-Host "=== All weeks done. Merging results... ===" -ForegroundColor Cyan
& doppler run --project firmographs --config prd -- python "$RepoRoot\regression.py" --merge

Write-Host ""
Write-Host "=== Complete ===" -ForegroundColor Cyan
