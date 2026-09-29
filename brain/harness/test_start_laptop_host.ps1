$ErrorActionPreference = 'Stop'
$launcher = Join-Path (Split-Path $PSScriptRoot -Parent) 'deploy/start_laptop_host.ps1'
$stateDir = Join-Path $env:TEMP 'mist host state with spaces'
$cloudflared = Join-Path $env:TEMP 'mist tunnel binary with spaces/cloudflared.exe'
$script:capturedLaunch = $null

function Get-CimInstance {
    param([string]$ClassName, [string]$Filter)
    return $null
}

function Start-Process {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WindowStyle,
        [string]$WorkingDirectory
    )
    $script:capturedLaunch = @{
        FilePath = $FilePath
        ArgumentList = @($ArgumentList)
        WindowStyle = $WindowStyle
        WorkingDirectory = $WorkingDirectory
    }
}

. $launcher -Action Start -StateDir $stateDir -Cloudflared $cloudflared -Protocol quic | Out-Null
if ($null -eq $script:capturedLaunch) { throw 'Start-Process was not called' }
$expected = @(
    ('"' + (Join-Path (Split-Path $launcher -Parent) 'laptop_host.py') + '"'),
    'run',
    '--state-dir',
    ('"' + [System.IO.Path]::GetFullPath($stateDir) + '"'),
    '--cloudflared',
    ('"' + [System.IO.Path]::GetFullPath($cloudflared) + '"'),
    '--protocol',
    'quic'
)
$actual = $script:capturedLaunch.ArgumentList
if ($actual.Count -ne $expected.Count) {
    throw "Expected $($expected.Count) arguments; got $($actual.Count): $($actual -join '|')"
}
for ($i = 0; $i -lt $expected.Count; $i++) {
    if ($actual[$i] -cne $expected[$i]) {
        throw "Argument $i differs: expected '$($expected[$i])', got '$($actual[$i])'"
    }
}
if ($script:capturedLaunch.WindowStyle -ne 'Hidden') { throw 'Start-Process must be hidden' }
$script:capturedLaunch = $null
. $launcher -Action Start | Out-Null
if ($null -eq $script:capturedLaunch -or $script:capturedLaunch.ArgumentList.Count -ne 2) {
    throw 'Default launch must have only script path and run arguments'
}
if ($script:capturedLaunch.ArgumentList[0] -cne $expected[0] -or
    $script:capturedLaunch.ArgumentList[1] -cne 'run') {
    throw 'Default launch argument vector differs'
}
Write-Output 'Start argument vector verified.'
