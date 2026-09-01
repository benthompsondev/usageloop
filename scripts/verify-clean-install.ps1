param(
    [ValidateSet('fresh', '0.9.1', '1.0.0', '1.0.1', '1.0.2', 'legacy-chain', 'broken-registration')]
    [string]$Scenario = 'fresh',
    [string[]]$PreviousInstallers = @()
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
$product = (& $python -c 'import json; from sentinel.product import PRODUCT; print(json.dumps(PRODUCT.packaging_metadata()))') | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Product metadata could not be read.' }

$installer = Join-Path $repoRoot "dist\$($product.installer_filename)"
$canonicalDir = Join-Path $env:LOCALAPPDATA "Programs\$($product.display_name)"
$canonicalExe = Join-Path $canonicalDir $product.executable_name
$legacyDir = Join-Path $env:LOCALAPPDATA "Programs\$($product.legacy_install_folder)"
$stateDir = Join-Path $env:LOCALAPPDATA $product.app_data_folder
$stateFile = Join-Path $stateDir 'app-state.json'
$historyFile = Join-Path $stateDir 'sentinel.jsonl'
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$appId = $product.app_id.Trim('{}')
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{$appId}_is1"
$shortcutDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$($product.display_name)"
$shortcut = Join-Path $shortcutDir "$($product.display_name).lnk"
$legacyStartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$($product.legacy_install_folder)"
$fixtureMarker = 'windows_install_acceptance_fixture'

function Invoke-Installer([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Installer is missing: $Path"
    }
    $process = Start-Process -FilePath $Path -ArgumentList @(
        '/VERYSILENT',
        '/SUPPRESSMSGBOXES',
        '/NORESTART',
        '/CLOSEAPPLICATIONS'
    ) -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Installer $Path exited with code $($process.ExitCode)."
    }
}

function Seed-LegacyInnoState {
    New-Item -ItemType Directory -Path $legacyDir -Force | Out-Null
    New-Item -ItemType Directory -Path $legacyStartMenu -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $legacyStartMenu 'Window Sentinel.lnk') -Value 'legacy fixture'
    New-Item -Path $uninstallKey -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name DisplayName -Value 'UsageLoop 0.7.0' -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name DisplayVersion -Value '0.7.0' -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name InstallLocation -Value $legacyDir -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name 'Inno Setup: App Path' -Value $legacyDir -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name 'Inno Setup: Icon Group' -Value $product.legacy_install_folder -PropertyType String -Force | Out-Null
}

function Seed-BrokenRegistration {
    New-ItemProperty -LiteralPath $uninstallKey -Name DisplayName -Value 'UsageLoop 0.9.1' -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name DisplayVersion -Value '0.9.1' -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name Publisher -Value 'Ben Thompson' -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name InstallLocation -Value "$legacyDir\" -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name DisplayIcon -Value (Join-Path $legacyDir $product.executable_name) -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name UninstallString -Value ('"{0}"' -f (Join-Path $legacyDir 'unins000.exe')) -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name QuietUninstallString -Value ('"{0}" /SILENT' -f (Join-Path $legacyDir 'unins000.exe')) -PropertyType String -Force | Out-Null
    New-ItemProperty -LiteralPath $uninstallKey -Name 'Inno Setup: App Path' -Value $legacyDir -PropertyType String -Force | Out-Null
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -LiteralPath $runKey -Name UsageLoop -Value ('"{0}" --background' -f (Join-Path $legacyDir $product.executable_name)) -PropertyType String -Force | Out-Null
    if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
    if (Test-Path -LiteralPath $legacyDir) {
        throw 'The broken-registration fixture requires the legacy target to be absent.'
    }
}

function Get-UsageLoopUninstallEntries {
    $roots = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    return @(
        foreach ($root in $roots) {
            Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue |
                ForEach-Object {
                    $item = Get-ItemProperty -LiteralPath $_.PSPath
                    if ($item.DisplayName -like 'UsageLoop*' -or $item.DisplayName -like 'Window Sentinel*') {
                        $item
                    }
                }
        }
    )
}

function Get-ShortcutDetails([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    return [pscustomobject]@{
        Target = $link.TargetPath
        WorkingDirectory = $link.WorkingDirectory
        IconLocation = $link.IconLocation
    }
}

function Assert-IconResource([string]$Path) {
    Add-Type -AssemblyName System.Drawing.Common -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.Drawing -ErrorAction SilentlyContinue
    $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($Path)
    if ($null -eq $icon -or $icon.Width -lt 16 -or $icon.Height -lt 16) {
        throw "No usable Windows icon was embedded in $Path."
    }
    $icon.Dispose()
}

if (-not (Test-Path -LiteralPath $installer)) {
    throw "Required build artifact is missing: $installer"
}

New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
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
('{"event":"error","timestamp":"2026-01-01T00:00:00Z","sentinel_version":"0.9.1","category":"' + $fixtureMarker + '"}') |
    Set-Content -LiteralPath $historyFile -Encoding ascii

if ($Scenario -eq '0.9.1' -or $Scenario -eq 'legacy-chain') {
    Seed-LegacyInnoState
}

foreach ($previous in $PreviousInstallers) {
    Invoke-Installer $previous
}

if ($PreviousInstallers.Count -gt 0) {
    $previousEntry = Get-ItemProperty -LiteralPath $uninstallKey
    $previousExe = Join-Path $previousEntry.InstallLocation.TrimEnd('\') $product.executable_name
    if (-not (Test-Path -LiteralPath $previousExe)) {
        throw "The previous release did not install its executable: $previousExe"
    }
    New-Item -Path $runKey -Force | Out-Null
    New-ItemProperty -LiteralPath $runKey -Name UsageLoop -Value ('"{0}" --background' -f $previousExe) -PropertyType String -Force | Out-Null
}

if ($Scenario -eq 'broken-registration') {
    if (-not (Test-Path -LiteralPath $canonicalExe)) {
        throw 'The broken-registration fixture requires canonical newer files.'
    }
    Seed-BrokenRegistration
    $broken = Get-ItemProperty -LiteralPath $uninstallKey
    $brokenTarget = [regex]::Match($broken.UninstallString, '^"([^"]+)"').Groups[1].Value
    if (Test-Path -LiteralPath $brokenTarget) {
        throw 'The broken-registration fixture unexpectedly has a valid uninstaller.'
    }
    Write-Output 'Reproduced stale 0.9.1 registration pointing at a deleted legacy uninstaller.'
}

if ($Scenario -eq 'legacy-chain') {
    if (-not (Test-Path -LiteralPath $canonicalExe)) {
        throw 'The legacy release chain never reached the canonical install folder.'
    }
    if (Test-Path -LiteralPath $shortcut) {
        throw 'The 1.0.1 legacy-group reproduction unexpectedly retained the UsageLoop shortcut.'
    }
    Write-Output 'Reproduced 1.0.1: legacy Start Menu inheritance left no UsageLoop shortcut.'
}

Invoke-Installer $installer

if (-not (Test-Path -LiteralPath $canonicalExe)) { throw 'Canonical executable was not installed.' }
if (Test-Path -LiteralPath $legacyDir) { throw 'Legacy install directory was not removed.' }
if (Test-Path -LiteralPath $legacyStartMenu) { throw 'Legacy Start Menu group was not removed.' }
if (-not (Test-Path -LiteralPath $shortcut)) { throw 'The stable per-user Start Menu shortcut was not created.' }

$installedVersion = (Get-Item -LiteralPath $canonicalExe).VersionInfo.ProductVersion.Trim()
if ($installedVersion -ne $product.version) { throw "Installed version was $installedVersion, not $($product.version)." }
Assert-IconResource $canonicalExe

$shortcutDetails = Get-ShortcutDetails $shortcut
if ($shortcutDetails.Target.TrimEnd('\') -ne $canonicalExe.TrimEnd('\')) {
    throw "Shortcut target is invalid: $($shortcutDetails.Target)"
}
if ($shortcutDetails.WorkingDirectory.TrimEnd('\') -ne $canonicalDir.TrimEnd('\')) {
    throw "Shortcut working directory is invalid: $($shortcutDetails.WorkingDirectory)"
}
$shortcutIcon = $shortcutDetails.IconLocation.Split(',')[0].Trim('"')
if ($shortcutIcon.TrimEnd('\') -ne $canonicalExe.TrimEnd('\')) {
    throw "Shortcut icon target is invalid: $($shortcutDetails.IconLocation)"
}

$startApps = @()
for ($attempt = 0; $attempt -lt 10 -and $startApps.Count -ne 1; $attempt++) {
    $startApps = @(Get-StartApps -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq $product.display_name })
    if ($startApps.Count -ne 1) { Start-Sleep -Seconds 1 }
}
if ($startApps.Count -ne 1) {
    throw "Windows Start app discovery found $($startApps.Count) UsageLoop entries."
}

$uninstallEntries = Get-UsageLoopUninstallEntries
if ($uninstallEntries.Count -ne 1) { throw "Expected one UsageLoop uninstall entry, found $($uninstallEntries.Count)." }
$entry = $uninstallEntries[0]
if ($entry.DisplayName -ne 'UsageLoop') { throw "DisplayName was '$($entry.DisplayName)'." }
if ($entry.DisplayVersion -ne $product.version) { throw "DisplayVersion was '$($entry.DisplayVersion)'." }
if ($entry.Publisher -ne 'UsageLoop') { throw "Publisher was '$($entry.Publisher)'." }
if ($entry.InstallLocation.TrimEnd('\') -ne $canonicalDir.TrimEnd('\')) {
    throw "InstallLocation points at the wrong folder: $($entry.InstallLocation)"
}
$displayIcon = $entry.DisplayIcon.Split(',')[0].Trim('"')
if ($displayIcon.TrimEnd('\') -ne $canonicalExe.TrimEnd('\') -or -not (Test-Path -LiteralPath $displayIcon)) {
    throw "DisplayIcon is invalid: $($entry.DisplayIcon)"
}
$uninstallTarget = [regex]::Match($entry.UninstallString, '^"([^"]+)"').Groups[1].Value
if (-not $uninstallTarget -or -not (Test-Path -LiteralPath $uninstallTarget)) {
    throw "Uninstall target is missing: $($entry.UninstallString)"
}

if ($PreviousInstallers.Count -gt 0) {
    $startup = (Get-ItemProperty -LiteralPath $runKey -Name UsageLoop).UsageLoop
    if ($startup -ne ('"{0}" --background' -f $canonicalExe)) {
        throw "Startup registration was not migrated: $startup"
    }
}

$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
if ($state.settings.automation_enabled -ne $false) { throw 'Automation setting was not preserved.' }
if ($state.settings.start_with_windows -ne $true) { throw 'Startup preference was not preserved.' }
if ($state.settings.schedule_mode -ne 'daily') { throw 'Daily schedule mode was not preserved.' }
if ($state.settings.daily_start_hour -ne 6 -or $state.settings.daily_start_minute -ne 45) {
    throw 'Daily schedule time was not preserved.'
}
if (-not (Select-String -LiteralPath $historyFile -Pattern $fixtureMarker -Quiet)) {
    throw 'Safe history was not preserved.'
}

$env:QT_QPA_PLATFORM = 'offscreen'
$first = Start-Process -FilePath $shortcut -PassThru
Start-Sleep -Seconds 3
if ($first.HasExited) { throw 'UsageLoop did not stay running when launched from its shortcut.' }
Stop-Process -Id $first.Id -Force
$first.WaitForExit()
$second = Start-Process -FilePath $shortcut -PassThru
Start-Sleep -Seconds 3
if ($second.HasExited) { throw 'UsageLoop could not be reopened from the Start Menu shortcut.' }
Stop-Process -Id $second.Id -Force
$second.WaitForExit()

& (Join-Path $PSScriptRoot 'verify-packaged-activation.ps1') -Executable $canonicalExe
if ($LASTEXITCODE -ne 0) { throw 'Packaged activation verification failed.' }

$uninstall = Start-Process -FilePath $uninstallTarget -ArgumentList @(
    '/VERYSILENT',
    '/SUPPRESSMSGBOXES',
    '/NORESTART'
) -Wait -PassThru
if ($uninstall.ExitCode -ne 0) { throw "Uninstaller exited with code $($uninstall.ExitCode)." }
if (Test-Path -LiteralPath $canonicalDir) { throw 'Canonical install directory remained after uninstall.' }
if (Test-Path -LiteralPath $shortcutDir) { throw 'UsageLoop Start Menu group remained after uninstall.' }
if (Test-Path -LiteralPath $legacyStartMenu) { throw 'Legacy Start Menu group remained after uninstall.' }
if ((Get-ItemProperty -LiteralPath $runKey -Name UsageLoop -ErrorAction SilentlyContinue).UsageLoop) {
    throw 'UsageLoop startup registration remained after uninstall.'
}
if ((Get-UsageLoopUninstallEntries).Count -ne 0) { throw 'A UsageLoop uninstall entry remained.' }
if (-not (Test-Path -LiteralPath $stateFile) -or -not (Test-Path -LiteralPath $historyFile)) {
    throw 'Uninstall removed user-owned state or history.'
}
if (Get-Process UsageLoop -ErrorAction SilentlyContinue) { throw 'UsageLoop process remained after uninstall.' }

Write-Output "Windows $Scenario install, Search, ARP, icon, relaunch, migration, and uninstall checks passed."
