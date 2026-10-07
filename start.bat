@echo off
cd /d "%~dp0"
title CScalp Journal

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.12 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo then run start.bat again.
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.12 or newer is required, your version is older.
  echo Install the latest Python from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo then run start.bat again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating the Python environment, this takes a couple of minutes...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create the Python environment.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Could not install the libraries. Check your internet connection and run start.bat again.
  pause
  exit /b 1
)

rem cTrader support is optional: the journal works without it
".venv\Scripts\python.exe" -c "import ctrader_open_api" >nul 2>nul
if errorlevel 1 (
  echo Installing the optional cTrader module...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-ctrader.txt >nul 2>nul
  if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -q --no-deps -r requirements-ctrader-nodeps.txt >nul 2>nul
    if errorlevel 1 echo The cTrader module could not be installed. The journal works without it.
  )
)

if not defined CSCALP_WEB_PORT set "CSCALP_WEB_PORT=8777"
echo Loading history from the terminal logs...
".venv\Scripts\python.exe" -m cscalp_journal.ingest
echo.
echo The journal opens at http://127.0.0.1:%CSCALP_WEB_PORT%
echo Close this window to stop the journal.
if not defined CSCALP_NO_BROWSER start "" "http://127.0.0.1:%CSCALP_WEB_PORT%"
".venv\Scripts\python.exe" -m cscalp_journal.web
pause
