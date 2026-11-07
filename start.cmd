@echo off
setlocal
cd /d "%~dp0"
set "DASHBOARD_VENV=.venv"
if exist ".venv-local\Scripts\python.exe" set "DASHBOARD_VENV=.venv-local"
if not exist "%DASHBOARD_VENV%\Scripts\python.exe" (
  echo Run setup.cmd first. The root .venv is the shared Python environment.
  pause
  exit /b 1
)
"%DASHBOARD_VENV%\Scripts\python.exe" run_dashboard.py %*
if errorlevel 1 (
  echo Startup failed. Review the error above. If port 8080 is occupied, run start.cmd --port 8081.
  pause
  exit /b 1
)
