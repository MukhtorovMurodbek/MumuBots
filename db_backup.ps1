<#
Download the family database, and upload it back.

    .\db_backup.ps1 save                    # cloud -> backups\family_<stamp>.dump
    .\db_backup.ps1 save -Source local      # laptop -> backups\...
    .\db_backup.ps1 load -File <path>       # a dump -> the LOCAL database
    .\db_backup.ps1 load -File <path> -Target cloud -Force
    .\db_backup.ps1 pull                    # cloud -> laptop, in one step
    .\db_backup.ps1 push -Force             # laptop -> cloud, in one step
    .\db_backup.ps1 list                    # what's in backups\

There is exactly one database for the whole family now, so all five bots come
down in one file and go back up in one command.

TWO ENGINES, PICKED AUTOMATICALLY
  pg_dump / pg_restore, when they are on PATH and new enough for the server.
  This is a real backup: tables, indexes, constraints, sequences, the lot.
  A "load" through this engine REPLACES the target's contents.

  manager_bot\family_db.py otherwise -- pure Python over the same psycopg the
  bots use, so it needs no Postgres installed locally and cannot hit a
  version mismatch. It moves rows, not structure, so the target's tables
  must already exist (they do as soon as each bot has started once). Its
  default is additive: rows the target already has are left alone.

  Force the Python one with -Portable if you want a version-proof file you
  can restore anywhere.

WHERE THE URLS COME FROM
  manager_bot\.env  ->  DATABASE_URL (local) and CLOUD_DATABASE_URL (cloud).
  For CLOUD_DATABASE_URL use Railway's PUBLIC connection string -- the
  private .railway.internal one only resolves inside Railway itself.

NOTHING HERE IS SILENT ABOUT DESTRUCTION. Writing into the cloud, or any
restore that replaces rather than merges, needs -Force.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("save", "load", "pull", "push", "list")]
    [string]$Action,

    [ValidateSet("local", "cloud")]
    [string]$Source = "cloud",

    [ValidateSet("local", "cloud")]
    [string]$Target = "local",

    [string]$File,
    [switch]$Portable,
    [switch]$Merge,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$backupDir = Join-Path $PSScriptRoot "backups"
$envFile = Join-Path $PSScriptRoot "manager_bot\.env"

function Get-EnvValue([string]$key) {
    if (-not (Test-Path $envFile)) {
        throw "No manager_bot\.env yet. Copy manager_bot\.env.example to manager_bot\.env and fill in DATABASE_URL (and CLOUD_DATABASE_URL to reach Railway)."
    }
    $line = Select-String -Path $envFile -Pattern "^\s*$key\s*=\s*(.+)$" | Select-Object -First 1
    if (-not $line) { return $null }
    return $line.Matches[0].Groups[1].Value.Trim().Trim('"').Trim("'")
}

function Get-Dsn([string]$alias) {
    $key = if ($alias -eq "local") { "DATABASE_URL" } else { "CLOUD_DATABASE_URL" }
    $dsn = Get-EnvValue $key
    if (-not $dsn) {
        throw "$key is not set in manager_bot\.env, so I don't know where '$alias' is."
    }
    return $dsn
}

function Get-PgTool([string]$name) {
    $tool = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $tool) { return $null }
    return $tool.Source
}

# Where the last Save-Database call actually wrote. A PowerShell function
# returns everything its callees print, and pg_dump/python both print, so the
# path cannot simply be `return`ed.
$script:LastSaved = $null

function Invoke-Portable {
    param([string[]]$Arguments)
    Push-Location (Join-Path $PSScriptRoot "manager_bot")
    try {
        python family_db.py @Arguments
        if ($LASTEXITCODE -ne 0) { throw "family_db.py exited with $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

function Save-Database([string]$alias, [string]$outFile) {
    $dsn = Get-Dsn $alias
    if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory $backupDir | Out-Null }

    $pgDump = if ($Portable) { $null } else { Get-PgTool "pg_dump" }
    if ($pgDump) {
        Write-Host "pg_dump: $alias -> $outFile" -ForegroundColor Cyan
        & $pgDump --format=custom --no-owner --no-acl --file=$outFile $dsn
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "pg_dump failed (often a server newer than your local Postgres). Falling back to the portable exporter."
            $outFile = [IO.Path]::ChangeExtension($outFile, ".zip")
            Invoke-Portable @("backup", "--from", $dsn, "--out", $outFile)
        }
    } else {
        $outFile = [IO.Path]::ChangeExtension($outFile, ".zip")
        Write-Host "portable export: $alias -> $outFile" -ForegroundColor Cyan
        Invoke-Portable @("backup", "--from", $dsn, "--out", $outFile)
    }
    Write-Host "Saved $outFile" -ForegroundColor Green
    $script:LastSaved = $outFile
}

function Load-Database([string]$alias, [string]$inFile) {
    if (-not (Test-Path $inFile)) { throw "No such file: $inFile" }
    $dsn = Get-Dsn $alias

    if ($alias -eq "cloud" -and -not $Force) {
        throw "Writing into the CLOUD database needs -Force. Run the same command again with -Force once you've read what it will do."
    }

    if ([IO.Path]::GetExtension($inFile) -eq ".zip") {
        $mode = if ($Merge) { "merge" } else { "replace" }
        if ($mode -eq "replace" -and -not $Force) {
            throw "A portable restore defaults to replacing every table's contents. Add -Force to go ahead, or -Merge to only add rows the target is missing."
        }
        Write-Host "portable restore ($mode): $inFile -> $alias" -ForegroundColor Cyan
        Invoke-Portable @("restore", "--into", $dsn, "--file", (Resolve-Path $inFile).Path, "--mode", $mode, "--yes")
    } else {
        $pgRestore = Get-PgTool "pg_restore"
        if (-not $pgRestore) {
            throw "That's a pg_dump custom-format file but pg_restore isn't on PATH. Either install the Postgres client tools, or take portable (.zip) backups instead: .\db_backup.ps1 save -Portable"
        }
        if (-not $Force) {
            throw "pg_restore replaces the target's contents. Add -Force once you're sure."
        }
        Write-Host "pg_restore: $inFile -> $alias" -ForegroundColor Cyan
        & $pgRestore --clean --if-exists --no-owner --no-acl --dbname=$dsn $inFile
        if ($LASTEXITCODE -ne 0) {
            # pg_restore reports non-fatal noise (missing objects a --clean pass
            # tried to drop) through the same exit code as real failures.
            Write-Warning "pg_restore exited with $LASTEXITCODE -- check the messages above. Warnings about objects that did not exist are normal on a fresh database."
        }
    }
    Write-Host "Loaded into $alias." -ForegroundColor Green
}

$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"

switch ($Action) {
    "list" {
        if (-not (Test-Path $backupDir)) { Write-Host "No backups yet."; return }
        Get-ChildItem $backupDir -File | Sort-Object LastWriteTime -Descending |
            Select-Object Name, @{n = "Size"; e = { "{0:N0} KB" -f ($_.Length / 1KB) } }, LastWriteTime |
            Format-Table -AutoSize
    }
    "save" {
        Save-Database $Source (Join-Path $backupDir "family_${Source}_$stamp.dump")
    }
    "load" {
        if (-not $File) { throw "Which file? .\db_backup.ps1 load -File backups\family_...dump" }
        Load-Database $Target $File
    }
    "pull" {
        # Asking to pull IS the confirmation that the laptop copy gets replaced;
        # the cloud side is only ever read here.
        $Force = $true
        Save-Database "cloud" (Join-Path $backupDir "family_cloud_$stamp.dump")
        Write-Host ""
        Load-Database "local" $script:LastSaved
    }
    "push" {
        if (-not $Force) { throw "push overwrites the CLOUD database with your laptop's copy. Add -Force." }
        Save-Database "local" (Join-Path $backupDir "family_local_$stamp.dump")
        Write-Host ""
        Load-Database "cloud" $script:LastSaved
    }
}
