param(
    [ValidateSet('Start','Stop','Status')][string]$Action = 'Start'
)

$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'laptop_host.py'
$python = (Get-Command python -ErrorAction Stop).Source

if ($Action -eq 'Start') {
    $existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($scriptPath) -and $_.CommandLine.Contains(' run') }
    if ($existing) {
        Write-Output 'MIST laptop host is already running.'
        exit 0
    }
    Start-Process -FilePath $python -ArgumentList @('"' + $scriptPath + '"', 'run') -WindowStyle Hidden -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
    Write-Output 'MIST laptop host launch requested.'
} elseif ($Action -eq 'Stop') {
    & $python $scriptPath stop
} else {
    & $python $scriptPath status
}
