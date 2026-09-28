# Launches the family, each bot in its own PowerShell window, so you don't
# have to open/cd/run five terminals by hand. The one shared Postgres must
# already be running -- either the Windows service, or `docker compose up -d`
# in this folder.
#
# ManagerBot goes last on purpose: it starts by reporting whichever bots it
# cannot see yet, and giving the other four a few seconds' head start keeps
# that from being four "is down" messages every time you start the family.
#
# Usage:  .\start_all.ps1                      # all five
#         .\start_all.ps1 -Exclude ManagerBot  # the four; ManagerBot lives in the cloud
#         .\start_all.ps1 -Only Sticker,Convert
# Stop:   close each window (Ctrl+C), or run .\stop_all.ps1
#
# -Exclude and -Only match on either the display name or the folder name,
# case-insensitively and by prefix: "manager", "ManagerBot" and "manager_bot"
# all pick out the same bot. -Only wins if you pass both.

param(
    [string[]]$Exclude = @(),
    [string[]]$Only = @()
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$bots = @(
    @{ Name = "StickerBot";    Dir = "sticker_bot" },
    @{ Name = "ConvertBot";    Dir = "convert_bot" },
    @{ Name = "DownloaderBot"; Dir = "downloader_bot" },
    @{ Name = "AnonBot";       Dir = "anon_bot" },
    @{ Name = "ManagerBot";     Dir = "manager_bot" }
)

# "sticker", "StickerBot" and "sticker_bot" should all select StickerBot, so
# compare against both spellings with the underscores taken out.
function Test-BotMatch([hashtable]$Bot, [string]$Pattern) {
    $p = $Pattern.Replace("_", "")
    return ($Bot.Name -like "$p*") -or ($Bot.Dir.Replace("_", "") -like "$p*")
}

foreach ($pattern in ($Only + $Exclude)) {
    if (-not ($bots | Where-Object { Test-BotMatch $_ $pattern })) {
        Write-Warning "'$pattern' matches none of the five bots -- check the spelling."
    }
}

if ($Only.Count) {
    $bots = $bots | Where-Object { $b = $_; $Only | Where-Object { Test-BotMatch $b $_ } }
} elseif ($Exclude.Count) {
    $bots = $bots | Where-Object { $b = $_; -not ($Exclude | Where-Object { Test-BotMatch $b $_ }) }
}

if (-not $bots) {
    Write-Warning "Nothing left to start."
    return
}

foreach ($bot in $bots) {
    $path = Join-Path $root $bot.Dir
    $envFile = Join-Path $path ".env"
    if (-not (Test-Path $envFile)) {
        Write-Warning "$($bot.Name): no .env found at $envFile -- skipping. Copy .env.example to .env first."
        continue
    }

    $cmd = "`$host.UI.RawUI.WindowTitle = '$($bot.Name)'; Set-Location '$path'; python bot.py"
    if ($bot.Name -eq "ManagerBot") { Start-Sleep -Seconds 5 }
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmd
    Write-Host "Started $($bot.Name)"
}
