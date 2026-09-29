@echo off
rem Double-click to stop the AiTrading backend and frontend.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-aitrading.ps1"
echo.
pause
