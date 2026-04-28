@echo off
REM Validate the live DB, then back it up. Used by the scheduled task.
REM Logs to scripts\backup_task.log so we can diagnose silent failures.
setlocal
cd /d "%~dp0\.."
set LOGFILE=%~dp0backup_task.log
echo. >> "%LOGFILE%"
echo === %DATE% %TIME% === >> "%LOGFILE%"
py -3.13 scripts\validate_db.py >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo validate_db failed with errorlevel %ERRORLEVEL%, aborting backup >> "%LOGFILE%"
    exit /b %ERRORLEVEL%
)
py -3.13 scripts\backup_db.py %* >> "%LOGFILE%" 2>&1
exit /b %ERRORLEVEL%
