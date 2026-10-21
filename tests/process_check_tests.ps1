# Runs in Windows PowerShell 5.1 or portable PowerShell; only disposable test processes are stopped.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts/Check-LANStreamProcesses.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'Process checker is missing.' }
. $scriptPath

function Assert-Equal($Actual, $Expected, [string]$Label) {
    if ($Actual -cne $Expected) { throw "$Label -- expected '$Expected', got '$Actual'" }
}
function Assert-Rejected([scriptblock]$Action, [string]$Label) {
    $rejected = $false
    try { & $Action } catch { $rejected = $true }
    if (-not $rejected) { throw "$Label -- expected rejection" }
}
function New-TestRow {
    [pscustomobject]@{
        ProcessId = 100; ParentProcessId = 10; CreationDate = [datetime]'2026-01-01T12:00:00Z'
        Name = 'LANStream.exe'; ExecutablePath = 'C:\LANStream-Enhanced\LANStream.exe'
        ConfiguredPort = '8061'; Hash = 'old-build'
    }
}
$row = New-TestRow
$listener = [pscustomobject]@{ LocalPort = 8061; OwningProcess = 100; LocalAddress = '0.0.0.0' }
Assert-Equal (Get-LanStreamStopBlock $row @($listener) '') '' 'Enhanced listener eligible'
Assert-Equal (Get-LanStreamStopBlock $row @() '') '' 'Hidden Enhanced instance eligible'
foreach ($port in @(8060, 8062)) {
    $other = [pscustomobject]@{ LocalPort = $port; OwningProcess = 100 }
    if (-not (Get-LanStreamStopBlock $row @($listener, $other) '')) { throw "Port $port was not protected" }
}
$row.ExecutablePath = 'C:\LANStream\LANStream.exe'
if (-not (Get-LanStreamStopBlock $row @($listener) '')) { throw 'Original installation was not protected' }
$row.ExecutablePath = 'C:\LANStream\backup\LANStream.exe'
if (-not (Get-LanStreamStopBlock $row @($listener) '')) { throw 'Original subfolder was not protected' }
$row = New-TestRow
$row.Name = 'python.exe'
if (-not (Get-LanStreamStopBlock $row @($listener) '')) { throw 'Unknown listener allowed' }
$row = New-TestRow
$row.ExecutablePath = ''
if (-not (Get-LanStreamStopBlock $row @($listener) '')) { throw 'Unreadable path allowed' }
$row = New-TestRow
$row.ConfiguredPort = '8062'
if (-not (Get-LanStreamStopBlock $row @() '')) { throw 'Non-listening diagnostic instance allowed' }
$row.ConfiguredPort = 'unknown'
if (-not (Get-LanStreamStopBlock $row @() '')) { throw 'Unidentified background instance allowed' }
$row = New-TestRow
if (-not (Get-LanStreamStopBlock $row @($listener) 'old-build')) { throw 'Reference build allowed' }
Assert-Equal (Get-LanStreamStopBlock $row @($listener) 'new-build') '' 'Different reference eligible'

$row | Add-Member NoteProperty StopBlock ''
Assert-Equal (@(Select-LanStreamStopTargets 'ALL' @($row)).Count) 1 'ALL selection'
Assert-Equal (@(Select-LanStreamStopTargets '100, 100' @($row)).Count) 1 'Duplicate IDs'
Assert-Equal (@(Select-LanStreamStopTargets '' @($row)).Count) 0 'Empty cancels'
Assert-Rejected { Select-LanStreamStopTargets '100,999' @($row) } 'Mixed valid and unknown IDs'
Assert-Rejected { Select-LanStreamStopTargets '1e2' @($row) } 'Malformed ID'
$row.StopBlock = 'protected'
Assert-Equal (@(Select-LanStreamStopTargets 'ALL' @($row)).Count) 0 'ALL excludes protected'
Assert-Rejected { Select-LanStreamStopTargets '100' @($row) } 'Explicit protected ID'

$original = New-TestRow
$current = New-TestRow
Assert-LanStreamSameProcess $original $current
$current.CreationDate = $current.CreationDate.AddSeconds(1)
Assert-Rejected { Assert-LanStreamSameProcess $original $current } 'Reused PID'
$current = New-TestRow
$current.ExecutablePath = 'C:\Other\LANStream.exe'
Assert-Rejected { Assert-LanStreamSameProcess $original $current } 'Changed executable'
$current = New-TestRow
$current.Hash = 'changed'
Assert-Rejected { Assert-LanStreamSameProcess $original $current } 'Changed binary'
Assert-Rejected { Assert-LanStreamSameProcess $original $null } 'Exited process'

$temp = Join-Path ([IO.Path]::GetTempPath()) ('lanstream-process-test-' + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $temp
try {
    $exe = Join-Path $temp 'LANStream.exe'
    [IO.File]::WriteAllText($exe, 'abc', [Text.Encoding]::ASCII)
    $hash = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    $identity = Join-Path $temp 'BUILD-IDENTITY.json'
    [IO.File]::WriteAllText($identity, ('{"source_commit":"' + ('a' * 40) + '","executable_sha256":"' + $hash + '"}'))
    $details = Get-LanStreamFileDetails $exe
    Assert-Equal $details.Hash $hash 'Real binary fingerprint'
    Assert-Equal $details.SourceCommit ('a' * 40) 'Matching build identity'
    [IO.File]::WriteAllText($exe, 'different')
    Assert-Equal (Get-LanStreamFileDetails $exe).SourceCommit 'unknown' 'Stale identity rejected'
    [IO.File]::WriteAllText($identity, 'invalid json')
    Assert-Equal (Get-LanStreamFileDetails $exe).SourceCommit 'unknown' 'Malformed identity tolerated'
    [IO.File]::WriteAllText((Join-Path $temp 'config.txt'), "password=private`nport=8061`n")
    Assert-Equal (Get-LanStreamConfiguredPort $exe) '8061' 'Configured port'
    [IO.File]::AppendAllText((Join-Path $temp 'config.txt'), "port=8060`n")
    Assert-Equal (Get-LanStreamConfiguredPort $exe) 'unknown' 'Ambiguous config rejected'
} finally { Remove-Item -LiteralPath $temp -Recurse -Force }

# Exercise the real inventory against simulated Windows CIM/TCP results. Keep
# unrelated listeners in the snapshot so protection cannot be lost by filtering.
$script:tcpFails = $false
function Get-NetTCPConnection {
    [CmdletBinding()]
    param($State)
    if ($script:tcpFails) { throw 'TCP inventory unavailable' }
    return @(
        [pscustomobject]@{ LocalPort = 8061; OwningProcess = 200; LocalAddress = '0.0.0.0' },
        [pscustomobject]@{ LocalPort = 8060; OwningProcess = 100; LocalAddress = '0.0.0.0' }
    )
}
function Get-CimInstance {
    [CmdletBinding()]
    param($ClassName)
    return @(
        (New-TestRow),
        [pscustomobject]@{ Name = 'Other.exe'; ProcessId = 200; ParentProcessId = 10; CreationDate = [datetime]::Now; ExecutablePath = '' },
        [pscustomobject]@{ Name = 'Unrelated.exe'; ProcessId = 300; ParentProcessId = 10; CreationDate = [datetime]::Now; ExecutablePath = '' }
    )
}
$inventory = Get-LanStreamInventory
Assert-Equal (($inventory.Processes.ProcessId | Sort-Object) -join ',') '100,200' 'Hidden app and unknown 8061 owner included'
Assert-Equal $inventory.Listeners.Count 2 'Other listeners retained for protection'
$script:tcpFails = $true
Assert-Rejected { Get-LanStreamInventory } 'Listener query failure must abort'
Remove-Item Function:\Get-NetTCPConnection, Function:\Get-CimInstance

if ([Environment]::OSVersion.Platform -eq 'Win32NT' -and $PSVersionTable.PSEdition -eq 'Desktop') {
    # Exercise the real Windows CIM, process handle and Kill/WaitForExit path on
    # an executable built just for this test. Never use an installed app.
    $nativeTemp = Join-Path ([IO.Path]::GetTempPath()) ('lanstream-native-test-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $nativeTemp
    $child = $null
    try {
        $nativeExe = Join-Path $nativeTemp 'LANStream.exe'
        Add-Type -TypeDefinition 'public class ProcessCheckFixture { public static void Main() { System.Threading.Thread.Sleep(120000); } }' -OutputAssembly $nativeExe -OutputType ConsoleApplication
        [IO.File]::WriteAllText((Join-Path $nativeTemp 'config.txt'), 'port=8061')
        $child = Start-Process -FilePath $nativeExe -PassThru -WindowStyle Hidden
        $found = @((Get-LanStreamInventory).Processes | Where-Object { $_.ProcessId -eq $child.Id })
        Assert-Equal $found.Count 1 'Real Windows hidden instance detected'
        Stop-LanStreamVerifiedProcess $found[0] ''
        if (-not $child.WaitForExit(5000)) { throw 'Real Windows fixture did not stop' }
        Write-Host 'PASS: native Windows CIM and verified process termination'
    } finally {
        if ($child) {
            if (-not $child.HasExited) { $child.Kill(); $null = $child.WaitForExit(5000) }
            $child.Dispose()
        }
        Remove-Item -LiteralPath $nativeTemp -Recurse -Force
    }
}

# Windows inventory and process handles are external boundaries. Model only those
# boundaries here, then exercise the real stop guard, including its side effect.
$script:killCount = 0
$script:currentRow = New-TestRow
$script:currentListeners = @($listener)
function Get-LanStreamInventory { [pscustomobject]@{ Processes = @($script:currentRow); Listeners = $script:currentListeners } }
function Open-LanStreamProcess {
    $handle = [pscustomobject]@{ StartTime = [datetime]'2026-01-01T12:00:00Z'; HasExited = $false }
    $handle | Add-Member ScriptMethod Kill { $script:killCount++ }
    $handle | Add-Member ScriptMethod WaitForExit { param($Milliseconds) return $true }
    $handle | Add-Member ScriptMethod Dispose { }
    return $handle
}
Stop-LanStreamVerifiedProcess (New-TestRow) ''
Assert-Equal $script:killCount 1 'Verified process stopped once'
$script:currentRow.CreationDate = $script:currentRow.CreationDate.AddSeconds(1)
Assert-Rejected { Stop-LanStreamVerifiedProcess (New-TestRow) '' } 'PID replaced during prompt'
Assert-Equal $script:killCount 1 'Reused PID never stopped'
$script:currentRow = New-TestRow
$script:currentListeners += [pscustomobject]@{ LocalPort = 8060; OwningProcess = 100 }
Assert-Rejected { Stop-LanStreamVerifiedProcess (New-TestRow) '' } 'Protected listener appeared during prompt'
Assert-Equal $script:killCount 1 'Protected process never stopped'
$script:currentListeners = @($listener)
Assert-Rejected { Stop-LanStreamVerifiedProcess (New-TestRow) 'old-build' } 'Reference protection rechecked'
Assert-Equal $script:killCount 1 'Reference process never stopped'
$script:currentRow = New-TestRow
function Open-LanStreamProcess {
    $handle = [pscustomobject]@{ StartTime = [datetime]'2026-01-01T12:01:00Z'; HasExited = $false }
    $handle | Add-Member ScriptMethod Kill { $script:killCount++ }
    $handle | Add-Member ScriptMethod Dispose { }
    return $handle
}
Assert-Rejected { Stop-LanStreamVerifiedProcess (New-TestRow) '' } 'Reused PID caught by handle start time'
Assert-Equal $script:killCount 1 'Wrong process handle never stopped'

# Exercise the actual interactive flow: report/WhatIf/cancel must never stop,
# and ALL must visit every eligible PID, then observe rather than kill a restart.
$script:currentListeners = @($listener)
$row2 = New-TestRow
$row2.ProcessId = 101
$row2.ExecutablePath = 'C:\Old-Enhanced\LANStream.exe'
function Get-LanStreamInventory { [pscustomobject]@{ Processes = @((New-TestRow), $row2); Listeners = $script:currentListeners } }
$script:answers = [Collections.Generic.Queue[string]]::new()
function Read-Host { param($Prompt) return $script:answers.Dequeue() }
function Start-Sleep { param($Seconds) }
$script:stoppedIds = @()
function Stop-LanStreamVerifiedProcess { param($Original, $ReferenceHash) $script:stoppedIds += $Original.ProcessId }
Assert-Equal (Invoke-LanStreamProcessCheck -ReportOnly) 0 'Report exits successfully'
Assert-Equal (Invoke-LanStreamProcessCheck -WhatIf) 0 'WhatIf exits successfully'
Assert-Equal $script:stoppedIds.Count 0 'Read-only modes never stop'
$script:answers.Enqueue('')
Assert-Equal (Invoke-LanStreamProcessCheck) 0 'Empty selection exits'
Assert-Equal $script:stoppedIds.Count 0 'Empty selection never stops'
$script:answers.Enqueue('ALL'); $script:answers.Enqueue('no')
Assert-Equal (Invoke-LanStreamProcessCheck) 0 'Confirmation declined'
Assert-Equal $script:stoppedIds.Count 0 'Declined confirmation never stops'
$script:answers.Enqueue('ALL'); $script:answers.Enqueue('STOP')
Assert-Equal (Invoke-LanStreamProcessCheck -WarningVariable restartWarnings) 0 'Confirmed ALL exits'
Assert-Equal ($script:stoppedIds -join ',') '100,101' 'ALL stops both selected instances only once'
if (-not ($restartWarnings -match 'still occupied')) { throw 'Remaining/restarted listener was not reported' }
$script:stoppedIds = @()
function Stop-LanStreamVerifiedProcess {
    param($Original, $ReferenceHash)
    $script:stoppedIds += $Original.ProcessId
    if ($Original.ProcessId -eq 100) { throw 'Process changed while waiting' }
}
$script:answers.Enqueue('ALL'); $script:answers.Enqueue('STOP')
Assert-Equal (Invoke-LanStreamProcessCheck) 1 'A failed stop produces nonzero exit status'
Assert-Equal ($script:stoppedIds -join ',') '100,101' 'A failed stop does not hide other requested results'

Write-Host 'PASS: process inventory policy, identity, selection and stop guards'
