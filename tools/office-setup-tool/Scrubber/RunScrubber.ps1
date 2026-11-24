# Runs Office Scrubber in this console window with QuickEdit turned off,
# so a mouse click cannot freeze the removal ("Select" mode).
# usage: powershell -NoProfile -ExecutionPolicy Bypass -File RunScrubber.ps1 [/A] [/Full]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ScrubArgs)

$t = [AppDomain]::CurrentDomain.DefineDynamicAssembly(4, 1).DefineDynamicModule(2, $False).DefineType(0)
$t.DefinePInvokeMethod('GetStdHandle', 'kernel32.dll', 22, 1, [IntPtr], @([Int32]), 1, 3).SetImplementationFlags(128)
$t.DefinePInvokeMethod('GetConsoleMode', 'kernel32.dll', 22, 1, [Boolean], @([IntPtr], [Int32].MakeByRefType()), 1, 3).SetImplementationFlags(128)
$t.DefinePInvokeMethod('SetConsoleMode', 'kernel32.dll', 22, 1, [Boolean], @([IntPtr], [Int32]), 1, 3).SetImplementationFlags(128)
$k = $t.CreateType()

$h = $k::GetStdHandle(-10)
$mode = 0
$ok = $k::GetConsoleMode($h, [ref]$mode)
if ($ok) {
    # clear ENABLE_QUICK_EDIT_MODE (0x40), keep ENABLE_EXTENDED_FLAGS (0x80)
    [void]$k::SetConsoleMode($h, (($mode -band (-bnot 0x40)) -bor 0x80))
}

$env:OST_SCRUB = Join-Path $PSScriptRoot 'OfficeScrubber.cmd'
$env:OST_ARGS = ($ScrubArgs -join ' ')
& $env:ComSpec /c 'call "%OST_SCRUB%" %OST_ARGS% -qedit'
$rc = $LASTEXITCODE

if ($ok) { [void]$k::SetConsoleMode($h, $mode) }
exit $rc
