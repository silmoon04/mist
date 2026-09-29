param(
    [ValidateSet('Start','Stop','Status')][string]$Action = 'Start',
    [string]$StateDir,
    [string]$Cloudflared
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'laptop_host.py'
$python = (Get-Command python -ErrorAction Stop).Source
$stateArgs = @()
if ($PSBoundParameters.ContainsKey('StateDir')) {
    $StateDir = [System.IO.Path]::GetFullPath($StateDir)
    $stateArgs = @('--state-dir', $StateDir)
}

if ($Action -eq 'Start') {
    $existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($scriptPath) -and $_.CommandLine.Contains(' run') }
    if ($existing) {
        Write-Output 'MIST laptop host is already running.'
        exit 0
    }
    $launchArgs = @(('"' + $scriptPath + '"'), 'run')
    if ($stateArgs.Count) {
        $launchArgs += @('--state-dir', ('"' + $StateDir + '"'))
    }
    if ($PSBoundParameters.ContainsKey('Cloudflared')) {
        $Cloudflared = [System.IO.Path]::GetFullPath($Cloudflared)
        $launchArgs += @('--cloudflared', ('"' + $Cloudflared + '"'))
    }
    Start-Process -FilePath $python -ArgumentList $launchArgs -WindowStyle Hidden -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
    Write-Output 'MIST laptop host launch requested.'
} elseif ($Action -eq 'Stop') {
    & $python $scriptPath stop @stateArgs
} else {
    & $python $scriptPath status @stateArgs
}
