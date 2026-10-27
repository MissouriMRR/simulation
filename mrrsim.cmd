@echo off
rem Run the MRRSimulator launcher straight from this checkout (no install step).
rem Needs Python 3.11 or newer on PATH (the launcher itself; student scripts use 3.12 in a container).
setlocal
set "PYTHONPATH=%~dp0launcher;%PYTHONPATH%"
python -m mrrsim %*
exit /b %ERRORLEVEL%
