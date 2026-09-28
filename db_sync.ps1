<#
Row-level, additive sync of the family database between your laptop and the
cloud (Railway) copy, one bot at a time.

    .\db_sync.ps1 pull          # preview: cloud-only rows -> laptop
    .\db_sync.ps1 pull -Apply   # actually copy them
    .\db_sync.ps1 push -Apply   # laptop-only rows -> cloud
    .\db_sync.ps1 both -Apply   # both directions, so the two end up identical

Nothing is ever deleted or overwritten. Each bot's db_merge.py matches rows by
their natural key and only ever INSERTs ones the target is missing. If both
sides changed the same row it is left alone and printed for you to look at.

Without -Apply everything runs inside a transaction that is rolled back, so a
preview genuinely cannot change anything.

WHEN TO USE THIS RATHER THAN db_backup.ps1
    db_backup.ps1 moves the whole database at once and replaces the target.
    This one is surgical: it reconciles the two copies row by row, adds only
    what is missing, and never deletes or overwrites anything -- which is what
    you want when both sides have been running and each has rows the other
    doesn't.

There is one shared database now, with a schema per bot, so a single Railway
Postgres serves all five services. (An earlier version of this script claimed
Railway cannot attach more than one service to a database -- that was simply
wrong; any number of services can reference the same database's variables.)
Each bot still has its own db_merge.py because each knows its own tables'
natural keys, so this loops over the four of them. ManagerBot has nothing worth
syncing: family.heartbeats and family.events are about a running process, and
mean nothing once copied to another machine.
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("pull", "push", "both")]
    [string]$Direction,
    [switch]$Apply
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$bots = @("sticker_bot", "convert_bot", "downloader_bot", "anon_bot")

$moves = switch ($Direction) {
    "pull" { @(@{ From = "cloud"; Into = "local" }) }
    "push" { @(@{ From = "local"; Into = "cloud" }) }
    "both" { @(@{ From = "cloud"; Into = "local" }, @{ From = "local"; Into = "cloud" }) }
}

if (-not $Apply) {
    Write-Host "`nPREVIEW ONLY -- nothing will be written. Add -Apply to commit.`n" -ForegroundColor Yellow
}

foreach ($bot in $bots) {
    $dir = Join-Path $PSScriptRoot $bot
    $envFile = Join-Path $dir ".env"
    if (-not (Test-Path $envFile)) {
        Write-Warning "$bot : no .env -- skipping (needs DATABASE_URL and CLOUD_DATABASE_URL)"
        continue
    }

    # Every direction here involves the cloud side, so fail early and clearly
    # rather than letting each db_merge.py print the same connection error.
    $hasCloud = Select-String -Path $envFile -Pattern '^\s*CLOUD_DATABASE_URL\s*=\s*\S' -Quiet
    if (-not $hasCloud) {
        Write-Warning "$bot : CLOUD_DATABASE_URL is not set in $bot\.env -- skipping."
        Write-Host   "         Add it once the family is deployed: copy the PUBLIC connection string" -ForegroundColor DarkGray
        Write-Host   "         from the shared Railway Postgres into $bot\.env as CLOUD_DATABASE_URL=..." -ForegroundColor DarkGray
        Write-Host   "         (the same value for every bot -- they differ only by DB_SCHEMA)" -ForegroundColor DarkGray
        continue
    }

    foreach ($m in $moves) {
        Write-Host "=== $bot : $($m.From) -> $($m.Into) ===" -ForegroundColor Cyan
        Push-Location $dir
        $pyArgs = @("db_merge.py", "--from", $m.From, "--into", $m.Into)
        if ($Apply) { $pyArgs += "--yes" } else { $pyArgs += "--dry-run" }
        python @pyArgs
        Pop-Location
        Write-Host ""
    }
}

if (-not $Apply) {
    Write-Host "Preview finished. Re-run with -Apply to actually sync.`n" -ForegroundColor Yellow
} else {
    Write-Host "Sync finished.`n" -ForegroundColor Green
}
