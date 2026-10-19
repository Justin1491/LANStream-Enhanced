#requires -Version 5.1
[CmdletBinding()]
param([switch]$SimulateHardwareSuccess, [switch]$UseConfigTemplate)
$ErrorActionPreference = 'Stop'
$base = Split-Path $PSScriptRoot -Parent
. (Join-Path $base 'scripts\Install-LANStreamEnhanced.ps1')
$mode = if ($SimulateHardwareSuccess) { 'simulated-hardware' } else { 'real-hardware' }
$work = Join-Path $base ('benchmark-results\installer-' + $mode + '-' + [Guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($work) | Out-Null
$media = Join-Path $work 'Fictional media'
[IO.Directory]::CreateDirectory($media) | Out-Null
[IO.File]::WriteAllText((Join-Path $media 'Example notes.txt'), 'Fictional acceptance media')
$clip = Join-Path $base 'benchmark-results\browser-smoke\Fictional Media\Clip 000000.mp4'
if (-not (Test-Path -LiteralPath $clip)) { throw 'Generate the existing synthetic video fixture before running this integration check.' }
[IO.File]::Copy($clip, (Join-Path $media 'Example clip.mp4'))
$target = Join-Path $work 'Enhanced'
$standbyConfig = Join-Path $work 'standby-config.txt'
$standbyCache = Join-Path $work 'standby-cache'
$standbyPort = 18061
if (@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object { $_.Port -in @(8061, $standbyPort) }).Count) { throw 'An integration test port is occupied; no process was stopped.' }
[IO.File]::WriteAllText($standbyConfig, "port=$standbyPort`r`ntranscoder=software`r`n")
$standbyText = [IO.File]::ReadAllText($standbyConfig)
$keys = @('LANSTREAM_CONFIG_FILE','LANSTREAM_CACHE_DIR','LANSTREAM_PORT','LANSTREAM_HOST','LANSTREAM_TRANSCODER','LANSTREAM_SESSION_SECRET','LANSTREAM_PASSWORD','LANSTREAM_USE_MASTER_PASSWORD')
$original = @{}
foreach ($key in $keys) { $original[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
$standby = $null
$own = $null
try {
    $env:LANSTREAM_CONFIG_FILE = $standbyConfig
    $env:LANSTREAM_CACHE_DIR = $standbyCache
    $env:LANSTREAM_PORT = [string]$standbyPort
    $env:LANSTREAM_HOST = '127.0.0.1'
    $env:LANSTREAM_TRANSCODER = 'software'
    $env:LANSTREAM_USE_MASTER_PASSWORD = 'false'
    Remove-Item Env:LANSTREAM_PASSWORD -ErrorAction SilentlyContinue
    $standbyExe = Join-Path $base 'dist\LANStream\LANStream.exe'
    $standby = Start-Process -FilePath $standbyExe -WorkingDirectory (Split-Path $standbyExe -Parent) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $work 'standby.stdout.txt') -RedirectStandardError (Join-Path $work 'standby.stderr.txt')
    $deadline = [DateTime]::UtcNow.AddSeconds(35)
    do {
        $listeners = @(Get-NetTCPConnection -LocalPort $standbyPort -State Listen -ErrorAction SilentlyContinue)
        if (@($listeners | Where-Object { $_.OwningProcess -eq $standby.Id }).Count) { break }
        if ($standby.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw 'Independent standby process did not start.' }
        Start-Sleep -Milliseconds 200
    } while ($true)
    # Poisoned inherited settings must not reach the installer child, and must be restored.
    $env:LANSTREAM_PORT = '8060'
    $expected = @{}
    foreach ($key in $keys) { $expected[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }

    if ($SimulateHardwareSuccess) {
        $originalTool = ${function:Invoke-LanStreamTool}
        function Invoke-LanStreamTool {
            param([string]$FilePath, [string[]]$Arguments, [string]$LogPrefix, [int]$TimeoutSeconds = 60)
            if ($Arguments -contains 'h264_qsv') {
                [IO.File]::WriteAllText($LogPrefix + '.stderr.txt', 'SIMULATED HARDWARE SUCCESS FOR TEST ONLY')
                return [pscustomobject]@{ ExitCode=0; TimedOut=$false; Seconds=0; Output=''; ErrorOutput='SIMULATED HARDWARE SUCCESS FOR TEST ONLY' }
            }
            & $originalTool @PSBoundParameters
        }
        $originalHttp = ${function:Invoke-LanStreamHttp}
        function Invoke-LanStreamHttp {
            param([Diagnostics.Process]$Process, [string]$Path, [Net.CookieContainer]$Cookies,
                [string]$FormBody, [int]$TimeoutSeconds=15, [switch]$Range)
            $response = & $originalHttp @PSBoundParameters
            if ($Path -eq '/api/transcoder-status?refresh=1') {
                $data = $response.Body | ConvertFrom-Json
                $data.backend = 'Intel QSV'
                $data.transcoder_ok = $true
                foreach ($test in $data.tests) { if ($test.key -eq 'qsv') { $test.ok = $true } }
                $response.Body = $data | ConvertTo-Json -Depth 10
            }
            return $response
        }
    }
    $password = New-Object Security.SecureString
    foreach ($character in 'Fictional integration profile 123'.ToCharArray()) { $password.AppendChar($character) }
    $inputOptions = @{ MediaRoot = @($media) }
    if ($UseConfigTemplate) {
        $templatePath = Join-Path $work 'config-template.txt'
        $templateText = "port=8061`r`nuse_master_password=false`r`nmaster_password=`r`ntranscoder=auto`r`nlow_bandwidth_resolution=1280x720`r`nlow_bandwidth_bitrate=3M`r`n<__LANSTREAM_SETUP_PASSWORD__>`r`n$media`r`n-/Private`r`n"
        [IO.File]::WriteAllText($templatePath, $templateText, (New-Object Text.UTF8Encoding $false))
        $inputOptions = @{ ConfigTemplatePath = $templatePath }
    }
    $exitCode = Invoke-LanStreamInstaller -ZipPath (Join-Path $base 'artifacts\LANStream-Enhanced-Windows-x64-test.zip') -ManifestPath (Join-Path $base 'artifacts\release-manifest.json') -InstallDirectory $target -Password $password @inputOptions
    $report = [IO.File]::ReadAllText((Join-Path $target 'acceptance-report.json')) | ConvertFrom-Json
    if ($UseConfigTemplate -and [IO.File]::ReadAllText((Join-Path $target 'config.txt')) -cne $templateText.Replace('__LANSTREAM_SETUP_PASSWORD__', 'Fictional integration profile 123')) {
        throw 'Installed config did not preserve template settings, mapping, exclusions and prompted password.'
    }
    foreach ($key in $keys) {
        if ([Environment]::GetEnvironmentVariable($key, 'Process') -cne $expected[$key]) { throw ('Caller environment was not restored: ' + $key) }
    }
    $standby.Refresh()
    if ($standby.HasExited) { throw 'Installer stopped the unrelated LANStream process.' }
    if ([IO.File]::ReadAllText($standbyConfig) -cne $standbyText) { throw 'Installer changed the unrelated configuration.' }
    $ownedId = @($report.checks | Where-Object { $_.name -eq 'Exclusive Enhanced listener on 8061' })[0].metrics.process_id
    if (-not $ownedId) { throw 'Installer did not reach owned-listener verification.' }
    $own = Get-Process -Id $ownedId -ErrorAction SilentlyContinue
    if ($SimulateHardwareSuccess) {
        if ($exitCode -ne 0 -or $report.status -ne 'PASS' -or -not $report.enhanced_left_running -or -not $own) { throw 'Simulated hardware success branch did not leave the checked application running.' }
        if ($own.Path -ine (Join-Path $target 'LANStream.exe')) { throw 'Retained process has the wrong executable path.' }
    }
    elseif ($report.status -eq 'FAIL') {
        if ($exitCode -ne 1 -or $report.enhanced_left_running -or $own) { throw 'Failed acceptance did not stop only its own process.' }
        $failed = @($report.checks | Where-Object { $_.status -eq 'FAIL' })
        if (-not $failed.Count -or @($failed | Where-Object { $_.name -notmatch 'QSV|Intel backend' }).Count) { throw 'An acceptance check other than the expected local QSV failure failed.' }
    }
    elseif ($exitCode -ne 0 -or -not $own) { throw 'Real successful acceptance returned inconsistent process state.' }
    $result = [ordered]@{ mode=$mode; installer_exit_code=$exitCode; installer_status=$report.status; unrelated_process_preserved=$true; inherited_environment_restored=$true; process_cleanup_verified=$true; checks=@($report.checks).Count; hardware_simulated=[bool]$SimulateHardwareSuccess; config_template_verified=[bool]$UseConfigTemplate }
    [IO.File]::WriteAllText((Join-Path $work 'integration-result.json'), ($result | ConvertTo-Json))
    Write-Host ($result | ConvertTo-Json -Compress)
}
finally {
    if (-not $own) {
        foreach ($listener in @(Get-NetTCPConnection -LocalPort 8061 -State Listen -ErrorAction SilentlyContinue)) {
            $candidate = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
            if ($candidate -and $candidate.Path -ieq (Join-Path $target 'LANStream.exe')) { $own = $candidate }
        }
    }
    if ($own) { $own.Refresh(); if (-not $own.HasExited) { $own.Kill(); $own.WaitForExit(5000) | Out-Null } }
    if ($standby) { $standby.Refresh(); if (-not $standby.HasExited) { $standby.Kill(); $standby.WaitForExit(5000) | Out-Null } }
    foreach ($key in $keys) { [Environment]::SetEnvironmentVariable($key, $original[$key], 'Process') }
}
