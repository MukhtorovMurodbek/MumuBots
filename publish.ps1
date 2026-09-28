<#
RETIRED IN 2.0.0.

Until 2.0.0 this pushed each bot folder of this monorepo to a public
repository of its own -- StickerBot, ConvertBot, DownloaderBot, AnonBot,
ParentBot -- one commit per release, and those were what Railway rebuilt
from. Since 2.0.0 the five bots share shared/, and each is built from the
whole repository with its own Dockerfile, so a bot folder published on its
own would not run. The owner is replacing the five repositories with one,
MumuBots: docs/changes/CHANGES_v2.0.0.md, "GitHub: 12 repositories become 3".

A release is now a push of this folder to MumuBots, which
scripts/nightly_release.py makes in its window; with --edition C it pushes
the C folder to MumuCombined instead. The last working copy of this script
is BotFamily1.7.0\publish.ps1.

Kept, printing this, so that the old habit gets an answer rather than
"not recognized".
#>
Write-Host ""
Write-Host "publish.ps1 is retired: since 2.0.0 the five bots share shared/ and deploy from one" -ForegroundColor Yellow
Write-Host "repository, MumuBots, so a bot folder no longer runs on its own. A release is a push" -ForegroundColor Yellow
Write-Host "of this folder: python scripts\nightly_release.py (--dry-run to see it first)." -ForegroundColor Yellow
Write-Host "Why, and the move to MumuBots: docs\changes\CHANGES_v2.0.0.md, 'GitHub: 12 repositories become 3'." -ForegroundColor Yellow
Write-Host ""
exit 1
