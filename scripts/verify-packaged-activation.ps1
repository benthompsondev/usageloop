param(
    [string]$Executable = ""
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Executable) {
    $Executable = Join-Path $repoRoot 'dist\UsageLoop\UsageLoop.exe'
}
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Packaged executable is missing: $Executable"
}

Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class UsageLoopActivationWindows {
  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lp);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int max);
}
'@

function Get-SmokeWindow([int]$ProcessId) {
    $found = [System.Collections.Generic.List[object]]::new()
    [UsageLoopActivationWindows]::EnumWindows({
        param($handle, $unused)
        $owner = 0
        [void][UsageLoopActivationWindows]::GetWindowThreadProcessId($handle, [ref]$owner)
        if ($owner -eq $ProcessId) {
            $title = [Text.StringBuilder]::new(256)
            [void][UsageLoopActivationWindows]::GetWindowText($handle, $title, 256)
            if ($title.ToString() -eq 'UsageLoop activation smoke') {
                $found.Add([pscustomobject]@{
                    Handle = $handle
                    Visible = [UsageLoopActivationWindows]::IsWindowVisible($handle)
                })
            }
        }
        return $true
    }, [IntPtr]::Zero) | Out-Null
    return $found | Select-Object -First 1
}

$name = [Guid]::NewGuid().ToString('N')
$primary = $null
try {
    # First prove the packaged normal launch opens a window.
    $primary = Start-Process -FilePath $Executable -ArgumentList @('--activation-smoke', $name) -PassThru
    $window = $null
    for ($attempt = 0; $attempt -lt 50 -and $null -eq $window; $attempt++) {
        Start-Sleep -Milliseconds 100
        $window = Get-SmokeWindow $primary.Id
    }
    if ($null -eq $window -or -not $window.Visible) {
        throw 'The first packaged launch did not open its window.'
    }
    Stop-Process -Id $primary.Id -Force
    $primary.WaitForExit()

    # Start a fresh primary that hides itself through Qt, exactly like the
    # production close-to-tray path.
    $primary = Start-Process -FilePath $Executable -ArgumentList @('--activation-smoke', $name, '--activation-smoke-auto-hide') -PassThru
    $window = $null
    for ($attempt = 0; $attempt -lt 50 -and $null -eq $window; $attempt++) {
        Start-Sleep -Milliseconds 100
        $window = Get-SmokeWindow $primary.Id
    }
    Start-Sleep -Milliseconds 700
    if ($null -eq (Get-SmokeWindow $primary.Id)) { throw 'The hidden smoke window was not created.' }
    if ((Get-SmokeWindow $primary.Id).Visible) { throw 'The smoke window did not hide through Qt.' }

    $background = Start-Process -FilePath $Executable -ArgumentList @('--background', '--activation-smoke', $name) -Wait -PassThru
    if ($background.ExitCode -ne 0) { throw 'The second background launch failed.' }
    Start-Sleep -Milliseconds 300
    if ((Get-SmokeWindow $primary.Id).Visible) {
        throw 'A second background launch unexpectedly opened the window.'
    }

    $normal = Start-Process -FilePath $Executable -ArgumentList @('--activation-smoke', $name) -Wait -PassThru
    if ($normal.ExitCode -ne 0) { throw 'The second normal launch failed.' }
    $visible = $false
    for ($attempt = 0; $attempt -lt 30 -and -not $visible; $attempt++) {
        Start-Sleep -Milliseconds 100
        $visible = (Get-SmokeWindow $primary.Id).Visible
    }
    if (-not $visible) { throw 'The second normal launch did not restore the primary window.' }

    $sameExecutable = @(Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and ([IO.Path]::GetFullPath($_.ExecutablePath) -eq [IO.Path]::GetFullPath($Executable))
    })
    if ($sameExecutable.Count -ne 1) {
        throw "Expected one packaged UsageLoop process, found $($sameExecutable.Count)."
    }
    Write-Output 'Packaged first launch, background no-pop, and normal-launch activation checks passed.'
}
finally {
    if ($null -ne $primary -and -not $primary.HasExited) {
        Stop-Process -Id $primary.Id -Force
        $primary.WaitForExit()
    }
}
