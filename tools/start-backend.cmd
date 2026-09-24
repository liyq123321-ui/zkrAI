@echo off
REM firstFlight 8088 backend launcher.
REM Wired as the Startup-folder item "firstFlight-backend.cmd".
REM Idempotent: exits without starting anything when 8088 is already served,
REM so calling it by hand while the server runs is safe.
REM
REM Marker lines go to backend\data\backend-8088.launcher.log -- a file nothing
REM else writes to. uvicorn's own stdout/stderr go to backend-8088.out.log.
REM Keeping them apart means the launcher trace stays readable even though the
REM server keeps that other file growing, and autostart (invisible console)
REM cannot fail silently.

setlocal

REM Normalise the repo root. pushd resolves the "..", which matters: %~dp0
REM already ends in a backslash, so a root built as "%~dp0.." would turn
REM "%ROOT%backend" into "\tools\..backend" and lose the separator.
pushd "%~dp0.." 2>nul || exit /b 1
set "ROOT=%CD%"
popd

set "PY=%ROOT%\backend\.venv\Scripts\python.exe"
set "DATADIR=%ROOT%\backend\data"
set "LAUNCHLOG=%DATADIR%\backend-8088.launcher.log"
set "OUTLOG=%DATADIR%\backend-8088.out.log"

if not exist "%DATADIR%" mkdir "%DATADIR%" 2>nul

if not exist "%PY%" (
  >>"%LAUNCHLOG%" echo [firstFlight] %DATE% %TIME% FATAL venv python not found: %PY%
  exit /b 1
)

cd /d "%ROOT%\backend" || exit /b 1

netstat -ano | findstr /r /c:":8088 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  >>"%LAUNCHLOG%" echo [firstFlight] %DATE% %TIME% 8088 already listening, skip.
  exit /b 0
)

>>"%LAUNCHLOG%" echo [firstFlight] %DATE% %TIME% starting uvicorn on 127.0.0.1:8088
"%PY%" -m uvicorn main:app --host 127.0.0.1 --port 8088 --env-file .env >> "%OUTLOG%" 2>&1
>>"%LAUNCHLOG%" echo [firstFlight] %DATE% %TIME% uvicorn exited with code %ERRORLEVEL%
