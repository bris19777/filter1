@echo off
REM filter1 local status / diagnostics. Double-click to see what works and why.
chcp 65001 >nul
"%~dp0py\python.exe" "%~dp0status.py"
echo.
echo (הדוח נשמר גם ב-C:\ProgramData\filter1\status.txt)
pause
