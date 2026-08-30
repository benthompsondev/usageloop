$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $repoRoot '.venv\Scripts\pyinstaller.exe'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pyinstaller)) {
    throw 'Run: .\.venv\Scripts\python.exe -m pip install --editable ".[build]"'
}

Push-Location $repoRoot
try {
    & $python .\scripts\render_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon rendering failed.' }

    & $pyinstaller --noconfirm --clean .\packaging\WindowSentinel.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $isccCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles} 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
    )
    $iscc = $isccCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $iscc) {
        throw 'Inno Setup compiler not found. Install the current per-user Inno Setup compiler first.'
    }
    & $iscc .\packaging\WindowSentinel.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
}
finally {
    Pop-Location
}

Write-Output "Runnable: $repoRoot\dist\WindowSentinel\WindowSentinel.exe"
Write-Output "Installer: $repoRoot\dist\WindowSentinel-Setup.exe"
