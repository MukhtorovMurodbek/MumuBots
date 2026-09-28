# Stops the "python bot.py" processes started by start_all.ps1 (or run by
# hand). Matches on command line, not just process name, so it won't touch
# unrelated python processes on the machine.
#
# Usage:  .\stop_all.ps1                      # all of them
#         .\stop_all.ps1 -Exclude ManagerBot   # leave ManagerBot running
#         .\stop_all.ps1 -Only Downloader
#
# The filters match the same way start_all.ps1's do -- display name or folder
# name, case-insensitive, by prefix.
#
# Which bot a python process *is* isn't in its own command line (they all read
# "python bot.py"; the folder only ever appears as the working directory). It
# is in the parent window's command line, because start_all.ps1 puts a
# Set-Location there. A bot started some other way therefore can't be
# identified -- so when a filter is in play, those are left alone and
# reported rather than guessed at and killed.

param(
    [string[]]$Exclude = @(),
    [string[]]$Only = @()
)

$dirs = @("sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot")

function Test-DirMatch([string]$Dir, [string]$Pattern) {
    $p = $Pattern.Replace("_", "")
    return $Dir.Replace("_", "") -like "$p*"
}

$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match "bot\.py" }

if (-not $procs) {
    Write-Host "No running bot.py processes found."
    return
}

$filtering = $Only.Count -or $Exclude.Count

foreach ($p in $procs) {
    $dir = $null
    if ($p.ParentProcessId) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent) {
            $dir = $dirs | Where-Object { $parent.CommandLine -match [regex]::Escape($_) } | Select-Object -First 1
        }
    }

    if ($filtering -and -not $dir) {
        Write-Warning "PID $($p.ProcessId): can't tell which bot this is -- leaving it running. Stop it from its own window."
        continue
    }

    if ($Only.Count) {
        if (-not ($Only | Where-Object { Test-DirMatch $dir $_ })) { continue }
    } elseif ($Exclude.Count) {
        if ($Exclude | Where-Object { Test-DirMatch $dir $_ }) {
            Write-Host "Leaving $dir running (PID $($p.ProcessId))."
            continue
        }
    }

    $label = if ($dir) { $dir } else { $p.CommandLine }
    Write-Host "Stopping PID $($p.ProcessId): $label"
    Stop-Process -Id $p.ProcessId -Force
}
