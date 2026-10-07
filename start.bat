@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CScalp Journal

set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Не найден Python 3.12 или новее.
  echo Установите его с https://www.python.org/downloads/ ^(отметьте галочку "Add python.exe to PATH"^)
  echo и запустите start.bat снова.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: создаю окружение, это займёт пару минут...
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 goto fail

if not defined CSCALP_WEB_PORT set "CSCALP_WEB_PORT=8777"
echo Загружаю историю из логов терминала...
".venv\Scripts\python.exe" -m cscalp_journal.ingest
echo.
echo Дневник откроется по адресу http://127.0.0.1:%CSCALP_WEB_PORT%
echo Чтобы остановить дневник, закройте это окно.
if not defined CSCALP_NO_BROWSER start "" "http://127.0.0.1:%CSCALP_WEB_PORT%"
".venv\Scripts\python.exe" -m cscalp_journal.web
pause
exit /b 0

:fail
echo.
echo Не удалось подготовить окружение. Проверьте интернет и запустите start.bat снова.
pause
exit /b 1
