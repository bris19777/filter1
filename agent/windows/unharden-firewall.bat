@echo off
REM filter1 layer 3: turn OFF default-deny outbound (revert). Run as administrator.
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0unharden-firewall.ps1"
pause
