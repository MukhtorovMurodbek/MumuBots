<#
Runs the bots' real code on a spare Telegram token, so a new version can be
tried in a real Telegram client before it reaches anybody. On this machine
only.

    .\testbot\run.ps1                # all five at once; switch in the chat with /asConvert,
                                     # /asSticker, /asDownloader, /asAnon, /asManager (or /convertbot...)
    .\testbot\run.ps1 all anon       # all five, starting as AnonBot
    .\testbot\run.ps1 anon           # AnonBot alone -- sticker, convert, downloader, anon or manager
    .\testbot\run.ps1 audit          # every command each bot handles, against its menus; then exit

Every one of them is testbot\switch.py, which explains itself at the top:
one process and one poller, one TestBot at a time, its own names, tables and
wallet, and on the shared database a login that cannot write anything else.

WHAT YOU HAVE TO DO FIRST
    Copy testbot\token.env.example to testbot\token.env and paste your bot
    token into the TESTBOT_TOKEN line. That is the only edit. Everything
    else in that file already works.

WHY A BOT IS NOT RUN AS ITS OWN bot.py ANY MORE
    Until 2.0.0 `run.ps1 anon` ran anon_bot\bot.py directly. That run used
    the bot's real name where lifecycle keys the poll lease, the maintenance
    flag and the restart marker, and python-dotenv read the bot folder's own
    .env -- the real one -- for every setting run.ps1 did not set. switch.py
    fixes both for one bot as for five, and it is still the bot's own files
    that run.
#>
param([string]$Which, [string]$Start)

$switch = Join-Path $PSScriptRoot "switch.py"
if (-not $Which -or $Which -eq "all") {
    $arguments = @()
    if ($Start) { $arguments += $Start }
} elseif ($Which -eq "audit") {
    $arguments = @("--audit")
} else {
    $arguments = @("--only", $Which)
}
python -u $switch @arguments
