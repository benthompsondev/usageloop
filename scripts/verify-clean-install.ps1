param(
    [string]$PreviousInstaller
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
$product = (& $python -c 'import json; from sentinel.product import PRODUCT; print(json.dumps(PRODUCT.packaging_metadata()))') | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Product metadata could not be read.' }
$installer = Join-Path $repoRoot "dist\$($product.installer_filename)"
$builtExe = Join-Path $repoRoot "dist\$($product.dist_folder_name)\$($product.executable_name)"
$canonicalDir = Join-Path $env:LOCALAPPDATA "Programs\$($product.display_name)"
$canonicalExe = Join-Path $canonicalDir $product.executable_name
$legacyDir = Join-Path $env:LOCALAPPDATA "Programs\$($product.legacy_install_folder)"
$legacyExe = Join-Path $legacyDir $product.executable_name
$stateDir = Join-Path $env:LOCALAPPDATA $product.app_data_folder
$stateFile = Join-Path $stateDir 'app-state.json'
$historyFile = Join-Path $stateDir 'sentinel.jsonl'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$appId = $product.app_id.Trim('{}')
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{$appId}_is1"
$shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$($product.display_name)\$($product.display_name).lnk"
$legacyStartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$($product.legacy_install_folder)"

foreach ($required in @($installer, $builtExe)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required build artifact is missing: $required"
    }
}

New-Item -ItemType Directory -Path $legacyDir -Force | Out-Null
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
New-Item -ItemType Directory -Path $legacyStartMenu -Force | Out-Null
Set-Content -LiteralPath (Join-Path $legacyStartMenu 'Window Sentinel.lnk') -Value 'legacy fixture'

@'
{
  "schema_version": 1,
  "settings": {
    "automation_enabled": false,
    "start_with_windows": true,
    "first_run_complete": true,
    "compatible_runtime_identities": {},
    "checked_runtime_identities": {},
    "schedule_mode": "daily",
    "daily_start_hour": 6,
    "daily_start_minute": 45
  },
  "providers": {}
}
'@ | Set-Content -LiteralPath $stateFile -Encoding utf8
'{"event":"error","timestamp":"2026-01-01T00:00:00Z","sentinel_version":"0.9.1","category":"clean_install_fixture"}' |
    Set-Content -LiteralPath $historyFile -Encoding ascii
New-Item -Path $uninstallKey -Force | Out-Null
New-ItemProperty -LiteralPath $uninstallKey -Name DisplayName -Value 'UsageLoop 0.9.1' -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $uninstallKey -Name DisplayVersion -Value '0.9.1' -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $uninstallKey -Name InstallLocation -Value $legacyDir -PropertyType String -Force | Out-Null
New-ItemProperty -LiteralPath $uninstallKey -Name 'Inno Setup: App Path' -Value $legacyDir -PropertyType String -Force | Out-Null

if ($PreviousInstaller) {
    if (-not (Test-Path -LiteralPath $PreviousInstaller)) {
        throw "Previous installer is missing: $PreviousInstaller"
    }
    $previous = Start-Process -FilePath $PreviousInstaller -ArgumentList @(
        '/VERYSILENT',
        '/SUPPRESSMSGBOXES',
        '/NORESTART'
    ) -Wait -PassThru
    if ($previous.ExitCode -ne 0) {
        throw "Previous installer exited with code $($previous.ExitCode)."
    }
    if (-not (Test-Path -LiteralPath $legacyExe)) {
        throw 'The 0.9.1 installer did not reproduce the legacy install location.'
    }
} else {
    Copy-Item -LiteralPath $builtExe -Destination $legacyExe -Force
}

New-Item -Path $runKey -Force | Out-Null
New-ItemProperty -LiteralPath $runKey -Name UsageLoop -Value ('"{0}" --background' -f $legacyExe) -PropertyType String -Force | Out-Null

$setup = Start-Process -FilePath $installer -ArgumentList @(
    '/VERYSILENT',
    '/SUPPRESSMSGBOXES',
    '/NORESTART',
    '/CLOSEAPPLICATIONS'
) -Wait -PassThru
if ($setup.ExitCode -ne 0) {
    throw "Installer exited with code $($setup.ExitCode)."
}

if (-not (Test-Path -LiteralPath $canonicalExe)) { throw 'Canonical executable was not installed.' }
if (Test-Path -LiteralPath $legacyDir) { throw 'Legacy install directory was not removed.' }
if (Test-Path -LiteralPath $legacyStartMenu) { throw 'Legacy Start menu group was not removed.' }
if (-not (Test-Path -LiteralPath $shortcut)) { throw 'Start menu shortcut was not created.' }
$installedVersion = (Get-Item -LiteralPath $canonicalExe).VersionInfo.ProductVersion.Trim()
if ($installedVersion -ne $product.version) { throw "Installed version was $installedVersion, not $($product.version)." }

$uninstallEntries = @(
    Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' |
        ForEach-Object {
            $item = Get-ItemProperty -LiteralPath $_.PSPath
            if ($item.DisplayName -like 'UsageLoop*') { $item }
        }
)
if ($uninstallEntries.Count -ne 1) { throw "Expected one UsageLoop uninstall entry, found $($uninstallEntries.Count)." }
if ($uninstallEntries[0].DisplayVersion -ne $product.version) { throw "Uninstall metadata did not upgrade to $($product.version)." }
if ($uninstallEntries[0].InstallLocation.TrimEnd('\') -ne $canonicalDir.TrimEnd('\')) {
    throw "Uninstall metadata points at the wrong install location: $($uninstallEntries[0].InstallLocation)"
}
$uninstallTarget = [regex]::Match($uninstallEntries[0].UninstallString, '^"([^"]+)"').Groups[1].Value
if (-not $uninstallTarget -or -not (Test-Path -LiteralPath $uninstallTarget)) {
    throw "Uninstall target is missing: $($uninstallEntries[0].UninstallString)"
}
$startupAfterSetup = (Get-ItemProperty -LiteralPath $runKey -Name UsageLoop).UsageLoop
if ($startupAfterSetup -ne ('"{0}" --background' -f $canonicalExe)) {
    throw "Installer did not migrate startup to the canonical executable: $startupAfterSetup"
}

$env:QT_QPA_PLATFORM = 'offscreen'
$first = Start-Process -FilePath $canonicalExe -ArgumentList '--background' -PassThru
Start-Sleep -Seconds 3
if ($first.HasExited) { throw 'Installed UsageLoop did not stay running.' }
$second = Start-Process -FilePath $canonicalExe -ArgumentList '--background' -PassThru
$second.WaitForExit(5000) | Out-Null
if (-not $second.HasExited -or $second.ExitCode -ne 0) {
    throw 'The second UsageLoop launch was not rejected cleanly.'
}
$running = @(Get-Process UsageLoop -ErrorAction SilentlyContinue)
if ($running.Count -ne 1) { throw "Expected one UsageLoop process, found $($running.Count)." }

$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
if ($state.settings.automation_enabled -ne $false) { throw 'Automation setting was not preserved.' }
if ($state.settings.schedule_mode -ne 'daily') { throw 'Daily schedule mode was not preserved.' }
if ($state.settings.daily_start_hour -ne 6 -or $state.settings.daily_start_minute -ne 45) {
    throw 'Daily schedule time was not preserved.'
}
if (-not (Select-String -LiteralPath $historyFile -Pattern 'clean_install_fixture' -Quiet)) {
    throw 'Safe history was not preserved.'
}
$startup = (Get-ItemProperty -LiteralPath $runKey -Name UsageLoop).UsageLoop
if ($startup -ne ('"{0}" --background' -f $canonicalExe)) {
    throw "Startup registration was not migrated to the canonical executable: $startup"
}

Stop-Process -Id $first.Id -Force
$uninstaller = Join-Path $canonicalDir 'unins000.exe'
$uninstall = Start-Process -FilePath $uninstaller -ArgumentList @(
    '/VERYSILENT',
    '/SUPPRESSMSGBOXES',
    '/NORESTART'
) -Wait -PassThru
if ($uninstall.ExitCode -ne 0) { throw "Uninstaller exited with code $($uninstall.ExitCode)." }
if (Test-Path -LiteralPath $canonicalDir) { throw 'Canonical install directory remained after uninstall.' }
if (Test-Path -LiteralPath $shortcut) { throw 'Start menu shortcut remained after uninstall.' }
if (Test-Path -LiteralPath $legacyStartMenu) { throw 'Legacy Start menu group remained after uninstall.' }
if ((Get-ItemProperty -LiteralPath $runKey -Name UsageLoop -ErrorAction SilentlyContinue).UsageLoop) {
    throw 'UsageLoop startup registration remained after uninstall.'
}
if (Test-Path -LiteralPath $uninstallKey) { throw 'UsageLoop uninstall entry remained after uninstall.' }
if (-not (Test-Path -LiteralPath $stateFile) -or -not (Test-Path -LiteralPath $historyFile)) {
    throw 'Uninstall removed the user-owned state or history.'
}
if (Get-Process UsageLoop -ErrorAction SilentlyContinue) { throw 'UsageLoop process remained after uninstall.' }

Write-Output 'Clean Windows install, migration, single-instance, startup, state preservation, and uninstall checks passed.'
