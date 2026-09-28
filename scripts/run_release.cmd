@echo off
REM Launcher for the 04:00 release, for Windows Task Scheduler to call.
REM
REM nightly_release.py works out the window itself and sleeps until 20 minutes
REM before it, so this can be started any time in the hour or two beforehand --
REM the task is set for 03:00 Asia/Tashkent, which leaves 40 minutes of margin
REM before it pauses the bots.
REM
REM Everything it does goes to scripts\release.log and, at the end, into
REM family.events, which ManagerBot forwards as a Telegram message. So the
REM outcome arrives whether or not anybody was watching this window.
REM
REM   schtasks /Run /TN "BotFamily release"      run it now
REM   schtasks /Delete /TN "BotFamily release"   call it off
REM   type scripts\release.log                   what happened

setlocal
cd /d "%~dp0.."
echo. >> "%~dp0release.log"
echo === launched by Task Scheduler at %DATE% %TIME% >> "%~dp0release.log"
python "%~dp0nightly_release.py" %*
echo === exit code %ERRORLEVEL% >> "%~dp0release.log"
endlocal
