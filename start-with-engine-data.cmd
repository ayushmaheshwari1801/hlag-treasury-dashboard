@echo off
setlocal
cd /d "%~dp0"
rem Starts the dashboard on the market data from your hlag_engine store (a copy, so the engine's own file is never changed).
rem Calculations use the dashboard's built-in engine (CASH_ENGINE=mock), because the hlag_engine program is not installed here.
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first.
  pause
  exit /b 1
)
if not exist "engine_market_copy.sqlite" (
  echo engine_market_copy.sqlite is missing from this folder.
  echo Copy market.sqlite from treasury-private-data\Documents\treasury-calculation-engine\hlag_engine\data
  echo into this folder and rename it to engine_market_copy.sqlite.
  pause
  exit /b 1
)
set "CASH_ENGINE=mock"
set "MARKET_DATA_DB=%~dp0engine_market_copy.sqlite"
echo Market data: %MARKET_DATA_DB%
echo When you see "Uvicorn running", open http://127.0.0.1:8080 in your browser. Keep this window open.
call start.cmd %*
