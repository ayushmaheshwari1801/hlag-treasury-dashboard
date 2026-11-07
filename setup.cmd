@echo off
setlocal
cd /d "%~dp0"
set "DASHBOARD_VENV=.venv"
if exist ".venv-local\Scripts\python.exe" set "DASHBOARD_VENV=.venv-local"
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3.12 or later is required. Install Python and enable Add to PATH.
  goto fail
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 (
  echo Python 3.12 or later is required.
  goto fail
)
where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo Node.js with npm is required. Install Node.js and reopen this window.
  goto fail
)
if not exist "%DASHBOARD_VENV%\Scripts\python.exe" python -m venv "%DASHBOARD_VENV%"
if errorlevel 1 goto fail
"%DASHBOARD_VENV%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
"%DASHBOARD_VENV%\Scripts\python.exe" -m pip install -e treasury-calculation-engine
if errorlevel 1 goto fail
call npm.cmd --prefix treasury-dashboard/frontend ci
if errorlevel 1 goto fail
call npm.cmd --prefix treasury-dashboard/frontend run build
if errorlevel 1 goto fail
echo Setup complete. Both the dashboard backend and news v8 use %DASHBOARD_VENV%.
echo For a source package, restore the inputs using SOURCE_PACKAGE.md, then run start.cmd.
exit /b 0
:fail
echo Setup failed. Review the error above.
pause
exit /b 1
