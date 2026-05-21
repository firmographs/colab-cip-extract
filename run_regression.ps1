# run_regression.ps1
# Runs the CIP regression harness one week at a time.
# Each week invocation stays well under 14 minutes for local agencies;
# --resume skips anything already marked ok so you can re-run safely.
#
# Usage:
#   .\run_regression.ps1                   # all weeks
#   .\run_regression.ps1 -Weeks w1326      # single week
#   .\run_regression.ps1 -Weeks w1326,w1426
#   .\run_regression.ps1 -Limit 5          # test run (5 agencies total)

param(
    [string]$Weeks = "w1326,w1426,w1526,w1626,w1726,w1826,w1926,w2026",
    [int]$Limit = 0
)

$RepoRoot = $PSScriptRoot
$Python   = "python"

$WeekList = $Weeks -split ","

Write-Host "=== CIP Regression Runner ===" -ForegroundColor Cyan
Write-Host "Weeks: $($WeekList -join ', ')"
Write-Host "Repo:  $RepoRoot"
Write-Host ""

foreach ($week in $WeekList) {
    Write-Host "--- $week ---" -ForegroundColor Yellow

    $args = @(
        "$RepoRoot\regression.py",
        "--week", $week,
        "--resume",
        "--commit"
    )
    if ($Limit -gt 0) {
        $args += "--limit"
        $args += "$Limit"
    }

    & $Python @args

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Week $week exited with code $LASTEXITCODE — continuing to next week" -ForegroundColor Red
    } else {
        Write-Host "  Week $week complete." -ForegroundColor Green
    }

    Write-Host ""
}

Write-Host "=== All weeks done ===" -ForegroundColor Cyan
