@echo off
REM filter1 layer 5: install the mitmproxy content filter. Run as administrator.
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-proxy.ps1"
pause
