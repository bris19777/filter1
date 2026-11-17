@echo off
setlocal EnableExtensions DisableDelayedExpansion
title User Profiles Backup (WinPE)
chcp 65001 >nul

rem ================================================================
rem  User profiles backup for WinPE (e.g. Strelec)
rem  - Finds every Windows install on the machine
rem  - Skips built-in / empty profiles
rem  - Copies 6 folders per user to an external drive:
rem      <Drive>:\Backup_<date>\<UserName>\<Folder>
rem ================================================================

set "FOLDERS=Desktop Documents Downloads Pictures Videos Music"
set "SKIP=All Users;Default;Default User;Public;defaultuser0;defaultuser100000;WDAGUtilityAccount;Default.migrated;TEMP"

echo.
echo ==============================================
echo    User Profiles Backup  (WinPE)
echo ==============================================
echo.

rem ---------- 1. Find Windows installations ----------
echo Searching for Windows installations...
echo.
set "COUNT=0"
for %%D in (C D E F G H I J K L M N O P Q R S T U V W Y Z) do (
  if exist "%%D:\Windows\System32\config\SYSTEM" if exist "%%D:\Users\" call :addsrc %%D
)

if "%COUNT%"=="0" (
  echo [ERROR] No Windows installation found.
  echo         If the disk is BitLocker-encrypted, unlock it first.
  goto end
)

set "SRC="
if "%COUNT%"=="1" (
  set "SRC=%SRC1%"
  goto gotsrc
)

:asksrc
set "N="
set "SRC="
set /p "N=Choose Windows number [1-%COUNT%]: "
if not defined N goto asksrc
call set "SRC=%%SRC%N%%%"
if not defined SRC goto asksrc
if not exist "%SRC%:\Users\" goto asksrc

:gotsrc
echo.
echo Source: %SRC%:\Users
echo.

rem ---------- 2. Ask for destination drive ----------
echo Available drives:
wmic logicaldisk get Caption,VolumeName,FileSystem,Size,FreeSpace 2>nul
echo.

:askdest
set "DEST="
set /p "DEST=Enter destination (external) drive letter, e.g. E : "
if not defined DEST goto askdest
set "DEST=%DEST::=%"
set "DEST=%DEST:\=%"
set "DEST=%DEST: =%"
if not defined DEST goto askdest
if not "%DEST:~1%"=="" (echo   Enter a single letter only. & goto askdest)
if not exist "%DEST%:\" (echo   Drive %DEST%: not found. & goto askdest)
if /i "%DEST%"=="%SRC%" (echo   Destination cannot be the source drive. & goto askdest)
if /i "%DEST%"=="X" (echo   X: is the RAM drive of WinPE - choose another. & goto askdest)

rem ---------- 3. Build backup folder name ----------
set "LDT="
for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value 2^>nul ^| find "="') do set "LDT=%%a"
if defined LDT (set "STAMP=%LDT:~0,8%_%LDT:~8,4%") else (set "STAMP=%RANDOM%")
set "ROOT=%DEST%:\Backup_%STAMP%"
set "LOG=%ROOT%\backup_log.txt"
md "%ROOT%" 2>nul
if not exist "%ROOT%\" (echo [ERROR] Cannot create "%ROOT%". & goto end)

echo.
echo Backup target: %ROOT%
echo Log file     : %LOG%
echo.

rem ---------- 4. Process users ----------
set "DONE=0"
set "ERRS=0"
for /d %%U in ("%SRC%:\Users\*") do call :user "%%~nxU"

echo.
echo ==============================================
echo  Finished. Users backed up: %DONE%
if not "%ERRS%"=="0" echo  Folders with copy errors: %ERRS%  (see log)
echo  Location: %ROOT%
echo ==============================================
goto end


rem ================= subroutines =================

:addsrc
set /a COUNT+=1
set "SRC%COUNT%=%1"
echo   [%COUNT%] %1:\  - profiles:
for /d %%P in ("%1:\Users\*") do echo         %%~nxP
echo.
goto :eof

:user
set "NAME=%~1"
set "UP=%SRC%:\Users\%NAME%"

echo ;%SKIP%;| find /i ";%NAME%;" >nul
if not errorlevel 1 (
  echo  - Skip   : "%NAME%"  [system profile]
  goto :eof
)

set "HAS="
for %%F in (%FOLDERS%) do if not defined HAS call :hasfiles "%UP%\%%F"
for %%F in (Desktop Documents Pictures) do if not defined HAS call :hasfiles "%UP%\OneDrive\%%F"
if not defined HAS (
  echo  - Skip   : "%NAME%"  [no files]
  goto :eof
)

echo.
echo  + Backup : "%NAME%"
for %%F in (%FOLDERS%) do call :copy "%UP%\%%F" "%ROOT%\%NAME%\%%F" "%%F"
rem Folders redirected to OneDrive (only files that exist locally)
for %%F in (Desktop Documents Pictures) do call :copy "%UP%\OneDrive\%%F" "%ROOT%\%NAME%\OneDrive_%%F" "OneDrive\%%F"
set /a DONE+=1
goto :eof

:hasfiles
if not exist "%~1\" goto :eof
for /f "delims=" %%x in ('dir /s /b /a-d "%~1" 2^>nul ^| findstr /v /i /e "desktop.ini"') do (
  set "HAS=1"
  goto :eof
)
goto :eof

:copy
if not exist "%~1\" goto :eof
echo      %~3 ...
robocopy "%~1" "%~2" /E /XJ /R:1 /W:1 /COPY:DAT /DCOPY:T /MT:8 /NP /NDL /XF desktop.ini /UNILOG+:"%LOG%" >nul
if errorlevel 8 (
  set /a ERRS+=1
  echo      !! errors while copying %~3  - see log
)
goto :eof

:end
echo.
pause
endlocal
