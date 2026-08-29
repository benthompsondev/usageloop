$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$sentinel = Join-Path $repoRoot '.venv\Scripts\sentinel.exe'

if (-not (Test-Path -LiteralPath $sentinel)) {
    throw 'Sentinel is not set up. Run: pwsh -NoProfile -File .\scripts\setup.ps1'
}

& $sentinel @args
exit $LASTEXITCODE
