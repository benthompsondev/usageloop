$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $repoRoot '.venv\Scripts\pyinstaller.exe'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $pyinstaller)) {
    throw 'Run: .\.venv\Scripts\python.exe -m pip install --editable ".[build]"'
}

Push-Location $repoRoot
try {
    $product = (& $python -c 'import json; from sentinel.product import PRODUCT; print(json.dumps(PRODUCT.packaging_metadata()))') | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Product metadata could not be read.' }

    & $python .\scripts\render_version_info.py
    if ($LASTEXITCODE -ne 0) { throw 'Version metadata rendering failed.' }

    & $python .\scripts\render_icon.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon rendering failed.' }

    & $pyinstaller --noconfirm --clean .\packaging\UsageLoop.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $helperEntry = Join-Path $repoRoot 'packaging\claude_status_entrypoint.py'
    $helperDist = Join-Path $repoRoot "dist\$($product.dist_folder_name)"
    $helperName = [IO.Path]::GetFileNameWithoutExtension($product.claude_status_helper_name)
    $helperWork = Join-Path $repoRoot "build\$helperName"
    & $pyinstaller --noconfirm --clean --onefile --console --name $helperName --distpath $helperDist --workpath $helperWork --specpath $helperWork --paths (Join-Path $repoRoot 'src') $helperEntry
    if ($LASTEXITCODE -ne 0) { throw 'Claude statusLine helper build failed.' }

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
    & $iscc "/DAppName=$($product.display_name)" "/DAppIconFile=$($product.icon_filename)" "/DDistFolder=$($product.dist_folder_name)" "/DStatusHelperName=$($product.claude_status_helper_name)" "/DAppVersion=$($product.version)" "/DAppExeName=$($product.executable_name)" "/DAppPublisher=$($product.publisher)" "/DAppId=$($product.app_id)" "/DInstallerBaseName=$installerBaseName" .\packaging\UsageLoop.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }

    $installerPath = Join-Path $repoRoot "dist\$($product.installer_filename)"
    $checksumPath = Join-Path $repoRoot "dist\$($product.checksum_filename)"
    $checksum = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $checksumPath -Value "$checksum  $($product.installer_filename)" -Encoding ascii
}
finally {
    Pop-Location
}

Write-Output "Runnable: $repoRoot\dist\$($product.dist_folder_name)\$($product.executable_name)"
Write-Output "Claude status helper: $repoRoot\dist\$($product.dist_folder_name)\$($product.claude_status_helper_name)"
Write-Output "Installer: $installerPath"
Write-Output "Checksum: $checksumPath"
