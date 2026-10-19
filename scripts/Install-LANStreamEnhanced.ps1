#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ZipPath,
    [string]$InstallDirectory = 'C:\LANStream-Enhanced',
    [string[]]$MediaRoot,
    [Security.SecureString]$Password,
    [string]$SampleVideo,
    [string]$ManifestPath,
    [string]$ConfigTemplatePath
)

function Test-LanStreamPathWithin {
    param([string]$Path, [string]$Parent)
    $child = $Path.TrimEnd('\', '/')
    $base = $Parent.TrimEnd('\', '/')
    return $child.Equals($base, [StringComparison]::OrdinalIgnoreCase) -or
        $child.StartsWith($base + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Get-LanStreamAbsolutePath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -ne $Path.Trim() -or
        $Path -match '[\x00-\x1f\x85\u2028\u2029<>|"=]' -or $Path -match '^\\\\[?.]\\' -or
        $Path -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') {
        throw 'Supply an absolute local or UNC path without config control characters.'
    }
    $full = [IO.Path]::GetFullPath($Path.Replace('/', '\'))
    foreach ($component in $full.Substring([IO.Path]::GetPathRoot($full).Length).Split('\')) {
        if ($component -match '[ .]$|[:*?]' -or $component -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') {
            throw 'Path contains an unsafe Windows component.'
        }
    }
    return $full.TrimEnd('\') + $(if ($full -match '^[A-Za-z]:\\$') { '\' } else { '' })
}

function Assert-LanStreamNoReparsePoint {
    param([string]$Path)
    $cursor = $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Reparse points in an install, media, or input path are not permitted.'
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor.TrimEnd('\'))
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Get-LanStreamPhysicalPath {
    param([string]$Path)
    # Resolve SUBST/mapped drives as well as lexical paths before comparing boundaries.
    if (-not ('LanStreamInstaller.NativePath' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace LanStreamInstaller {
 public static class NativePath {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  public static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
 }
}
'@
    }
    $existing = $Path
    $tail = New-Object 'Collections.Generic.List[string]'
    while (-not (Test-Path -LiteralPath $existing)) {
        $tail.Insert(0, [IO.Path]::GetFileName($existing))
        $existing = [IO.Path]::GetDirectoryName($existing)
        if (-not $existing) { throw 'Path has no accessible parent.' }
    }
    $handle = [LanStreamInstaller.NativePath]::CreateFile($existing, 0, 7, [IntPtr]::Zero, 3, 0x02000000, [IntPtr]::Zero)
    try {
        if ($handle.IsInvalid) { throw 'Cannot resolve the physical path safely.' }
        $buffer = New-Object Text.StringBuilder 32768
        $length = [LanStreamInstaller.NativePath]::GetFinalPathNameByHandle($handle, $buffer, $buffer.Capacity, 0)
        if ($length -eq 0 -or $length -ge $buffer.Capacity) { throw 'Cannot resolve the physical path safely.' }
        $physical = $buffer.ToString()
        if ($physical.StartsWith('\\?\UNC\')) { $physical = '\\' + $physical.Substring(8) }
        elseif ($physical.StartsWith('\\?\')) { $physical = $physical.Substring(4) }
        foreach ($part in $tail) { $physical = Join-Path $physical $part }
        return Get-LanStreamAbsolutePath $physical
    }
    finally { $handle.Dispose() }
}

function Read-LanStreamConfigTemplate {
    param([string]$Path)
    $file = Get-LanStreamAbsolutePath $Path
    if (Test-LanStreamPathWithin $file 'C:\LANStream') { throw 'The existing LANStream configuration is protected.' }
    Assert-LanStreamNoReparsePoint $file
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Config template was not found.' }
    $physical = Get-LanStreamPhysicalPath $file
    if ($physical -notmatch '^[A-Za-z]:\\' -or (Test-LanStreamPathWithin $physical (Get-LanStreamPhysicalPath 'C:\LANStream'))) {
        throw 'Config template must resolve to a local file outside the existing LANStream installation.'
    }
    $text = [Text.UTF8Encoding]::new($false, $true).GetString([IO.File]::ReadAllBytes($file)).TrimStart([char]0xfeff)
    $token = '__LANSTREAM_SETUP_PASSWORD__'
    if ([regex]::Matches($text, [regex]::Escape($token)).Count -ne 1 -or $text -match '[\x00-\x08\x0b\x0c\x0e-\x1f\x85\u2028\u2029]') {
        throw 'Config template must contain exactly one setup-password token and no unsupported control characters.'
    }
    $settings = @{}
    $roots = @()
    $profile = $false
    $known = @('port', 'use_master_password', 'master_password', 'transcoder', 'directory_cache_ttl', 'directory_scan_timeout', 'thumbnail_workers', 'low_bandwidth_resolution', 'low_bandwidth_bitrate')
    foreach ($raw in ($text -split '\r\n|\n|\r')) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('<') -or $line.EndsWith('>')) {
            if ($profile -or $line -cne ('<' + $token + '>')) { throw 'Config template must have exactly one setup-password profile.' }
            $profile = $true
        }
        elseif ($line.StartsWith('-/')) {
            if (-not $profile -or $roots.Count -eq 0 -or $line.Length -le 2) { throw 'A template exclusion must follow a media root.' }
        }
        elseif ($line.Contains('=')) {
            if ($profile) { throw 'All template settings must appear before the password profile.' }
            $key, $value = $line -split '=', 2
            $key = $key.Trim().ToLowerInvariant()
            $value = $value.Trim()
            if ($known -notcontains $key -or $settings.ContainsKey($key)) { throw 'Config template has an unsupported or duplicate setting.' }
            if ($key -in @('directory_cache_ttl', 'directory_scan_timeout', 'thumbnail_workers')) {
                $number = 0
                $maximum = @{ directory_cache_ttl = 86400; directory_scan_timeout = 300; thumbnail_workers = 8 }[$key]
                if (-not [int]::TryParse($value, [ref]$number) -or $number -lt 1 -or $number -gt $maximum) { throw 'A template worker or directory timing setting is outside its supported range.' }
            }
            if (($key -eq 'low_bandwidth_resolution' -and $value -notmatch '^[1-9]\d*x[1-9]\d*$') -or
                ($key -eq 'low_bandwidth_bitrate' -and $value -notmatch '^[1-9]\d*(?:\.\d+)?[kKmM]?$')) { throw 'A template Low Bandwidth setting has an invalid format.' }
            $settings[$key] = $value
        }
        else {
            if (-not $profile) { throw 'Template media roots must follow the password profile.' }
            $roots += Get-LanStreamAbsolutePath $line
        }
    }
    if (-not $profile -or $roots.Count -eq 0 -or $settings['port'] -ne '8061' -or
        $settings['use_master_password'] -ne 'false' -or -not $settings.ContainsKey('master_password') -or
        $settings['master_password'] -ne '' -or $settings['transcoder'] -ne 'auto') {
        throw 'Config template requires media roots, port=8061, use_master_password=false, empty master_password, and transcoder=auto.'
    }
    return [pscustomobject]@{ Text = $text; MediaRoot = $roots }
}

function Assert-LanStreamInstallPaths {
    param([string]$InstallDirectory, [string[]]$MediaRoot, [string]$SampleVideo)
    $target = Get-LanStreamAbsolutePath $InstallDirectory
    if (Test-LanStreamPathWithin $target 'C:\LANStream') { throw 'The existing LANStream installation is protected.' }
    if ($target -notmatch '^[A-Za-z]:\\' -or [IO.Path]::GetPathRoot($target) -eq $target) {
        throw 'The new install must be a local directory below a drive root.'
    }
    Assert-LanStreamNoReparsePoint $target
    if (Test-Path -LiteralPath $target) { throw 'Install directory already exists; no files were changed.' }
    $parent = [IO.Path]::GetDirectoryName($target)
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Install parent directory must already exist.' }
    $physicalTarget = Get-LanStreamPhysicalPath $target
    if ($physicalTarget -notmatch '^[A-Za-z]:\\') { throw 'The install must resolve to a local disk, not a mapped network share.' }
    $production = Get-LanStreamPhysicalPath 'C:\LANStream'
    if ((Test-LanStreamPathWithin $target 'C:\LANStream') -or
        (Test-LanStreamPathWithin $physicalTarget $production)) {
        throw 'The existing LANStream installation is protected.'
    }
    if (-not $MediaRoot -or $MediaRoot.Count -eq 0) { throw 'Pass at least one explicit -MediaRoot.' }
    $roots = @()
    foreach ($entry in $MediaRoot) {
        $root = Get-LanStreamAbsolutePath $entry
        if ((Test-LanStreamPathWithin $root 'C:\LANStream') -or (Test-LanStreamPathWithin 'C:\LANStream' $root) -or
            (Test-LanStreamPathWithin $root $target) -or (Test-LanStreamPathWithin $target $root)) {
            throw 'A media root overlaps the new install or protected production directory.'
        }
        Assert-LanStreamNoReparsePoint $root
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'A media root is not an accessible directory.' }
        $physical = Get-LanStreamPhysicalPath $root
        foreach ($pair in @(@($root, $target), @($root, 'C:\LANStream'), @($physical, $physicalTarget), @($physical, $production))) {
            if ((Test-LanStreamPathWithin $pair[0] $pair[1]) -or (Test-LanStreamPathWithin $pair[1] $pair[0])) {
                throw 'A media root overlaps the new install or protected production directory.'
            }
        }
        # Local SMB aliases could hide C:\LANStream behind a share name; require a local path instead.
        if ($physical.StartsWith('\\')) {
            $server = $physical.Substring(2).Split('\')[0]
            $localAddresses = @([Net.Dns]::GetHostAddresses([Net.Dns]::GetHostName()) | ForEach-Object { $_.ToString() })
            $addresses = @([Net.Dns]::GetHostAddresses($server))
            if ($server -ieq $env:COMPUTERNAME -or $server -ieq 'localhost' -or
                @($addresses | Where-Object { [Net.IPAddress]::IsLoopback($_) -or $localAddresses -contains $_.ToString() }).Count) {
                throw 'Use a local path for shares hosted by this computer so protected-path checks remain reliable.'
            }
        }
        if ($roots -contains $root) { throw 'Duplicate media roots are not permitted.' }
        $roots += $root
    }
    $sample = $null
    if ($SampleVideo) {
        $sample = Get-LanStreamAbsolutePath $SampleVideo
        Assert-LanStreamNoReparsePoint $sample
        if (-not (Test-Path -LiteralPath $sample -PathType Leaf) -or
            -not @($roots | Where-Object { Test-LanStreamPathWithin $sample $_ }).Count) {
            throw 'SampleVideo must be an existing file inside an explicit media root.'
        }
    }
    return [pscustomobject]@{ InstallDirectory = $target; MediaRoot = $roots; SampleVideo = $sample }
}

function Assert-LanStreamZipEntries {
    param($Archive)
    $names = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $Archive.Entries) {
        $name = $entry.FullName
        if ($name -cnotmatch '^LANStream-Enhanced/' -or $name.Contains('\') -or $name -match '[\x00-\x1f:*?<>|"=]' -or
            (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000 -or
            ($entry.ExternalAttributes -band 0x400) -ne 0) { throw 'ZIP contains an unsafe entry.' }
        $parts = $name.TrimEnd('/').Split('/')
        foreach ($part in $parts) {
            if (-not $part -or $part -eq '.' -or $part -eq '..' -or $part -match '[ .]$' -or
                $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)') { throw 'ZIP contains an unsafe path component.' }
        }
        if (-not $names.Add($name.TrimEnd('/'))) { throw 'ZIP contains duplicate Windows paths.' }
    }
    foreach ($required in @('LANStream.exe', 'ffmpeg.exe', 'ffprobe.exe', '7z.exe', '7z.dll')) {
        if (-not $names.Contains('LANStream-Enhanced/' + $required)) { throw 'ZIP is missing a required portable-package component.' }
    }
    if (-not @($Archive.Entries | Where-Object { $_.FullName.StartsWith('LANStream-Enhanced/_internal/') -and $_.Length -gt 0 }).Count) {
        throw 'ZIP is missing its portable application runtime.'
    }
    $files = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $Archive.Entries) { if (-not $entry.FullName.EndsWith('/')) { $files.Add($entry.FullName) | Out-Null } }
    foreach ($entry in $Archive.Entries) {
        $parent = $entry.FullName.TrimEnd('/')
        while ($parent.Contains('/')) {
            $parent = $parent.Substring(0, $parent.LastIndexOf('/'))
            if ($files.Contains($parent)) { throw 'ZIP file and directory paths collide.' }
        }
    }
}

function Get-LanStreamPercentile95 {
    param([double[]]$Values)
    if (-not $Values.Count) { throw 'No latency samples were collected.' }
    $ordered = @($Values | Sort-Object)
    return [math]::Round($ordered[[math]::Ceiling($ordered.Count * 0.95) - 1], 2)
}

function Invoke-LanStreamTool {
    param([string]$FilePath, [string[]]$Arguments, [string]$LogPrefix, [int]$TimeoutSeconds = 60)
    # ProcessStartInfo needs Windows CRT quoting for a path that may contain spaces.
    $quoted = foreach ($argument in $Arguments) {
        '"' + [regex]::Replace([regex]::Replace($argument, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $quoted -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        if (-not $process.Start()) { throw 'Bundled tool did not start.' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut -and -not $process.HasExited) { $process.Kill(); $process.WaitForExit(5000) | Out-Null }
        if (-not $process.HasExited) { throw 'Self-started bundled tool could not be stopped.' }
        $out = $stdout.GetAwaiter().GetResult()
        $err = $stderr.GetAwaiter().GetResult()
        [IO.File]::WriteAllText($LogPrefix + '.stdout.txt', $out)
        [IO.File]::WriteAllText($LogPrefix + '.stderr.txt', $err)
        return [pscustomobject]@{ ExitCode = $process.ExitCode; TimedOut = $timedOut; Seconds = [math]::Round($timer.Elapsed.TotalSeconds, 3); Output = $out; ErrorOutput = $err }
    }
    finally { $process.Dispose() }
}

function Assert-LanStreamListener {
    param([Diagnostics.Process]$Process)
    $Process.Refresh()
    if ($Process.HasExited) { throw 'The self-started Enhanced process exited.' }
    $listeners = @(Get-NetTCPConnection -LocalPort 8061 -State Listen -ErrorAction Stop)
    if (-not $listeners.Count -or @($listeners | Where-Object { $_.OwningProcess -ne $Process.Id }).Count) {
        throw 'Port 8061 is not owned exclusively by the self-started Enhanced process.'
    }
}

function Invoke-LanStreamHttp {
    param([Diagnostics.Process]$Process, [string]$Path, [Net.CookieContainer]$Cookies,
        [string]$FormBody, [int]$TimeoutSeconds = 15, [switch]$Range)
    Assert-LanStreamListener $Process
    $request = [Net.HttpWebRequest]::Create('http://127.0.0.1:8061' + $Path)
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = $TimeoutSeconds * 1000
    $request.ReadWriteTimeout = $TimeoutSeconds * 1000
    $request.CookieContainer = $Cookies
    if ($Range) { $request.AddRange(0, 15) }
    if ($FormBody) {
        $request.Method = 'POST'
        $request.ContentType = 'application/x-www-form-urlencoded'
        $bytes = [Text.Encoding]::UTF8.GetBytes($FormBody)
        $request.ContentLength = $bytes.Length
        $upload = $request.GetRequestStream()
        try { $upload.Write($bytes, 0, $bytes.Length) } finally { $upload.Dispose(); [Array]::Clear($bytes, 0, $bytes.Length) }
    }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $response = $null
    try {
        try { $response = $request.GetResponse() }
        catch [Net.WebException] { if ($_.Exception.Response) { $response = $_.Exception.Response } else { throw 'Enhanced HTTP request failed or timed out.' } }
        $stream = $response.GetResponseStream()
        try {
            if ($Range) {
                $buffer = New-Object byte[] 16
                $count = 0
                while ($count -lt 16) {
                    $remaining = $TimeoutSeconds * 1000 - $timer.ElapsedMilliseconds
                    if ($remaining -le 0) { throw 'Enhanced HTTP response exceeded its total time bound.' }
                    if ($stream.CanTimeout) { $stream.ReadTimeout = [int]$remaining }
                    $read = $stream.Read($buffer, $count, 16 - $count)
                    if ($read -eq 0) { break }
                    $count += $read
                }
                $body = ''
            }
            else {
                $memory = New-Object IO.MemoryStream
                try {
                    $buffer = New-Object byte[] 8192
                    while ($true) {
                        $remaining = $TimeoutSeconds * 1000 - $timer.ElapsedMilliseconds
                        if ($remaining -le 0) { throw 'Enhanced HTTP response exceeded its total time bound.' }
                        if ($stream.CanTimeout) { $stream.ReadTimeout = [int]$remaining }
                        $read = $stream.Read($buffer, 0, $buffer.Length)
                        if ($read -eq 0) { break }
                        if ($memory.Length + $read -gt 4194304) { throw 'Enhanced response exceeded the acceptance-check size bound.' }
                        $memory.Write($buffer, 0, $read)
                    }
                    $body = [Text.Encoding]::UTF8.GetString($memory.ToArray())
                }
                finally { $memory.Dispose() }
                $count = 0
            }
        }
        finally { $stream.Dispose() }
        return [pscustomobject]@{ Status = [int]$response.StatusCode; Body = $body; Milliseconds = $timer.Elapsed.TotalMilliseconds; BytesRead = $count; ContentRange = $response.Headers['Content-Range'] }
    }
    finally { if ($response) { $response.Dispose() }; $request.Abort() }
}

function Assert-LanStreamListing {
    param($Listing)
    if ($null -eq $Listing.items -or $Listing.error -or $Listing.state -in @('error', 'offline') -or @($Listing.items).Count -gt 100) {
        throw 'Listing failed, is offline, or exceeded the 100-item page bound.'
    }
    if ($Listing.state -ne 'ready' -or $Listing.refreshing) { throw 'Listing is still loading or refreshing; it cannot be a warm sample.' }
}

function Invoke-LanStreamInstaller {
    [CmdletBinding()]
    param([string]$ZipPath, [string]$InstallDirectory = 'C:\LANStream-Enhanced', [string[]]$MediaRoot,
        [Security.SecureString]$Password, [string]$SampleVideo, [string]$ManifestPath, [string]$ConfigTemplatePath)
    $ErrorActionPreference = 'Stop'
    $stage = $null
    $installed = $false
    $process = $null
    $zipLock = $null
    $plain = $null
    $form = $null
    $checks = New-Object 'Collections.Generic.List[object]'
    $report = [ordered]@{ schema = 1; started_utc = [DateTime]::UtcNow.ToString('o'); status = 'FAIL'; archive = ''; revision = ''; sha256 = ''; port = 8061; media_root_count = 0; checks = $checks; manual_pending = @('Browser HEVC playback; audio and subtitle selection; seeking; Low Bandwidth', 'Physical Safari and Teleport clients over LAN/VPN', 'Household profile permissions and exclusions; favorites after restart; media-share outage/recovery', 'Observe N100 CPU, RAM, GPU video-engine utilization, and network throughput during representative playback'); private_files = 'config.txt contains the profile password and media paths. acceptance-logs and application logs may contain private media paths. Do not share them.' }
    $addCheck = {
        param([string]$Name, [string]$Status, $Metrics)
        $checks.Add([pscustomobject]@{ name = $Name; status = $Status; metrics = $Metrics })
        Write-Host ($Status + ': ' + $Name)
    }
    try {
        $template = $null
        if ($ConfigTemplatePath) {
            if ($PSBoundParameters.ContainsKey('MediaRoot')) { throw 'Use ConfigTemplatePath or MediaRoot, not both.' }
            $template = Read-LanStreamConfigTemplate $ConfigTemplatePath
            $MediaRoot = $template.MediaRoot
        }
        $paths = Assert-LanStreamInstallPaths -InstallDirectory $InstallDirectory -MediaRoot $MediaRoot -SampleVideo $SampleVideo
        $InstallDirectory = $paths.InstallDirectory
        $MediaRoot = $paths.MediaRoot
        $SampleVideo = $paths.SampleVideo
        $report.media_root_count = $MediaRoot.Count
        if (@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object { $_.Port -eq 8061 }).Count) {
            throw 'Port 8061 is already in use. No process was stopped and no files were changed.'
        }
        $zip = Get-LanStreamAbsolutePath $ZipPath
        Assert-LanStreamNoReparsePoint $zip
        if (-not (Test-Path -LiteralPath $zip -PathType Leaf)) { throw 'ZIP was not found.' }
        if (-not $ManifestPath) { $ManifestPath = Join-Path ([IO.Path]::GetDirectoryName($zip)) 'release-manifest.json' }
        $manifestFile = Get-LanStreamAbsolutePath $ManifestPath
        Assert-LanStreamNoReparsePoint $manifestFile
        $manifest = [IO.File]::ReadAllText($manifestFile) | ConvertFrom-Json
        if ($manifest.archive -isnot [string] -or $manifest.sha256 -isnot [string] -or $manifest.revision -isnot [string] -or
            $manifest.archive -cne [IO.Path]::GetFileName($zip) -or $manifest.sha256 -notmatch '^[A-Fa-f0-9]{64}$' -or
            [string]::IsNullOrWhiteSpace($manifest.revision) -or $manifest.revision -match '[\x00-\x1f]') { throw 'Release manifest is invalid or does not identify this ZIP.' }
        $zipLock = [IO.File]::Open($zip, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $hash = [BitConverter]::ToString($hasher.ComputeHash($zipLock)).Replace('-', '') }
        finally { $hasher.Dispose() }
        if ($hash -ine $manifest.sha256) { throw 'ZIP SHA256 does not match the release manifest. No files were extracted.' }
        $report.archive = $manifest.archive
        $report.sha256 = $hash.ToLowerInvariant()
        $report.revision = $manifest.revision
        & $addCheck 'Release ZIP SHA256' 'PASS' $null
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [IO.Compression.ZipFile]::OpenRead($zip)
        try { Assert-LanStreamZipEntries $archive } finally { $archive.Dispose() }
        if (-not $Password) { $Password = Read-Host 'Choose a password for the isolated Enhanced test profile' -AsSecureString }
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
        try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain -ne $plain.Trim() -or $plain -match '[\x00-\x1f\x85\u2028\u2029<>]') {
            throw 'Password must be nonempty and have no outer whitespace, line breaks, control characters, or angle brackets.'
        }
        $parent = [IO.Path]::GetDirectoryName($InstallDirectory)
        $stage = Join-Path $parent ('.LANStream-Enhanced-install-' + [Guid]::NewGuid().ToString('N'))
        Assert-LanStreamNoReparsePoint $parent
        if (Test-Path -LiteralPath $InstallDirectory) { throw 'Install directory appeared during preflight; refusing overwrite.' }
        [IO.Directory]::CreateDirectory($stage) | Out-Null
        [IO.Compression.ZipFile]::ExtractToDirectory($zip, $stage)
        $stagedApp = Join-Path $stage 'LANStream-Enhanced'
        Assert-LanStreamNoReparsePoint $stagedApp
        [IO.Directory]::Move($stagedApp, $InstallDirectory)
        $installed = $true
        $zipLock.Dispose()
        $zipLock = $null
        [IO.Directory]::Delete($stage)
        $stage = $null
        $logs = Join-Path $InstallDirectory 'acceptance-logs'
        [IO.Directory]::CreateDirectory($logs) | Out-Null
        [IO.Directory]::CreateDirectory((Join-Path $InstallDirectory 'cache')) | Out-Null
        $config = "# Isolated Enhanced acceptance profile. Private: contains password and media paths.`r`nport=8061`r`nuse_master_password=false`r`nmaster_password=`r`ntranscoder=auto`r`ndirectory_cache_ttl=300`r`ndirectory_scan_timeout=30`r`nthumbnail_workers=2`r`nlow_bandwidth_resolution=854x480`r`nlow_bandwidth_bitrate=2M`r`n`r`n<" + $plain + ">`r`n" + ($MediaRoot -join "`r`n") + "`r`n"
        if ($template) { $config = $template.Text.Replace('__LANSTREAM_SETUP_PASSWORD__', $plain) }
        [IO.File]::WriteAllText((Join-Path $InstallDirectory 'config.txt'), $config, (New-Object Text.UTF8Encoding $false))
        $config = $null
        foreach ($tool in @('ffmpeg', 'ffprobe', '7z')) {
            $arguments = @('-version')
            if ($tool -eq '7z') { $arguments = @('i') }
            $version = Invoke-LanStreamTool -FilePath (Join-Path $InstallDirectory ($tool + '.exe')) -Arguments $arguments -LogPrefix (Join-Path $logs ($tool + '-version')) -TimeoutSeconds 15
            if ($version.TimedOut -or $version.ExitCode -ne 0) { throw 'A required bundled tool failed its version check.' }
            $firstLine = @(($version.Output + "`n" + $version.ErrorOutput).Split("`n") | Where-Object { $_.Trim() })[0].Trim()
            & $addCheck ($tool + ' bundled version') 'PASS' @{ version = $firstLine }
        }
        $executable = Join-Path $InstallDirectory 'LANStream.exe'
        $identityStream = [IO.File]::OpenRead($executable)
        $identityHasher = [Security.Cryptography.SHA256]::Create()
        try { $identityHash = [BitConverter]::ToString($identityHasher.ComputeHash($identityStream)).Replace('-', '').ToLowerInvariant() }
        finally { $identityHasher.Dispose(); $identityStream.Dispose() }
        & $addCheck 'Portable application identity' 'PASS' @{ sha256 = $identityHash; file_version = [Diagnostics.FileVersionInfo]::GetVersionInfo($executable).FileVersion }
        $environment = @{ LANSTREAM_CONFIG_FILE = (Join-Path $InstallDirectory 'config.txt'); LANSTREAM_CACHE_DIR = (Join-Path $InstallDirectory 'cache'); LANSTREAM_PORT = '8061'; LANSTREAM_HOST = '0.0.0.0'; LANSTREAM_TRANSCODER = 'auto'; LANSTREAM_PASSWORD = $null; LANSTREAM_USE_MASTER_PASSWORD = 'false'; LANSTREAM_SESSION_SECRET = ([Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')) }
        $saved = @{}
        try {
            foreach ($key in $environment.Keys) { $saved[$key] = [Environment]::GetEnvironmentVariable($key, 'Process'); [Environment]::SetEnvironmentVariable($key, $environment[$key], 'Process') }
            if (@([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() | Where-Object { $_.Port -eq 8061 }).Count) { throw 'Port 8061 became occupied before launch.' }
            $process = Start-Process -FilePath $executable -WorkingDirectory $InstallDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'application.stdout.txt') -RedirectStandardError (Join-Path $logs 'application.stderr.txt')
        }
        finally { foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key], 'Process') } }
        $ready = [Diagnostics.Stopwatch]::StartNew()
        do {
            $process.Refresh()
            if ($process.HasExited) { throw 'Enhanced exited during startup; inspect private application logs.' }
            try { Assert-LanStreamListener $process; break } catch { if ($ready.Elapsed.TotalSeconds -ge 45) { throw 'Enhanced did not acquire port 8061 within 45 seconds.' }; Start-Sleep -Milliseconds 300 }
        } while ($true)
        & $addCheck 'Exclusive Enhanced listener on 8061' 'PASS' @{ process_id = $process.Id }
        $anonymous = New-Object Net.CookieContainer
        $response = Invoke-LanStreamHttp -Process $process -Path '/api/browse/0/' -Cookies $anonymous
        if ($response.Status -ne 401) { throw 'Unauthenticated API access was not denied.' }
        & $addCheck 'Unauthenticated API denied' 'PASS' @{ http_status = $response.Status }
        $cookies = New-Object Net.CookieContainer
        $form = 'password=' + [Uri]::EscapeDataString($plain)
        $login = Invoke-LanStreamHttp -Process $process -Path '/login' -Cookies $cookies -FormBody $form
        $form = $null
        $plain = $null
        if ($login.Status -ne 302) { throw 'Test profile login failed.' }
        foreach ($url in @('/', '/static/style.css', '/static/explorer.js', '/browse/0/')) {
            $response = Invoke-LanStreamHttp -Process $process -Path $url -Cookies $cookies
            if ($response.Status -ne 200 -or -not $response.Body) { throw 'Home or a required static asset failed.' }
            if ($url -eq '/browse/0/' -and $response.Body -notmatch 'id=["'']fileType["'']') { throw 'The installed browse page lacks the new File type selector.' }
        }
        & $addCheck 'Authenticated home and static assets' 'PASS' $null
        $firstVideo = $null
        for ($index = 0; $index -lt $MediaRoot.Count; $index++) {
            $url = '/api/browse/' + $index + '/'
            $cold = [Diagnostics.Stopwatch]::StartNew()
            do {
                if ($cold.Elapsed.TotalSeconds -ge 60) { throw 'Cold browse did not finish within 60 seconds.' }
                $response = Invoke-LanStreamHttp -Process $process -Path $url -Cookies $cookies -TimeoutSeconds ([math]::Max(1, [math]::Min(15, [math]::Ceiling(60 - $cold.Elapsed.TotalSeconds))))
                if ($response.Status -ne 200) { throw 'Browse endpoint did not return HTTP 200.' }
                $listing = $response.Body | ConvertFrom-Json
                if ($null -eq $listing.items -or $listing.error -or $listing.state -in @('error', 'offline') -or @($listing.items).Count -gt 100) { throw 'Cold browse failed or returned more than 100 items.' }
                if ($cold.Elapsed.TotalSeconds -ge 60) { throw 'Cold browse did not finish within 60 seconds.' }
                if ($listing.state -eq 'ready' -and -not $listing.refreshing) { break }
                Start-Sleep -Milliseconds 250
            } while ($true)
            & $addCheck ('Root ' + $index + ' cold browse complete') 'PASS' @{ milliseconds = [math]::Round($cold.Elapsed.TotalMilliseconds, 2); returned_items = @($listing.items).Count; total_items = $listing.total }
            if (-not $firstVideo) { $firstVideo = @($listing.items | Where-Object { $_.kind -eq 'video' } | Select-Object -First 1) | Select-Object -First 1 }
            $latencies = @()
            for ($run = 0; $run -lt 20; $run++) {
                $response = Invoke-LanStreamHttp -Process $process -Path $url -Cookies $cookies
                if ($response.Status -ne 200) { throw 'Warm browse request failed.' }
                Assert-LanStreamListing ($response.Body | ConvertFrom-Json)
                $latencies += $response.Milliseconds
            }
            $p95 = Get-LanStreamPercentile95 $latencies
            & $addCheck ('Root ' + $index + ' warm browse p95') $(if ($p95 -le 500) { 'PASS' } else { 'FAIL' }) @{ requests = 20; milliseconds = $p95; target_ms = 500 }
            foreach ($category in @('videos', 'pictures', 'documents')) {
                $response = Invoke-LanStreamHttp -Process $process -Path ($url + '?file_type=' + $category) -Cookies $cookies
                if ($response.Status -ne 200) { throw 'A new File type API filter failed.' }
                $filtered = $response.Body | ConvertFrom-Json
                Assert-LanStreamListing $filtered
                $allowed = @{ videos = @('folder', 'video'); pictures = @('folder', 'image'); documents = @('folder', 'pdf', 'comic', 'text') }
                if (@($filtered.items | Where-Object { $allowed[$category] -notcontains $_.kind }).Count) { throw 'A File type API filter returned an incorrect item kind.' }
            }
            $invalid = Invoke-LanStreamHttp -Process $process -Path ($url + '?file_type=invalid') -Cookies $cookies
            if ($invalid.Status -ne 400) { throw 'An invalid File type filter was not rejected.' }
            & $addCheck ('Root ' + $index + ' new File type filters') 'PASS' @{ categories = @('videos', 'pictures', 'documents'); invalid_http_status = 400 }
        }
        $latencies = @()
        for ($run = 0; $run -lt 20; $run++) {
            $response = Invoke-LanStreamHttp -Process $process -Path '/api/search?scope=all&q=a' -Cookies $cookies
            if ($response.Status -ne 200) { throw 'Global filename search request failed.' }
            $search = $response.Body | ConvertFrom-Json
            Assert-LanStreamListing $search
            $latencies += $response.Milliseconds
        }
        & $addCheck 'Global filename search coverage' 'PASS' @{ scope = 'Partial background library index; automatic discovery may still be in progress'; roots = $MediaRoot.Count }
        $p95 = Get-LanStreamPercentile95 $latencies
        & $addCheck 'Global filename search p95' $(if ($p95 -le 300) { 'PASS' } else { 'FAIL' }) @{ requests = 20; milliseconds = $p95; target_ms = 300 }
        if ($firstVideo) {
            $encoded = @($firstVideo.rel.Split('/') | ForEach-Object { [Uri]::EscapeDataString($_) }) -join '/'
            $response = Invoke-LanStreamHttp -Process $process -Path ('/media/' + $firstVideo.root_index + '/' + $encoded) -Cookies $cookies -Range
            if ($response.Status -ne 206 -or $response.BytesRead -ne 16 -or $response.ContentRange -notmatch '^bytes 0-15/\d+$') { throw 'The bounded direct-video Range check failed.' }
            & $addCheck 'Direct video Range bytes 0-15' 'PASS' @{ bytes_read = $response.BytesRead }
        }
        else { & $addCheck 'Direct video Range bytes 0-15' 'SKIP' @{ reason = 'No video in the first page of the explicitly supplied roots' } }
        $qsv = Invoke-LanStreamTool -FilePath (Join-Path $InstallDirectory 'ffmpeg.exe') -Arguments @('-nostdin', '-hide_banner', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30', '-t', '5', '-vf', 'format=nv12', '-an', '-c:v', 'h264_qsv', '-f', 'null', 'NUL') -LogPrefix (Join-Path $logs 'qsv-synthetic') -TimeoutSeconds 60
        & $addCheck 'Real Intel QSV 5-second 720p30 synthetic encode' $(if (-not $qsv.TimedOut -and $qsv.ExitCode -eq 0) { 'PASS' } else { 'FAIL' }) @{ seconds = $qsv.Seconds; exit_code = $qsv.ExitCode; timed_out = $qsv.TimedOut }
        $response = Invoke-LanStreamHttp -Process $process -Path '/api/transcoder-status?refresh=1' -Cookies $cookies -TimeoutSeconds 100
        if ($response.Status -ne 200) { throw 'Application transcoder diagnostics failed.' }
        $status = $response.Body | ConvertFrom-Json
        $qsvTest = @($status.tests | Where-Object { $_.key -eq 'qsv' })
        & $addCheck 'Application real QSV test and selected Intel backend' $(if ($status.transcoder_ok -and $status.backend -eq 'Intel QSV' -and $status.preference -eq 'auto' -and $qsvTest.Count -eq 1 -and $qsvTest[0].ok) { 'PASS' } else { 'FAIL' }) @{ selected_backend = $status.backend; tests = @($status.tests | ForEach-Object { @{ key = $_.key; ok = [bool]$_.ok } }); note = 'NVIDIA/AMD test failures are expected on an Intel-only N100; QSV is required' }
        [IO.File]::WriteAllText((Join-Path $logs 'transcoder-status.private.json'), $response.Body)
        if ($SampleVideo) {
            $sample = Invoke-LanStreamTool -FilePath (Join-Path $InstallDirectory 'ffmpeg.exe') -Arguments @('-nostdin', '-hide_banner', '-i', $SampleVideo, '-t', '60', '-map', '0:v:0', '-vf', 'scale=1280:720,format=nv12', '-an', '-c:v', 'h264_qsv', '-f', 'null', 'NUL') -LogPrefix (Join-Path $logs 'qsv-sample') -TimeoutSeconds 180
            $speed = @([regex]::Matches($sample.ErrorOutput, 'speed=\s*([0-9.]+)x') | ForEach-Object { $_.Groups[1].Value }) | Select-Object -Last 1
            & $addCheck 'Optional sample video QSV encode to null' $(if (-not $sample.TimedOut -and $sample.ExitCode -eq 0) { 'PASS' } else { 'FAIL' }) @{ seconds = $sample.Seconds; exit_code = $sample.ExitCode; timed_out = $sample.TimedOut; final_speed_x = $speed; input_window_seconds = 60 }
        }
        else { & $addCheck 'Optional sample video QSV encode to null' 'SKIP' @{ reason = 'No -SampleVideo supplied' } }
        Assert-LanStreamListener $process
        if (@($checks | Where-Object { $_.status -eq 'FAIL' }).Count -eq 0) { $report.status = 'PASS' }
    }
    catch {
        $reason = $_.Exception.Message
        foreach ($private in @($plain, $form, $SampleVideo) + @($MediaRoot)) {
            if ($private) { $reason = $reason.Replace($private, '[redacted]') }
        }
        & $addCheck 'Installer or acceptance execution' 'FAIL' @{ reason = $reason }
        Write-Warning $reason
        foreach ($requiredCheck in @('Real Intel QSV 5-second 720p30 synthetic encode', 'Application real QSV test and selected Intel backend')) {
            if (-not @($checks | Where-Object { $_.name -eq $requiredCheck }).Count) { & $addCheck $requiredCheck 'SKIP' @{ reason = 'Acceptance aborted before this required hardware check; overall result is FAIL' } }
        }
    }
    finally {
        $plain = $null
        $form = $null
        if ($zipLock) { $zipLock.Dispose() }
        if ($process -and $report.status -ne 'PASS') {
            $process.Refresh()
            if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(5000) | Out-Null }
        }
        if ($installed) {
            $report.completed_utc = [DateTime]::UtcNow.ToString('o')
            $report.enhanced_left_running = $report.status -eq 'PASS'
            try {
                [IO.File]::WriteAllText((Join-Path $InstallDirectory 'acceptance-report.json'), ($report | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding $false))
                Write-Host ('Acceptance report: ' + (Join-Path $InstallDirectory 'acceptance-report.json'))
            }
            catch {
                $report.status = 'FAIL'
                if ($process) {
                    $process.Refresh()
                    if (-not $process.HasExited) { $process.Kill(); $process.WaitForExit(5000) | Out-Null }
                }
                Write-Warning 'The acceptance report could not be saved. The self-started Enhanced process was stopped.'
            }
            Write-Host $report.private_files
        }
        if ($stage -and (Test-Path -LiteralPath $stage)) {
            # A failed extraction stays at its unique staging path for diagnosis; never delete arbitrary trees.
            Write-Warning ('Partial extraction retained at ' + $stage)
        }
    }
    if ($report.status -eq 'PASS') {
        Write-Host 'Automated checks PASS. Enhanced is running on port 8061; manual playback/client checks remain pending.'
        return 0
    }
    Write-Warning 'Acceptance FAIL. The self-started Enhanced process has been stopped; the existing installation was not controlled.'
    return 1
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $ZipPath) { $ZipPath = Join-Path $PSScriptRoot 'LANStream-Enhanced-Windows-x64-test.zip' }
    $PSBoundParameters['ZipPath'] = $ZipPath
    exit (Invoke-LanStreamInstaller @PSBoundParameters)
}
