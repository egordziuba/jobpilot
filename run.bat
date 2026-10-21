@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=py -3
if exist scripts\python.txt set /p PY=<scripts\python.txt
if not exist logs mkdir logs
echo JobPilot Auto: searching, please wait...
%PY% -m jobpilot run >> logs\task.log 2>&1
if "%1"=="" start "" "data\reports\latest.html"
