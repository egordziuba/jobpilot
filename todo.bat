@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
chcp 65001 >nul
set PY=py -3
if exist scripts\python.txt set /p PY=<scripts\python.txt
%PY% -m jobpilot todo
pause
