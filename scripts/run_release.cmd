@echo off
REM Launcher for the 04:00 release, for Windows Task Scheduler to call.
REM
REM nightly_release.py works out the window itself and sleeps until 20 minutes
REM before it, so this can be started any time in the hour or two beforehand --
REM the task is set for 03:00 Asia/Tashkent, which leaves 40 minutes of margin
REM before it pauses the bots.
REM
REM The arguments are passed straight on, and they are REQUIRED: production runs
REM the C edition, so the task must say --edition C. With no arguments the script
REM would release the base edition by default -- a push to the wrong repository
REM that changes nothing for anybody, after pausing the bots for it -- so with
REM none this refuses (exit 2) instead of guessing.
REM
REM It runs the venv in TempTools, not whichever python Task Scheduler's
REM environment happens to find first (or none).
REM
REM Everything it does goes to scripts\release.log and, at the end, into
REM family.events, which ManagerBot forwards as a Telegram message. So the
REM outcome arrives whether or not anybody was watching this window.
REM
REM   schtasks /Run /TN "BotFamily release"      run it now
REM   schtasks /Delete /TN "BotFamily release"   call it off
REM   type scripts\release.log                   what happened

setlocal
if "%~1"=="" (
    echo usage: run_release.cmd --edition C [--now] [--at HH:MM] [--dry-run]   arguments are required, so a task without --edition C cannot release the base folder by default
    exit /b 2
)
set "PY=C:\Bitura\.Projects\MumuBots\TempTools\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo run_release.cmd: the TempTools python was not found at %PY%
    exit /b 3
)
cd /d "%~dp0.."
echo. >> "%~dp0release.log"
echo === launched by Task Scheduler at %DATE% %TIME% >> "%~dp0release.log"
"%PY%" "%~dp0nightly_release.py" %*
set "RC=%ERRORLEVEL%"
echo === exit code %RC% >> "%~dp0release.log"
endlocal & exit /b %RC%
