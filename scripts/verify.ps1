$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$sentinel = Join-Path $repoRoot '.venv\Scripts\sentinel.exe'

if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $sentinel)) {
    throw 'Run scripts\setup.ps1 before verification.'
}

Push-Location $repoRoot
try {
    & $python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }

    & $python -m compileall -q src tests
    if ($LASTEXITCODE -ne 0) { throw 'Compilation check failed.' }

    & $sentinel --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'CLI smoke test failed.' }
}
finally {
    Pop-Location
}

Write-Output 'Verification passed.'
