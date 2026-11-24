@echo off
:: ============================================================================
::  Office Setup Tool
::  - Removal : Office Scrubber v14 Fast  (folder "Scrubber" next to this file)
::  - Install : Microsoft Office Deployment Tool (setup.exe) downloaded from
::              officecdn.microsoft.com, Office files from Microsoft's CDN.
::  No activation tricks. Install, then sign in or enter your own product key.
:: ============================================================================
setlocal EnableExtensions DisableDelayedExpansion
set "BASE=%~dp0"
set "SELF=%~f0"

:: run under 64-bit cmd on 64-bit Windows
if not exist "%SystemRoot%\Sysnative\cmd.exe" goto :Native
"%SystemRoot%\Sysnative\cmd.exe" /c ""%SELF%" %*"
exit /b
:Native

:: administrator rights
reg query "HKU\S-1-5-19" >nul 2>&1 && goto :Admin
if /i "%~1"=="-elevated" (
    echo Administrator rights are required.
    echo Right-click the file and choose "Run as administrator".
    pause
    exit /b 1
)
set "_ps=%SELF:'=''%"
powershell -nop -c "Start-Process -FilePath $env:ComSpec -ArgumentList ('/c \"\"' + '%_ps%' + '\" -elevated\"') -Verb RunAs" >nul 2>&1 || (
    echo Administrator rights are required.
    echo Right-click the file and choose "Run as administrator".
    pause
)
exit /b

:Admin
cd /d "%BASE%"
setlocal EnableDelayedExpansion
color 1F
title Office Setup Tool
set "ODTDIR=%BASE%ODT"
set "ODT=%BASE%ODT\setup.exe"
set "FILES=%BASE%Office_Files"
set "SCRUB=%BASE%Scrubber\OfficeScrubber.cmd"
set "TMPW=%TEMP%\OfficeSetupTool"
if not exist "%TMPW%" md "%TMPW%"
set "OSBIT=64"
if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 set "OSBIT=32"
set "APPS=Word Excel PowerPoint Outlook OutlookForWindows Access Publisher OneNote OneDrive Teams Lync Groove Visio Project"
set "K5=[0-9A-Za-z][0-9A-Za-z][0-9A-Za-z][0-9A-Za-z][0-9A-Za-z]"
call :Defaults

:: ============================================================================
:Main
call :Hdr
echo   [1] Remove Office completely            (fast - detected versions)
echo   [2] Install Office from Microsoft       (online)
echo   [3] Clean reinstall                     (remove, then install from Microsoft)
echo   [4] Download Office for offline install
echo   [5] Install Office from downloaded files (offline)
echo   [6] License: enter product key / status / sign in
echo   [7] Advanced removal                    (deep scrub / Office Scrubber menu)
echo   [0] Exit
echo.
choice /c 12345670 /n /m "Choose an option: "
set "_e=%errorlevel%"
if "%_e%"=="1" goto :ActRemove
if "%_e%"=="2" goto :ActInstall
if "%_e%"=="3" goto :ActReinstall
if "%_e%"=="4" goto :ActDownload
if "%_e%"=="5" goto :ActOffline
if "%_e%"=="6" goto :ActLicense
if "%_e%"=="7" goto :ActAdvanced
exit /b 0

:: ============================================================================
:ActRemove
call :Hdr
echo   All detected Office versions will be removed, including their licenses.
echo   Save your work and close Office programs first.
echo.
choice /c YN /m "  Continue"
if errorlevel 2 goto :Main
call :Scrub /A
call :Hdr
echo   Removal finished. A restart is recommended before installing again.
call :Pause
goto :Main

:ActAdvanced
call :Hdr
echo   [1] Deep removal - also scrub Office C2R and 2016 even if not detected (slow)
echo   [2] Open the Office Scrubber menu (choose versions, licenses, keys)
echo   [0] Back
echo.
choice /c 120 /n /m "Choose an option: "
set "_e=%errorlevel%"
if "%_e%"=="3" goto :Main
if "%_e%"=="1" (
    call :Scrub /A /Full
) else (
    call :Scrub
)
call :Pause
goto :Main

:ActInstall
call :Wizard key || goto :Main
call :Install online
call :Pause
goto :Main

:ActReinstall
call :Wizard key || goto :Main
call :Hdr
echo   Step 1: all detected Office versions will be REMOVED.
echo   Step 2: !P_NAME! will be installed from Microsoft.
echo.
choice /c YN /m "  Continue"
if errorlevel 2 goto :Main
:: get the installer first, so nothing is removed if the download fails
call :GetODT || (call :Pause & goto :Main)
call :Scrub /A
call :Install online
call :Pause
goto :Main

:ActDownload
call :Wizard nokey || goto :Main
call :GetODT || (call :Pause & goto :Main)
set "_ln=!LANGS: =_!"
set "DL=%FILES%\!P_ID!_x!BIT!!_ln:~0,-1!"
if not exist "!DL!" md "!DL!"
set "_keep=!PIDKEY!"
set "PIDKEY="
call :MakeXml "!DL!\config.xml" "!DL!"
set "PIDKEY=!_keep!"
call :Hdr
echo   Downloading !P_NAME! !BIT!-bit to:
echo   !DL!
echo   This can take a while (about 3-5 GB).
echo.
"%ODT%" /download "!DL!\config.xml"
set "_rc=!errorlevel!"
if exist "!DL!\Office\Data" (
    echo   Download finished ^(code !_rc!^). Use option 5 to install from these files.
) else (
    echo   ERROR: the download did not complete ^(code !_rc!^).
)
call :Pause
goto :Main

:ActOffline
call :Hdr
echo   Downloaded Office packages:
echo.
set "_c=0"
if exist "!FILES!" for /d %%D in ("!FILES!\*") do if exist "%%D\config.xml" if exist "%%D\Office\Data" (
    set /a _c+=1
    set "_d!_c!=%%D"
    echo   [!_c!] %%~nxD
)
if "!_c!"=="0" (
    echo   None found in "!FILES!".
    echo   Use option 4 to download Office first.
    call :Pause
    goto :Main
)
echo.
set "_in="
set /p "_in=  Select a number (Enter = back): "
if not defined _in goto :Main
call :IsNum "!_in!" || goto :ActOffline
if !_in! lss 1 goto :ActOffline
if !_in! gtr !_c! goto :ActOffline
for %%n in (!_in!) do set "DL=!_d%%n!"
set "_cfg=!DL!\config.xml"
set "_src=!DL!"
set "_out=%TMPW%\config-offline.xml"
if exist "!_out!" del /f /q "!_out!"
powershell -nop -ep bypass -c "$x=[xml](Get-Content -LiteralPath $env:_cfg -Raw); $x.Configuration.Add.SetAttribute('SourcePath',$env:_src); $x.Save($env:_out)" >nul 2>&1
if not exist "!_out!" (
    echo   ERROR: cannot read "!_cfg!".
    call :Pause
    goto :Main
)
call :Install "!_out!"
call :Pause
goto :Main

:ActLicense
set "OSPP="
for %%P in ("%ProgramFiles%\Microsoft Office\Office16\OSPP.VBS" "%ProgramFiles(x86)%\Microsoft Office\Office16\OSPP.VBS") do if not defined OSPP if exist "%%~P" set "OSPP=%%~P"
call :Hdr
if not defined OSPP (
    echo   Office 2016 or later is not installed.
    call :Pause
    goto :Main
)
echo   [1] Enter my product key and activate
echo   [2] Show license status
echo   [3] Open Word - sign in with the Microsoft account that owns the license
echo   [0] Back
echo.
choice /c 1230 /n /m "Choose an option: "
set "_e=%errorlevel%"
if "%_e%"=="4" goto :Main
if "%_e%"=="2" (
    cscript //nologo "!OSPP!" /dstatus
    call :Pause
    goto :ActLicense
)
if "%_e%"=="3" (
    start "" winword.exe
    goto :ActLicense
)
echo.
set "_key="
set /p "_key=  Product key (XXXXX-XXXXX-XXXXX-XXXXX-XXXXX): "
if not defined _key goto :ActLicense
call :IsKey "!_key!" || (
    echo   Invalid key format.
    call :Pause
    goto :ActLicense
)
cscript //nologo "!OSPP!" /inpkey:!_key!
cscript //nologo "!OSPP!" /act
call :Pause
goto :ActLicense

:: ============================================================================
::  Wizard: product, bitness, languages, apps, optional key, summary
::  %1 = key | nokey.  Returns errorlevel 1 when the user goes back.
:: ============================================================================
:Wizard
set "_wk=%~1"
call :MenuProduct
call :MenuBit
call :MenuLang
call :MenuApps
if /i "!_wk!"=="key" call :MenuKey
:WizSummary
call :Hdr
set "_inc="
for %%A in (%APPS%) do if "!A_%%A!"=="1" set "_inc=!_inc! %%A"
echo   Product   : !P_NAME!
echo   ID        : !P_ID!   channel: !P_CH!
echo   Bitness   : !BIT!-bit
echo   Languages :!LANGS!  (first = main language)
echo   Apps      :!_inc!
if /i "!_wk!"=="key" (
    if defined PIDKEY (echo   Key       : *****-*****-*****-*****-!PIDKEY:~-5!) else (echo   Key       : none - activate after installation)
)
echo.
echo   [1] Continue   [2] Change settings   [0] Back to main menu
echo.
choice /c 120 /n /m "Choose an option: "
if errorlevel 3 exit /b 1
if errorlevel 2 goto :Wizard
exit /b 0

:MenuProduct
call :Hdr
echo   Select the Office product you have a license for:
echo.
for /l %%i in (1,1,11) do (
    call :SetProduct %%i
    if %%i lss 10 (echo   [%%i]  !P_NAME!) else (echo   [%%i] !P_NAME!)
)
call :SetProduct !P_NUM!
echo.
echo   Current: [!P_NUM!] !P_NAME!
set "_in="
set /p "_in=  Product number (Enter = keep current): "
if not defined _in goto :eof
call :IsNum "!_in!" || goto :MenuProduct
if !_in! lss 1 goto :MenuProduct
if !_in! gtr 11 goto :MenuProduct
set "P_NUM=!_in!"
call :SetProduct !P_NUM!
goto :eof

:SetProduct
if "%~1"=="1"  set "P_ID=O365ProPlusRetail"&      set "P_NAME=Microsoft 365 Apps for enterprise"&                    set "P_FAM=365"
if "%~1"=="2"  set "P_ID=O365BusinessRetail"&     set "P_NAME=Microsoft 365 Apps for business"&                      set "P_FAM=365"
if "%~1"=="3"  set "P_ID=O365HomePremRetail"&     set "P_NAME=Microsoft 365 Family / Personal"&                      set "P_FAM=365"
if "%~1"=="4"  set "P_ID=Home2024Retail"&         set "P_NAME=Office Home 2024"&                                     set "P_FAM=R24"
if "%~1"=="5"  set "P_ID=HomeBusiness2024Retail"& set "P_NAME=Office Home and Business 2024"&                        set "P_FAM=R24"
if "%~1"=="6"  set "P_ID=ProPlus2024Retail"&      set "P_NAME=Office Professional Plus 2024 - retail"&               set "P_FAM=R24"
if "%~1"=="7"  set "P_ID=ProPlus2024Volume"&      set "P_NAME=Office LTSC Professional Plus 2024 - volume license"&  set "P_FAM=V24"
if "%~1"=="8"  set "P_ID=Standard2024Volume"&     set "P_NAME=Office LTSC Standard 2024 - volume license"&           set "P_FAM=V24"
if "%~1"=="9"  set "P_ID=HomeBusiness2021Retail"& set "P_NAME=Office Home and Business 2021 - support ended"&        set "P_FAM=R21"
if "%~1"=="10" set "P_ID=ProPlus2021Retail"&      set "P_NAME=Office Professional Plus 2021 - support ended"&        set "P_FAM=R21"
if "%~1"=="11" set "P_ID=ProPlus2021Volume"&      set "P_NAME=Office LTSC Professional Plus 2021 - support ended"&   set "P_FAM=V21"
if "%P_FAM%"=="365" set "P_CH=Current"&         set "P_VISIO=VisioProRetail"&     set "P_PROJECT=ProjectProRetail"
if "%P_FAM%"=="R24" set "P_CH=Current"&         set "P_VISIO=VisioPro2024Retail"& set "P_PROJECT=ProjectPro2024Retail"
if "%P_FAM%"=="V24" set "P_CH=PerpetualVL2024"& set "P_VISIO=VisioPro2024Volume"& set "P_PROJECT=ProjectPro2024Volume"
if "%P_FAM%"=="R21" set "P_CH=Current"&         set "P_VISIO=VisioPro2021Retail"& set "P_PROJECT=ProjectPro2021Retail"
if "%P_FAM%"=="V21" set "P_CH=PerpetualVL2021"& set "P_VISIO=VisioPro2021Volume"& set "P_PROJECT=ProjectPro2021Volume"
goto :eof

:MenuBit
if "%OSBIT%"=="32" (
    set "BIT=32"
    goto :eof
)
call :Hdr
echo   [1] 64-bit (recommended)
echo   [2] 32-bit (only for old 32-bit add-ins)
echo.
choice /c 12 /n /m "Choose an option: "
if errorlevel 2 (set "BIT=32") else (set "BIT=64")
goto :eof

:MenuLang
call :Hdr
echo   Languages - press a number to add/remove. The first one is the main language.
echo   Selected:!LANGS!
echo.
set "_i=0"
for %%L in ("he-il:Hebrew" "en-us:English" "ar-sa:Arabic" "ru-ru:Russian" "fr-fr:French" "es-es:Spanish" "de-de:German" "MatchOS:Same as Windows") do (
    for /f "tokens=1,2 delims=:" %%a in ("%%~L") do (
        set /a _i+=1
        set "_lc!_i!=%%a"
        set "_m=[ ]"
        if not "!LANGS: %%a =!"=="!LANGS!" set "_m=[x]"
        echo   [!_i!] !_m! %%b
    )
)
echo   [C] Continue
echo.
choice /c 12345678C /n /m "Choose an option: "
set "_e=%errorlevel%"
if "%_e%"=="9" (
    if "!LANGS!"==" " (
        echo   Select at least one language.
        timeout /t 2 >nul
        goto :MenuLang
    )
    goto :eof
)
set "_code=!_lc%_e%!"
if not "!LANGS: %_code% =!"=="!LANGS!" (
    set "LANGS=!LANGS: %_code% = !"
) else (
    set "LANGS=!LANGS!%_code% "
)
goto :MenuLang

:MenuApps
call :Hdr
echo   Apps to install - press a letter to switch on/off:
echo.
call :AppLine W Word              "Word"
call :AppLine E Excel             "Excel"
call :AppLine P PowerPoint        "PowerPoint"
call :AppLine L Outlook           "Outlook (classic)"
call :AppLine O OutlookForWindows "Outlook (new)"
call :AppLine A Access            "Access"
call :AppLine U Publisher         "Publisher (retired by Microsoft in 2026)"
call :AppLine N OneNote           "OneNote"
call :AppLine D OneDrive          "OneDrive"
call :AppLine T Teams             "Teams"
call :AppLine S Lync              "Skype for Business"
call :AppLine G Groove            "OneDrive for Business (old sync client)"
call :AppLine V Visio             "Visio      (needs its own license)"
call :AppLine R Project           "Project    (needs its own license)"
echo.
echo   [F] All Office apps   [M] Word, Excel, PowerPoint, Outlook only   [C] Continue
echo.
choice /c WEPLOAUNDTSGVRFMC /n /m "Choose an option: "
set "_e=%errorlevel%"
if "%_e%"=="17" (
    set "_any="
    for %%A in (%APPS%) do if "!A_%%A!"=="1" set "_any=1"
    if not defined _any (
        echo   Select at least one app.
        timeout /t 2 >nul
        goto :MenuApps
    )
    goto :eof
)
if "%_e%"=="15" (
    for %%A in (%APPS%) do set "A_%%A=1"
    set "A_Visio=0"
    set "A_Project=0"
    goto :MenuApps
)
if "%_e%"=="16" (
    for %%A in (%APPS%) do set "A_%%A=0"
    for %%A in (Word Excel PowerPoint Outlook) do set "A_%%A=1"
    goto :MenuApps
)
set "_k=0"
for %%A in (%APPS%) do (
    set /a _k+=1
    if "!_k!"=="%_e%" (
        if "!A_%%A!"=="1" (set "A_%%A=0") else (set "A_%%A=1")
    )
)
goto :MenuApps

:AppLine
set "_m=[ ]"
if "!A_%~2!"=="1" set "_m=[x]"
echo   [%~1] !_m! %~3
goto :eof

:MenuKey
call :Hdr
echo   Optional: enter your product key now, or press Enter to skip
echo   and activate after installation (sign in, or the License menu).
echo.
set "PIDKEY="
set /p "PIDKEY=  Product key: "
if not defined PIDKEY goto :eof
call :IsKey "!PIDKEY!" && goto :eof
echo   Invalid key format.
timeout /t 2 >nul
goto :MenuKey

:: ============================================================================
:Install
::  %1 = online | full path of a configuration file
call :GetODT || exit /b 1
if /i "%~1"=="online" (
    set "CFG=%TMPW%\config-online.xml"
    call :MakeXml "!CFG!" ""
) else (
    set "CFG=%~1"
)
call :Hdr
echo   Installing Office. The Microsoft installer window will show the progress.
echo   Do not close this window.
echo.
"%ODT%" /configure "!CFG!"
set "_rc=!errorlevel!"
set "_pr="
set "_ok=0"
for /f "skip=2 tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Office\ClickToRun\Configuration" /v ProductReleaseIds 2^>nul') do set "_pr=%%b"
if defined _pr (
    if /i "%~1"=="online" (
        if not "!_pr:%P_ID%=!"=="!_pr!" set "_ok=1"
    ) else (
        set "_ok=1"
    )
)
if "!_ok!"=="0" (
    echo   ERROR: Office was not installed ^(setup code !_rc!^).
    echo   Logs: %%TEMP%% and %%WINDIR%%\Temp ^(files named like the computer name^).
    exit /b 1
)
echo   Office is installed ^(setup code !_rc!^).
echo   To activate: open Word and sign in with your Microsoft account,
echo   or use option 6 to enter your product key.
if /i "%~1"=="online" if exist "!CFG!" del /f /q "!CFG!"
exit /b 0

:MakeXml
::  %1 = output file, %2 = SourcePath (empty for online)
set "_src="
if not "%~2"=="" set "_src= SourcePath="%~2""
(
    echo ^<Configuration^>
    echo   ^<Add OfficeClientEdition="!BIT!" Channel="!P_CH!"!_src! AllowCdnFallback="TRUE"^>
    call :XmlProduct !P_ID! "!PIDKEY!"
    if "!A_Visio!"=="1" call :XmlProduct !P_VISIO! ""
    if "!A_Project!"=="1" call :XmlProduct !P_PROJECT! ""
    echo   ^</Add^>
    echo   ^<Property Name="FORCEAPPSHUTDOWN" Value="TRUE" /^>
    echo   ^<Updates Enabled="TRUE" /^>
    echo   ^<RemoveMSI /^>
    echo   ^<Display Level="Full" AcceptEULA="TRUE" /^>
    echo ^</Configuration^>
) >"%~1"
goto :eof

:XmlProduct
set "_pk="
if not "%~2"=="" set "_pk= PIDKEY="%~2""
echo     ^<Product ID="%~1"!_pk!^>
for %%L in (!LANGS!) do echo       ^<Language ID="%%L" /^>
for %%A in (Word Excel PowerPoint Outlook OutlookForWindows Access Publisher OneNote OneDrive Teams Lync Groove) do (
    if not "!A_%%A!"=="1" echo       ^<ExcludeApp ID="%%A" /^>
)
if "!P_FAM!"=="365" echo       ^<ExcludeApp ID="Bing" /^>
echo     ^</Product^>
goto :eof

:GetODT
if exist "%ODT%" (
    call :VerifySig "%ODT%" && exit /b 0
    del /f /q "%ODT%" >nul 2>&1
)
if not exist "%ODTDIR%" md "%ODTDIR%"
echo   Downloading the Office Deployment Tool from officecdn.microsoft.com ...
set "_odturl=https://officecdn.microsoft.com/pr/wsus/setup.exe"
set "_odtout=%ODT%"
powershell -nop -ep bypass -c "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile($env:_odturl, $env:_odtout)" >nul 2>&1
if not exist "%ODT%" (
    echo   ERROR: download failed. Check the internet connection.
    exit /b 1
)
call :VerifySig "%ODT%" && exit /b 0
del /f /q "%ODT%" >nul 2>&1
echo   ERROR: the downloaded file is not signed by Microsoft. It was deleted.
exit /b 1

:VerifySig
set "_vf=%~1"
powershell -nop -ep bypass -c "$s=Get-AuthenticodeSignature -LiteralPath $env:_vf; if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'O=Microsoft Corporation') { exit 0 }; exit 1" >nul 2>&1
exit /b %errorlevel%

:Scrub
if not exist "%BASE%Scrubber\RunScrubber.ps1" set "SCRUB=%BASE%Scrubber\RunScrubber.ps1"
if not exist "%SCRUB%" (
    echo   ERROR: the "Scrubber" folder is missing next to this file.
    exit /b 1
)
powershell -nop -ep bypass -File "%BASE%Scrubber\RunScrubber.ps1" %*
title Office Setup Tool
color 1F
exit /b 0

:: ============================================================================
:Defaults
set "P_NUM=1"
call :SetProduct 1
set "BIT=64"
if "%OSBIT%"=="32" set "BIT=32"
set "LANGS= he-il en-us "
for %%A in (%APPS%) do set "A_%%A=0"
for %%A in (Word Excel PowerPoint Outlook Access) do set "A_%%A=1"
set "PIDKEY="
goto :eof

:IsNum
set "_t=%~1"
if not defined _t exit /b 1
for /f "delims=0123456789" %%x in ("%~1") do exit /b 1
exit /b 0

:IsKey
set "_t=%~1"
>"%TMPW%\key.txt" echo(!_t!
findstr /r /x /c:"%K5%-%K5%-%K5%-%K5%-%K5%" "%TMPW%\key.txt" >nul 2>&1
set "_r=!errorlevel!"
del /f /q "%TMPW%\key.txt" >nul 2>&1
exit /b %_r%

:Hdr
cls
echo.
echo  ===========================================================================
echo    Office Setup Tool  -  clean removal and official installation of Office
echo  ===========================================================================
echo.
goto :eof

:Pause
echo.
echo   Press any key to continue . . .
pause >nul
goto :eof
