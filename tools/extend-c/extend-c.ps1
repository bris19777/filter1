# ExtendC - extends drive C: into all unallocated space on its disk.
# If a Windows recovery partition sits between C: and the free space, it is
# deleted and re-created (1 GB) at the end of the disk, and WinRE is re-enabled.
param(
    [ValidateSet('Analyze', 'Apply')][string]$Mode = 'Analyze',
    [Parameter(Mandatory = $true)][string]$Out,
    [Parameter(Mandatory = $true)][string]$Log
)

$ErrorActionPreference = 'Stop'
$RecoveryGuid    = '{de94bba4-06d1-4d40-a16a-bfd50179d6ac}'
$NewRecoverySize = 1GB
$WinreWim        = Join-Path $env:SystemRoot 'System32\Recovery\Winre.wim'
$ReAgentC        = Join-Path $env:SystemRoot 'System32\reagentc.exe'
$DiskPart        = Join-Path $env:SystemRoot 'System32\diskpart.exe'

function Write-Log([string]$m) {
    ('[{0:HH:mm:ss}] {1}' -f (Get-Date), $m) | Out-File -FilePath $Log -Append -Encoding utf8
}

function Finish([int]$code, [string]$msg) {
    $msg | Out-File -FilePath $Out -Encoding utf8
    Write-Log "EXIT $code"
    Write-Log $msg
    exit $code
}

function Fmt([double]$b) {
    if ($b -ge 1GB) { '{0:N2} GB' -f ($b / 1GB) } else { '{0:N0} MB' -f ($b / 1MB) }
}

function Test-Recovery($p) {
    ($p.GptType -eq $RecoveryGuid) -or ($p.MbrType -eq 39)
}

function Invoke-Tool([string]$exe, [string[]]$argList) {
    $ErrorActionPreference = 'Continue'
    Write-Log ("> {0} {1}" -f $exe, ($argList -join ' '))
    $o = & $exe @argList 2>&1 | Out-String
    $rc = $LASTEXITCODE
    Write-Log $o
    Write-Log "exit code: $rc"
    return $rc
}

function Invoke-DiskPart([string[]]$lines) {
    $f = Join-Path ([IO.Path]::GetDirectoryName($Log)) 'diskpart-script.txt'
    $lines | Set-Content -Path $f -Encoding Ascii
    Write-Log ("diskpart script:`r`n" + ($lines -join "`r`n"))
    return (Invoke-Tool $DiskPart @('/s', $f))
}

function Get-Plan {
    try { Update-HostStorageCache } catch { }
    $c     = Get-Partition -DriveLetter C
    $disk  = Get-Disk -Number $c.DiskNumber
    $parts = @(Get-Partition -DiskNumber $c.DiskNumber | Sort-Object Offset)
    $cEnd  = $c.Offset + $c.Size
    $after = @($parts | Where-Object { $_.Offset -ge $cEnd })

    Write-Log ("Disk {0}: {1}, style {2}, size {3}" -f $disk.Number, $disk.FriendlyName, $disk.PartitionStyle, (Fmt $disk.Size))
    foreach ($p in $parts) {
        Write-Log ("  #{0} offset={1} size={2} type={3} gpt={4} letter={5}" -f $p.PartitionNumber, $p.Offset, (Fmt $p.Size), $p.Type, $p.GptType, $p.DriveLetter)
    }

    $plan = [pscustomobject]@{
        Kind = ''; C = $c; Disk = $disk; Recovery = $null; Free = 0; NewCSize = 0; Message = ''
    }

    if ($after.Count -eq 0) {
        $plan.Free = $disk.Size - $cEnd
        $plan.NewCSize = $disk.Size - $c.Offset - 1MB
        if ($plan.Free -lt 16MB) { $plan.Kind = 'Nothing'; return $plan }
        $plan.Kind = 'Simple'
        return $plan
    }

    if ($after.Count -eq 1 -and (Test-Recovery $after[0])) {
        $rec = $after[0]
        $plan.Recovery = $rec
        $plan.Free = $disk.Size - ($rec.Offset + $rec.Size)
        $plan.NewCSize = $disk.Size - $c.Offset - $NewRecoverySize - 1MB
        if ($plan.Free -lt $NewRecoverySize) { $plan.Kind = 'Nothing'; return $plan }
        $plan.Kind = 'MoveRecovery'
        return $plan
    }

    $plan.Kind = 'Unsupported'
    $plan.Message = ($after | ForEach-Object {
        "  - מחיצה {0}: {1} ({2})" -f $_.PartitionNumber, (Fmt $_.Size), $_.Type
    }) -join "`r`n"
    return $plan
}

function Stop-IfNotActionable($plan) {
    switch ($plan.Kind) {
        'Nothing' {
            Finish 2 ("אין שטח פנוי משמעותי אחרי כונן C:.`r`nגודל C: כרגע: {0}`r`nאין צורך בשינוי." -f (Fmt $plan.C.Size))
        }
        'Unsupported' {
            Finish 3 ("אחרי כונן C: יש מחיצות שהכלי לא מוחק (מטעמי בטיחות):`r`n{0}`r`n`r`nלא בוצע שום שינוי." -f $plan.Message)
        }
    }
}

Write-Log "===== ExtendC $Mode ====="

try {
    $plan = Get-Plan
} catch {
    Finish 1 ("שגיאה בקריאת מבנה הדיסק:`r`n{0}" -f $_.Exception.Message)
}
Stop-IfNotActionable $plan

# ---------------------------------------------------------------- Analyze
if ($Mode -eq 'Analyze') {
    $d = $plan.Disk.Number
    if ($plan.Kind -eq 'Simple') {
        Finish 0 (@"
נמצא בדיסק {0}:
  כונן C: בגודל {1}
  שטח לא מוקצה מיד אחריו: {2}

הכלי יגדיל את C: לכ-{3}.

מומלץ לגבות קבצים חשובים לפני שינוי מחיצות.
להמשיך?
"@ -f $d, (Fmt $plan.C.Size), (Fmt $plan.Free), (Fmt $plan.NewCSize))
    }
    Finish 0 (@"
נמצא בדיסק {0}:
  כונן C: בגודל {1}
  מחיצת שחזור ({2}) - חוסמת את ההגדלה
  שטח לא מוקצה אחריה: {3}

הכלי יבצע:
  1. יעביר זמנית את סביבת השחזור של Windows לכונן C:
  2. ימחק את מחיצת השחזור הישנה
  3. יגדיל את C: לכ-{4}
  4. ייצור מחיצת שחזור חדשה (1 GB) בסוף הדיסק
  5. יפעיל מחדש את סביבת השחזור

מומלץ לגבות קבצים חשובים לפני שינוי מחיצות.
לא לכבות את המחשב בזמן הפעולה.
להמשיך?
"@ -f $d, (Fmt $plan.C.Size), (Fmt $plan.Recovery.Size), (Fmt $plan.Free), (Fmt $plan.NewCSize))
}

# ---------------------------------------------------------------- Apply
$d = $plan.Disk.Number
$winreDisabled = $false
try {
    if ($plan.Kind -eq 'Simple') {
        $sup = Get-PartitionSupportedSize -DriveLetter C
        Write-Log ("SizeMax = {0}" -f $sup.SizeMax)
        Resize-Partition -DriveLetter C -Size $sup.SizeMax
        $newC = (Get-Partition -DriveLetter C).Size
        Finish 0 ("הפעולה הושלמה בהצלחה.`r`nגודל C: החדש: {0}" -f (Fmt $newC))
    }

    # --- MoveRecovery
    $rec = $plan.Recovery

    # 1. Move WinRE image back onto C:
    Write-Log 'Step 1: reagentc /disable'
    Invoke-Tool $ReAgentC @('/disable') | Out-Null
    $winreDisabled = $true
    if (-not (Test-Path -LiteralPath $WinreWim)) {
        Invoke-Tool $ReAgentC @('/enable') | Out-Null
        Finish 1 ("לא נמצא הקובץ {0} אחרי העברת סביבת השחזור.`r`nכדי לא לאבד את סביבת השחזור, מחיצת השחזור לא נמחקה.`r`nלא בוצע שינוי במחיצות." -f $WinreWim)
    }
    Write-Log "Winre.wim found at $WinreWim"

    # 2. Delete the old recovery partition (verified by offset)
    Write-Log ("Step 2: deleting recovery partition #{0} at offset {1}" -f $rec.PartitionNumber, $rec.Offset)
    $check = Get-Partition -DiskNumber $d -PartitionNumber $rec.PartitionNumber
    if ($check.Offset -ne $rec.Offset -or -not (Test-Recovery $check)) {
        throw 'Partition identity check failed - aborting before delete.'
    }
    try {
        Remove-Partition -DiskNumber $d -PartitionNumber $rec.PartitionNumber -Confirm:$false
    } catch {
        Write-Log ("Remove-Partition failed: {0} - trying diskpart override" -f $_.Exception.Message)
        Invoke-DiskPart @("select disk $d", "select partition $($rec.PartitionNumber)", 'delete partition override') | Out-Null
    }
    Update-Disk -Number $d
    if (Get-Partition -DiskNumber $d | Where-Object { $_.Offset -eq $rec.Offset }) {
        throw 'The recovery partition could not be deleted.'
    }

    # 3. Extend C:, leaving room for a new recovery partition at the end
    Write-Log 'Step 3: extending C:'
    $sup = Get-PartitionSupportedSize -DriveLetter C
    $target = [math]::Floor(($sup.SizeMax - $NewRecoverySize) / 1MB) * 1MB
    Write-Log ("SizeMax = {0}, target = {1}" -f $sup.SizeMax, $target)
    Resize-Partition -DriveLetter C -Size $target
    $newC = (Get-Partition -DriveLetter C).Size

    # 4. Re-create the recovery partition at the end of the disk
    Write-Log 'Step 4: creating new recovery partition'
    $recOk = $false
    try {
        $np = New-Partition -DiskNumber $d -UseMaximumSize
        Format-Volume -Partition $np -FileSystem NTFS -NewFileSystemLabel 'Recovery' -Confirm:$false -Force | Out-Null
        $np = Get-Partition -DiskNumber $d -PartitionNumber $np.PartitionNumber
        if ($np.DriveLetter -and $np.DriveLetter -ne [char]0) {
            Remove-PartitionAccessPath -DiskNumber $d -PartitionNumber $np.PartitionNumber -AccessPath ("{0}:\" -f $np.DriveLetter)
        }
        if ($plan.Disk.PartitionStyle -eq 'GPT') {
            $lines = @("select disk $d", "select partition $($np.PartitionNumber)",
                       'set id=de94bba4-06d1-4d40-a16a-bfd50179d6ac override',
                       'gpt attributes=0x8000000000000001')
        } else {
            $lines = @("select disk $d", "select partition $($np.PartitionNumber)", 'set id=27 override')
        }
        $recOk = ((Invoke-DiskPart $lines) -eq 0)
    } catch {
        Write-Log ("Creating recovery partition failed: {0}" -f $_.Exception.Message)
    }

    # 5. Re-enable WinRE (goes into the new partition, or stays on C:)
    Write-Log 'Step 5: reagentc /enable'
    $rc = Invoke-Tool $ReAgentC @('/enable')
    $winreDisabled = $false
    Invoke-Tool $ReAgentC @('/info') | Out-Null

    $recText = if ($recOk) { 'נוצרה מחיצת שחזור חדשה בגודל 1 GB בסוף הדיסק.' }
               else { 'לא הצלחתי ליצור מחיצת שחזור חדשה - סביבת השחזור נשארה על כונן C: (זה תקין).' }
    $reText  = if ($rc -eq 0) { 'סביבת השחזור של Windows פעילה.' }
               else { 'שים לב: הפעלת סביבת השחזור נכשלה. אפשר לנסות ידנית: reagentc /enable (בחלון מנהל).' }
    Finish 0 ("הפעולה הושלמה בהצלחה.`r`n`r`nגודל C: החדש: {0}`r`n{1}`r`n{2}" -f (Fmt $newC), $recText, $reText)
}
catch {
    $err = $_.Exception.Message
    Write-Log ("ERROR: {0}`r`n{1}" -f $err, $_.ScriptStackTrace)
    if ($winreDisabled) { Invoke-Tool $ReAgentC @('/enable') | Out-Null }
    Finish 1 ("אירעה שגיאה:`r`n{0}" -f $err)
}
