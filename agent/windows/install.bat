@echo off
REM filter1 client installer launcher. Right-click -> Run as administrator.
REM Elevates and runs install.ps1 with the execution policy bypassed.

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
