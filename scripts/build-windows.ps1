$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $repoRoot '.venv\Scripts\pyinstaller.exe'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pyinstaller)) {
    throw 'Run: .\.venv\Scripts\python.exe -m pip install --editable ".[build]"'
}

Push-Location $repoRoot
try {
    $product = (& $python -c 'import json; from dataclasses import asdict; from sentinel.product import PRODUCT; print(json.dumps(asdict(PRODUCT)))') | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Product metadata could not be read.' }

    & $python .\scripts\render_version_info.py
    if ($LASTEXITCODE -ne 0) { throw 'Version metadata rendering failed.' }

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
    $installerBaseName = [IO.Path]::GetFileNameWithoutExtension($product.installer_filename)
    & $iscc "/DAppName=$($product.display_name)" "/DAppVersion=$($product.version)" "/DAppExeName=$($product.executable_name)" "/DAppPublisher=$($product.publisher)" "/DAppId=$($product.app_id)" "/DInstallerBaseName=$installerBaseName" .\packaging\WindowSentinel.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }

    $installerPath = Join-Path $repoRoot "dist\$($product.installer_filename)"
    $checksumPath = Join-Path $repoRoot "dist\$($product.checksum_filename)"
    $checksum = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $checksumPath -Value "$checksum  $($product.installer_filename)" -Encoding ascii
}
finally {
    Pop-Location
}

Write-Output "Runnable: $repoRoot\dist\WindowSentinel\WindowSentinel.exe"
Write-Output "Installer: $repoRoot\dist\WindowSentinel-Setup.exe"
Write-Output "Checksum: $repoRoot\dist\WindowSentinel-Setup.exe.sha256"
