$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot '.venv'

$pythonCommand = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($version in @('3.12', '3.13', '3.11', '3.14')) {
        & py "-$version" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = @('py', "-$version")
            break
        }
    }
}

if (-not $pythonCommand -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
    if ($LASTEXITCODE -eq 0) {
        $pythonCommand = @('python')
    }
}

if (-not $pythonCommand) {
    throw 'Python 3.11 or newer is required. No compatible local interpreter was found.'
}

if (-not (Test-Path -LiteralPath $venvPath)) {
    if ($pythonCommand.Count -eq 2) {
        & $pythonCommand[0] $pythonCommand[1] -m venv $venvPath
    }
    else {
        & $pythonCommand[0] -m venv $venvPath
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to create the local virtual environment.'
    }
}

$venvPython = Join-Path $venvPath 'Scripts\python.exe'
& $venvPython -m pip install --editable $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install Sentinel in the local virtual environment.'
}

Write-Output 'Codex Window Sentinel is ready.'
Write-Output 'Run: .\sentinel.ps1 doctor'
Write-Output 'Then: .\sentinel.ps1 sample'
Write-Output 'Inspect Phase 2 safely: .\sentinel.ps1 chain --dry-run'
