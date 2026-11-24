#!/usr/bin/env python3
"""
Builds "Office Scrubber Fast" from the original Office Scrubber v14 package.

usage: python3 patch.py <original_extracted_dir> <output_dir>

Every replacement is checked to apply exactly the expected number of times,
so a different upstream version fails loudly instead of being half-patched.
"""
import os
import re
import shutil
import sys

VBS_FILES = ["OffScrub03.vbs", "OffScrub07.vbs", "OffScrub10.vbs",
             "OffScrub_O15msi.vbs", "OffScrub_O16msi.vbs", "OffScrubC2R.vbs"]
MSI_FILES = ["OffScrub03.vbs", "OffScrub07.vbs", "OffScrub10.vbs",
             "OffScrub_O15msi.vbs", "OffScrub_O16msi.vbs"]


def sub_exact(text, pattern, repl, count, what, flags=0):
    new, n = re.subn(pattern, repl, text, flags=flags)
    if n != count:
        sys.exit(f"patch failed: {what}: expected {count} match(es), got {n}")
    return new


def rep_exact(text, old, new, what, count=1):
    n = text.count(old)
    if n != count:
        sys.exit(f"patch failed: {what}: expected {count} match(es), got {n}")
    return text.replace(old, new)


# ---------------------------------------------------------------- VBS
FAST_SHORTCUTS = r"""
'-------------------------------------------------------------------------------
'   CleanShortcutsFast  [Office Scrubber Fast]
'
'   The original code walked EVERY file under every user profile looking for
'   .lnk files. Shortcuts only live in a few known places, so scan only those:
'   Desktop, Start Menu and Quick Launch / pinned taskbar of every profile,
'   the all-users Start Menu / Desktop, OneDrive desktops and the current
'   user's (possibly redirected) shell folders.
'-------------------------------------------------------------------------------
Sub CleanShortcutsFast (fDelete, fUnPin)
    Dim dicScFld, oPrf, oSub, sRel, sFld, arrNew, arrOld, arrUse, arrSpecial

    On Error Resume Next
    Set dicScFld = CreateObject("Scripting.Dictionary")
    dicScFld.CompareMode = 1
    arrNew = Array("Desktop", "Microsoft\Windows\Start Menu", _
                   "AppData\Roaming\Microsoft\Windows\Start Menu", _
                   "AppData\Roaming\Microsoft\Internet Explorer\Quick Launch")
    arrOld = Array("Desktop", "Start Menu", _
                   "Application Data\Microsoft\Internet Explorer\Quick Launch")

    If oFso.FolderExists(sAllUsersProfile & "\Microsoft\Windows\Start Menu") Then arrUse = arrNew Else arrUse = arrOld
    For Each sRel In arrUse
        CleanShortcutsAdd dicScFld, sAllUsersProfile & "\" & sRel
    Next

    If oFso.FolderExists(sProfilesDirectory) Then
        For Each oPrf In oFso.GetFolder(sProfilesDirectory).SubFolders
            ' skip junctions such as "All Users" / "Default User"
            If NOT CBool(oPrf.Attributes AND 1024) Then
                If oFso.FolderExists(oPrf.Path & "\AppData") Then arrUse = arrNew Else arrUse = arrOld
                For Each sRel In arrUse
                    CleanShortcutsAdd dicScFld, oPrf.Path & "\" & sRel
                Next
                For Each oSub In oPrf.SubFolders
                    If LCase(Left(oSub.Name, 8)) = "onedrive" Then CleanShortcutsAdd dicScFld, oSub.Path & "\Desktop"
                Next
            End If
        Next
    End If

    arrSpecial = Array("Desktop", "AllUsersDesktop", "StartMenu", "AllUsersStartMenu")
    For Each sRel In arrSpecial
        CleanShortcutsAdd dicScFld, oWShell.SpecialFolders(sRel)
    Next
    Err.Clear
    On Error Goto 0

    For Each sFld In dicScFld.Keys
        CleanShortcuts sFld, fDelete, fUnPin
    Next
End Sub 'CleanShortcutsFast

Sub CleanShortcutsAdd (dicScFld, sFld)
    If IsEmpty(sFld) Or IsNull(sFld) Then Exit Sub
    If Len(sFld) = 0 Then Exit Sub
    If NOT oFso.FolderExists(sFld) Then Exit Sub
    If NOT dicScFld.Exists(sFld) Then dicScFld.Add sFld, 0
End Sub 'CleanShortcutsAdd
'=======================================================================================================
"""

FAST_INSCOPE = r"""
'-------------------------------------------------------------------------------
'   HasInScopeProducts  [Office Scrubber Fast]
'
'   ScanComponents walks every Windows Installer component on the machine.
'   It can only find something when a registered product is in scope, so
'   when none is registered the (very slow) scan is skipped.
'-------------------------------------------------------------------------------
Function HasInScopeProducts ()
    Dim prod
    HasInScopeProducts = False
    On Error Resume Next
    For Each prod In oMsi.Products
        If InScope(prod) Then
            HasInScopeProducts = True
            Exit Function
        End If
    Next
    If Err <> 0 Then HasInScopeProducts = True ' API problem: keep original behavior
    Err.Clear
End Function 'HasInScopeProducts
'=======================================================================================================
"""

SCAN_BLOCK = (
    "    If HasInScopeProducts() Then\r\n"
    "        ScanComponents\r\n"
    "    Else\r\n"
    "        Log \"No in-scope products registered. Component Detection skipped [Fast].\"\r\n"
    "    End If\r\n"
)


def patch_vbs(name, text):
    # 1. shortcut scanning: whole profile tree -> known shortcut folders
    text = sub_exact(
        text,
        r"([ \t]*)CleanShortcuts sAllUsersProfile, (True|False), (True|False)\r\n"
        r"[ \t]*CleanShortcuts sProfilesDirectory, \2, \3\r\n",
        r"\1CleanShortcutsFast \2, \3\r\n",
        2, f"{name}: shortcut calls", flags=re.IGNORECASE)

    # 2. MSI scrubbers: skip component scan when nothing is in scope
    if name in MSI_FILES:
        text = sub_exact(text, r"\r\n    ScanComponents ?\r\n", "\r\n" + SCAN_BLOCK,
                         1, f"{name}: ScanComponents call")

    # helpers go right before the original CleanShortcuts sub header comment
    helpers = FAST_SHORTCUTS + (FAST_INSCOPE if name in MSI_FILES else "")
    helpers = helpers.replace("\r\n", "\n").replace("\n", "\r\n")
    text = sub_exact(text, r"(\r\n)(Sub CleanShortcuts \()",
                     lambda m: m.group(1) + helpers + m.group(2),
                     1, f"{name}: helper insertion")
    return text


# ---------------------------------------------------------------- CMD
def patch_cmd(text):
    t = text

    t = rep_exact(t, "@set uivr=v14\r\n", "@set uivr=v14 Fast\r\n", "version")
    t = rep_exact(t, "for %%# in (All,C2R,UWP,M16,M15,M14,M12,M11) do set _u%%#=0",
                  "for %%# in (All,C2R,UWP,M16,M15,M14,M12,M11,Full) do set _u%%#=0", "init flags")
    t = rep_exact(t, 'if /i "%%~#"=="/M1" set _uM11=1&set Unattend=1\r\n',
                  'if /i "%%~#"=="/M1" set _uM11=1&set Unattend=1\r\n'
                  'if /i "%%~#"=="/Full" set _uFull=1\r\n', "/Full switch")
    # pass -qedit relaunch arguments through unchanged (they already are: %*)

    # Scrub ALL: only detected versions unless /Full (original behavior)
    t = rep_exact(t,
                  "if %winbuild% GEQ 7600 set tOCTR=1\r\n"
                  "if %winbuild% GEQ 7600 set tOM16=1\r\n"
                  "if %winbuild% LSS 7600 set tOM14=1\r\n",
                  "if %_uFull% EQU 1 if %winbuild% GEQ 7600 set tOCTR=1\r\n"
                  "if %_uFull% EQU 1 if %winbuild% GEQ 7600 set tOM16=1\r\n"
                  "if %_uFull% EQU 1 if %winbuild% LSS 7600 set tOM14=1\r\n",
                  "unattended scrub-all defaults")
    t = rep_exact(t,
                  'if "%_er%"=="1" set tOCTR=1&set tOM16=1&goto :mALL\r\n',
                  'if "%_er%"=="1" (if %_uFull% EQU 1 set tOCTR=1&set tOM16=1)&goto :mALL\r\n',
                  "menu scrub-all defaults (new)")
    t = rep_exact(t,
                  'if "%_er%"=="1" set tOM14=1&goto :mALL\r\n',
                  'if "%_er%"=="1" (if %_uFull% EQU 1 set tOM14=1)&goto :mALL\r\n',
                  "menu scrub-all defaults (legacy)")
    t = rep_exact(t,
                  "echo Notice:\r\n"
                  "echo Manually selecting all is not necessary and will take a long time.\r\n",
                  "echo Notice:\r\n"
                  "echo Fast mode: only detected versions {*} are preselected.\r\n"
                  "echo Toggle a version with its number to force it anyway.\r\n",
                  "scrub-all notice")

    # Product keys: one combined WMI query instead of one per version,
    # and do it only once per run.
    t = rep_exact(t, "for %%A in (16,19,21,24) do call :cKpp %%A\r\nif %_O15CTR% EQU 1 call :cKpp 15\r\n",
                  "if %_O15CTR% EQU 1 (call :cKpp 15 16 19 21 24) else (call :cKpp 16 19 21 24)\r\n", "rOCTR keys")
    t = rep_exact(t, "for %%A in (15,16,19,21,24) do call :cKpp %%A\r\n",
                  "call :cKpp 15 16 19 21 24\r\n", "KeysU keys")
    t = rep_exact(t, "for %%A in (15,16,19,21,24) do call :cKEY %%A\r\n",
                  "call :cKEY 15 16 19 21 24\r\n", "cKMS SPP keys")
    t = rep_exact(t, "for %%A in (14,15,16,19,21,24) do call :cKEY %%A\r\n",
                  "call :cKEY 14 15 16 19 21 24\r\n", "cKMS OSPP keys")
    t = rep_exact(t,
                  "reg.exe delete \"HKU\\S-1-5-20\\%OPPk%\" /f\r\ngoto :eof\r\n",
                  "reg.exe delete \"HKU\\S-1-5-20\\%OPPk%\" /f\r\nset _keysDone=1\r\ngoto :eof\r\n",
                  "cKMS done flag")
    t = rep_exact(t,
                  ":cK14\r\nset _spp=OfficeSoftwareProtectionProduct\r\n",
                  ":cK14\r\nif defined _keysDone goto :eof\r\nset _spp=OfficeSoftwareProtectionProduct\r\n",
                  "cK14 skip")
    t = rep_exact(t,
                  ":cKpp\r\nset _spp=SoftwareLicensingProduct\r\nif %winbuild% GEQ 9200 (\r\ncall :cKEY %1\r\n)\r\n"
                  "set _spp=OfficeSoftwareProtectionProduct\r\nif %winbuild% LSS 9200 if %OsppHook% NEQ 0 (\r\ncall :cKEY %1\r\n)\r\n",
                  ":cKpp\r\nif defined _keysDone goto :eof\r\nset _spp=SoftwareLicensingProduct\r\nif %winbuild% GEQ 9200 (\r\ncall :cKEY %*\r\n)\r\n"
                  "set _spp=OfficeSoftwareProtectionProduct\r\nif %winbuild% LSS 9200 if %OsppHook% NEQ 0 (\r\ncall :cKEY %*\r\n)\r\n",
                  "cKpp multi")
    t = rep_exact(t,
                  ":cKEY\r\nset \"_ocq=Name LIKE 'Office %~1%%' AND PartialProductKey is not NULL\"\r\n",
                  ":cKEY\r\n"
                  ":: [Fast] one WMI query for all requested versions\r\n"
                  "set \"_ocq=\"\r\n"
                  "for %%A in (%*) do (\r\n"
                  "if defined _ocq (set \"_ocq=!_ocq! OR Name LIKE 'Office %%A%%' AND PartialProductKey is not NULL\") else (set \"_ocq=Name LIKE 'Office %%A%%' AND PartialProductKey is not NULL\")\r\n"
                  ")\r\n",
                  "cKEY combined query")

    # CloseC2R: one process snapshot instead of 32 tasklist+find pairs
    t = rep_exact(t,
                  ":CloseC2R\r\nnet stop OfficeSvc /y\r\nnet stop ClickToRunSvc /y\r\nfor %%# in (\r\n",
                  ":CloseC2R\r\nnet stop OfficeSvc /y\r\nnet stop ClickToRunSvc /y\r\n"
                  "set \"_tl=%TEMP%\\OfficeScrubber_tasks.txt\"\r\n"
                  "tasklist /fo csv /nh >\"%_tl%\"\r\n"
                  "for %%# in (\r\n",
                  "CloseC2R snapshot")
    t = rep_exact(t,
                  'tasklist /FI "IMAGENAME eq %%#.exe" | find /i "%%#.exe" && taskkill /t /f /IM %%#.exe\r\n)\r\n',
                  'findstr /i /b /l /c:"\\"%%#.exe\\"" "%_tl%" && taskkill /t /f /IM %%#.exe\r\n)\r\n'
                  'del /f /q "%_tl%"\r\n',
                  "CloseC2R kill loop")
    # C2R uninstall: silent and can hang -> wait at most 15 minutes, then
    # continue (OffScrubC2R.vbs removes whatever is left anyway).
    for var in ("_file", "_fil2"):
        t = rep_exact(t,
                      '%_Nul3% start "" /WAIT "!' + var + '!" platform=%_plat% productstoremove=AllProducts displaylevel=False\r\n',
                      'call :RunC2R "!' + var + '!"\r\n',
                      "C2R uninstall " + var)
    t = rep_exact(t, "\r\n:CloseC2R\r\nnet stop OfficeSvc /y\r\n",
                  "\r\n:RunC2R\r\n"
                  "echo [Fast] Silent Microsoft uninstall - can take up to 15 minutes, please wait.\r\n"
                  "if %_pwsh% EQU 0 (\r\n"
                  "%_Nul3% start \"\" /WAIT \"%~1\" platform=%_plat% productstoremove=AllProducts displaylevel=False\r\n"
                  "goto :eof\r\n"
                  ")\r\n"
                  "set \"_c2rexe=%~1\"\r\n"
                  "set \"_c2rarg=platform=%_plat% productstoremove=AllProducts displaylevel=False\"\r\n"
                  "%_Nul3% %_psc% \"$p=Start-Process -FilePath $env:_c2rexe -ArgumentList $env:_c2rarg -PassThru; "
                  "if (-not $p.WaitForExit(900000)) { try { $p.Kill() } catch {} }\"\r\n"
                  "goto :eof\r\n"
                  "\r\n"
                  ":CloseC2R\r\nnet stop OfficeSvc /y\r\n",
                  "RunC2R label")
    return t


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    for name in VBS_FILES:
        p = os.path.join(dst, "bin", name)
        raw = open(p, "rb").read()
        assert raw[:2] == b"\xff\xfe", f"{name}: expected UTF-16LE BOM"
        text = raw[2:].decode("utf-16-le")
        text = patch_vbs(name, text)
        open(p, "wb").write(b"\xff\xfe" + text.encode("utf-16-le"))
        print("patched", name)

    p = os.path.join(dst, "OfficeScrubber.cmd")
    text = open(p, "rb").read().decode("ascii")
    open(p, "wb").write(patch_cmd(text).encode("ascii"))
    print("patched OfficeScrubber.cmd")


if __name__ == "__main__":
    main()
