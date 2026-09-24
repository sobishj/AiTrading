@echo off
rem Double-click to stop the TradeAI backend and frontend.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-tradeai.ps1"
echo.
pause
