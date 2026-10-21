#Requires -Version 5.1
<#
.SYNOPSIS
Inspect port 8061 and interactively stop selected LANStream instances.
.DESCRIPTION
Run locally on the Windows N100, preferably in an elevated PowerShell window.
Reports all 8061 owners and all LANStream.exe processes, including hidden ones.
File hashes identify builds; they do not establish which build is newer or prove
the identity of already-loaded code. No passwords or HTTP login are required.
Stops only selected, revalidated LANStream.exe processes after a typed STOP.
Protects C:\LANStream, processes listening on other ports, and reference builds.
Does not stop process trees or modify files, services, tasks, or startup settings.
.PARAMETER ReferenceExecutable
Optional path to the desired LANStream.exe. Matching on-disk builds are protected.
.PARAMETER ReportOnly
Print the inventory without prompting or stopping anything.
.EXAMPLE
.\Check-LANStreamProcesses.ps1
.EXAMPLE
.\Check-LANStreamProcesses.ps1 -ReferenceExecutable 'C:\Downloads\New build\LANStream.exe'
.EXAMPLE
.\Check-LANStreamProcesses.ps1 -ReportOnly
.EXAMPLE
.\Check-LANStreamProcesses.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ReferenceExecutable,
    [switch]$ReportOnly
)


function Get-LanStreamFileDetails {
    param([string]$ExecutablePath)
    $result = [pscustomobject]@{ Hash = 'unknown'; FileVersion = 'unknown'; ProductVersion = 'unknown'; SourceCommit = 'unknown'; LastWriteTime = $null }
    if (-not $ExecutablePath) { return $result }
    try {
        $file = Get-Item -LiteralPath $ExecutablePath -ErrorAction Stop
        $result.Hash = (Get-FileHash -LiteralPath $ExecutablePath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
        $result.LastWriteTime = $file.LastWriteTime
        if ($file.VersionInfo.FileVersion) { $result.FileVersion = $file.VersionInfo.FileVersion }
        if ($file.VersionInfo.ProductVersion) { $result.ProductVersion = $file.VersionInfo.ProductVersion }
        $identityPath = Join-Path $file.DirectoryName 'BUILD-IDENTITY.json'
        if (Test-Path -LiteralPath $identityPath -PathType Leaf) {
            $identity = [IO.File]::ReadAllText($identityPath) | ConvertFrom-Json -ErrorAction Stop
            if ($identity.executable_sha256 -ieq $result.Hash -and $identity.source_commit -match '^[a-fA-F0-9]{40,64}$') {
                $result.SourceCommit = $identity.source_commit
            }
        }
    } catch { } # Missing/locked files and older packages remain inspectable.
    return $result
}

function Get-LanStreamConfiguredPort {
    param([string]$ExecutablePath)
    if (-not $ExecutablePath) { return 'unknown' }
    try {
        $configPath = Join-Path (Split-Path -Parent $ExecutablePath) 'config.txt'
        $ports = @([IO.File]::ReadLines($configPath) | Where-Object { $_ -match '^\s*port\s*=' })
        if ($ports.Count -eq 1 -and $ports[0] -match '^\s*port\s*=\s*(\d+)\s*$') { return $Matches[1] }
    } catch { }
    return 'unknown'
}

function Get-LanStreamInventory {
    # Query all listeners so an absent 8061 is normal, but a failed query is fatal.
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop)
    $owners = @($listeners | Where-Object { $_.LocalPort -eq 8061 } | Select-Object -ExpandProperty OwningProcess -Unique)
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $_.Name -ieq 'LANStream.exe' -or $owners -contains $_.ProcessId
    } | ForEach-Object {
        $file = Get-LanStreamFileDetails $_.ExecutablePath
        [pscustomobject]@{
            ProcessId = $_.ProcessId; ParentProcessId = $_.ParentProcessId
            CreationDate = $_.CreationDate; Name = $_.Name; ExecutablePath = $_.ExecutablePath
            ConfiguredPort = Get-LanStreamConfiguredPort $_.ExecutablePath
            Hash = $file.Hash; FileVersion = $file.FileVersion; ProductVersion = $file.ProductVersion
            SourceCommit = $file.SourceCommit; LastWriteTime = $file.LastWriteTime
        }
    })
    foreach ($ownerId in $owners) {
        if (-not @($processes | Where-Object { $_.ProcessId -eq $ownerId }).Count) {
            Write-Warning "8061 owner PID $ownerId exited or could not be inspected. Refresh before acting."
        }
    }
    return [pscustomobject]@{ Processes = $processes; Listeners = $listeners }
}

function Get-LanStreamStopBlock {
    param($Process, [object[]]$Listeners, [string]$ReferenceHash)
    if ($Process.Name -ine 'LANStream.exe' -or -not $Process.ExecutablePath -or
        $Process.ExecutablePath -notmatch '^[A-Za-z]:\\.+\\LANStream\.exe$' -or -not $Process.CreationDate) {
        return 'Inspection only: not a LANStream.exe with a readable local path/start time.'
    }
    if ($Process.ExecutablePath -match '^C:\\LANStream(?:\\|$)') { return 'Protected original installation: C:\LANStream.' }
    $ports = @($Listeners | Where-Object { $_.OwningProcess -eq $Process.ProcessId } | Select-Object -ExpandProperty LocalPort -Unique)
    if (@($ports | Where-Object { $_ -ne 8061 }).Count) { return 'Protected: listens on a port other than 8061 (including 8060/8062).' }
    if ($ports -notcontains 8061 -and $Process.ConfiguredPort -ne '8061') { return 'Inspection only: no 8061 listener or unambiguous port=8061 config.' }
    if ($Process.Hash -eq 'unknown' -or -not $Process.Hash) { return 'Inspection only: executable fingerprint unavailable.' }
    if ($ReferenceHash -and $Process.Hash -eq $ReferenceHash) { return 'Keep: matches the reference executable.' }
    return ''
}

function Show-LanStreamInventory {
    param($Inventory, [string]$ReferenceHash)
    Write-Host "`nLANStream process check -- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    $owners = @($Inventory.Listeners | Where-Object { $_.LocalPort -eq 8061 })
    if ($owners.Count) {
        Write-Host 'Port 8061 listeners:'
        $owners | Select-Object LocalAddress, LocalPort, OwningProcess | Format-Table -AutoSize | Out-Host
    } else { Write-Host 'Port 8061 is free (no listening process at this check).' }
    if (-not $Inventory.Processes.Count) { Write-Host 'No LANStream.exe processes found.' }
    foreach ($process in $Inventory.Processes) {
        $block = Get-LanStreamStopBlock $process $Inventory.Listeners $ReferenceHash
        $process | Add-Member -NotePropertyName StopBlock -NotePropertyValue $block -Force
        $ports = @($Inventory.Listeners | Where-Object { $_.OwningProcess -eq $process.ProcessId } | Select-Object -ExpandProperty LocalPort -Unique)
        $comparison = 'No reference supplied; build age unknown.'
        if ($ReferenceHash) {
            $comparison = 'Different from reference; build age unknown.'
            if ($process.Hash -eq 'unknown') { $comparison = 'Cannot compare with reference.' }
            elseif ($process.Hash -eq $ReferenceHash) { $comparison = 'Matches reference executable.' }
        }
        [pscustomobject]@{
            PID = $process.ProcessId; ParentPID = $process.ParentProcessId
            Started = $process.CreationDate; Executable = $process.ExecutablePath
            ListeningPorts = $ports -join ', '; ConfiguredPort = $process.ConfiguredPort
            FileVersion = $process.FileVersion; ProductVersion = $process.ProductVersion
            ExecutableSHA256 = $process.Hash; SourceCommit = $process.SourceCommit
            Reference = $comparison
            StopAction = $(if ($block) { $block } else { 'Eligible for your selection.' })
        } | Format-List | Out-Host
        if ($process.LastWriteTime -and $process.CreationDate -and $process.LastWriteTime -gt $process.CreationDate) {
            Write-Warning 'Executable was modified after process start; its loaded build may differ from the file.'
        }
    }
    Write-Host 'Versions/hashes above describe files on disk; PE versions may be generic runtime metadata.'
    Write-Host 'For a live build/instance ID, sign in to /transcoder-status and compare with the launcher.'
}

function Select-LanStreamStopTargets {
    param([string]$Selection, [object[]]$Processes)
    $eligible = @($Processes | Where-Object { -not $_.StopBlock })
    if ([string]::IsNullOrWhiteSpace($Selection)) { return }
    if ($Selection.Trim() -ieq 'ALL') { return $eligible }
    if ($Selection -notmatch '^\s*\d+(?:[\s,]+\d+)*\s*$') { throw 'Enter ALL, a comma/space separated PID list, or Enter to cancel.' }
    $ids = @($Selection.Trim() -split '[\s,]+' | ForEach-Object { [int]$_ } | Select-Object -Unique)
    $selected = @()
    foreach ($ownerId in $ids) {
        $target = @($eligible | Where-Object { $_.ProcessId -eq $ownerId })
        if ($target.Count -ne 1) { throw "PID $ownerId is not an eligible displayed process. Nothing was stopped." }
        $selected += $target[0]
    }
    return $selected
}

function Assert-LanStreamSameProcess {
    param($Original, $Current)
    if (-not $Current -or $Original.ProcessId -ne $Current.ProcessId -or
        $Original.ExecutablePath -ine $Current.ExecutablePath -or
        $Original.CreationDate -ne $Current.CreationDate -or $Original.Hash -ne $Current.Hash) {
        throw 'Process exited, PID was reused, or executable changed since inspection. Refresh and select again.'
    }
}

function Open-LanStreamProcess {
    param([int]$ProcessId)
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    try {
        # Cache a real OS handle before rechecking identity; Kill uses this handle,
        # so a subsequently recycled numeric PID cannot target another process.
        $null = $process.Handle
        return $process
    } catch { $process.Dispose(); throw }
}

function Stop-LanStreamVerifiedProcess {
    param($Original, [string]$ReferenceHash)
    $process = Open-LanStreamProcess $Original.ProcessId
    try {
        if ($process.HasExited -or [math]::Abs(($process.StartTime.ToUniversalTime() - $Original.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) {
            throw 'The inspected process has exited or its PID was reused.'
        }
        $fresh = Get-LanStreamInventory
        $current = @($fresh.Processes | Where-Object { $_.ProcessId -eq $Original.ProcessId })
        if ($current.Count -ne 1) { throw 'The selected process is no longer inspectable.' }
        Assert-LanStreamSameProcess $Original $current[0]
        $block = Get-LanStreamStopBlock $current[0] $fresh.Listeners $ReferenceHash
        if ($block) { throw $block }
        $process.Kill()
        if (-not $process.WaitForExit(10000)) { throw 'Stop requested but exit was not observed within 10 seconds.' }
        Write-Host "Stopped verified LANStream PID $($Original.ProcessId)."
    } finally { $process.Dispose() }
}

function Invoke-LanStreamProcessCheck {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([string]$ReferenceExecutable, [switch]$ReportOnly)
    $referenceHash = ''
    if ($ReferenceExecutable) {
        $reference = Get-LanStreamFileDetails $ReferenceExecutable
        if ($reference.Hash -eq 'unknown') { throw 'Cannot read the reference executable. No process was stopped.' }
        $referenceHash = $reference.Hash
        Write-Host "Reference SHA256: $referenceHash"
    }
    $inventory = Get-LanStreamInventory
    Show-LanStreamInventory $inventory $referenceHash
    if ($ReportOnly) { return 0 }
    $eligible = @($inventory.Processes | Where-Object { -not $_.StopBlock })
    if (-not $eligible.Count) { Write-Host 'No eligible instances to stop.'; return 0 }
    Write-Host 'Choose which instances are old/unwanted. ALL includes every eligible instance, even the current build.'
    Write-Host 'Matching reference builds and protected processes are always excluded.'
    if ($WhatIfPreference) { $selection = 'ALL' }
    else { $selection = Read-Host 'PIDs to stop (comma/space separated), ALL, or Enter to exit' }
    $selected = @(Select-LanStreamStopTargets $selection $inventory.Processes)
    if (-not $selected.Count) { Write-Host 'Nothing selected; no processes stopped.'; return 0 }
    Write-Host 'Selected processes:'
    $selected | Select-Object ProcessId, ExecutablePath, CreationDate | Format-List | Out-Host
    if (-not $WhatIfPreference) {
        Write-Host 'Stopping interrupts playback and background work in these instances. Prefer Ctrl+C for a visible console.'
        if ((Read-Host 'Type STOP to terminate these selected processes') -cne 'STOP') {
            Write-Host 'Cancelled; no processes stopped.'; return 0
        }
    }
    $failed = $false
    foreach ($target in $selected) {
        if ($PSCmdlet.ShouldProcess("PID $($target.ProcessId): $($target.ExecutablePath)", 'Stop verified LANStream process')) {
            try { Stop-LanStreamVerifiedProcess $target $referenceHash }
            catch { $failed = $true; Write-Warning "PID $($target.ProcessId) not stopped: $($_.Exception.Message)" }
        }
    }
    if ($WhatIfPreference) { return 0 }
    Write-Host "`nWaiting three seconds to check for remaining/restarted listeners..."
    Start-Sleep -Seconds 3
    $after = Get-LanStreamInventory
    Show-LanStreamInventory $after $referenceHash
    if (@($after.Listeners | Where-Object { $_.LocalPort -eq 8061 }).Count) {
        Write-Warning '8061 is still occupied or was restarted. Review the PID above and check Task Scheduler, Services and Startup apps. No new process was stopped automatically.'
    }
    Write-Host 'Only selected processes were targeted. Services/tasks/startup settings and child processes were not changed.'
    if ($failed) { return 1 }
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $ErrorActionPreference = 'Stop'
        if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Run this script locally on the Windows N100.' }
        exit (Invoke-LanStreamProcessCheck -ReferenceExecutable $ReferenceExecutable -ReportOnly:$ReportOnly -WhatIf:$WhatIfPreference)
    } catch {
        Write-Error $_ -ErrorAction Continue
        exit 1
    }
}
