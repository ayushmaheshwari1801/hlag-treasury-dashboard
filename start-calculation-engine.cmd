@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0treasury-calculation-engine"
set "CASH_ENGINE=spec"
if not defined NEWS_V8_DATABASE set "NEWS_V8_DATABASE=%~dp0data\news-current.sqlite"
set "ENGINE_MARKET_DB=%~dp0treasury-calculation-engine\hlag_engine\data\market.sqlite"
rem Uses the working Python environment currently available on this computer.
call start.cmd %*
