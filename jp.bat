@echo off
rem Usage: jp status ^| jp list prepared ^| jp show ID ^| jp approve ID ^| jp skip ID ^| jp mark ID interview
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=py -3
if exist scripts\python.txt set /p PY=<scripts\python.txt
%PY% -m jobpilot %*
