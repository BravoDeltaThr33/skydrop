<#
Remove skydrop from Windows.

  uninstall.cmd           remove the program; keep your settings
  uninstall.cmd -Purge    also delete your settings (ZIP code, keys)

Python itself, if the installer added it, stays: other programs may use it.
#>
[CmdletBinding()]
param([switch]$Purge)

$Data      = Join-Path $env:LOCALAPPDATA "skydrop"
$BinDir    = Join-Path $Data "bin"
$ConfDir   = Join-Path $env:APPDATA "skydrop"
$PidFile   = Join-Path $Data "cache\watch.pid"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\skydrop.lnk"
$Fragment  = Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\Fragments\skydrop"
function Say($t) { Write-Host "  $t" }
Set-Location $env:TEMP          # not inside the folder about to go

Write-Host "Removing skydrop..."

# the background tornado watch and its login item
if (Test-Path $PidFile) {
  $wpid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($wpid -match '^\d+$') {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$wpid" -ErrorAction SilentlyContinue
    if ($proc -and "$($proc.CommandLine)" -match '--watch') {
      Stop-Process -Id ([int]$wpid) -Force -ErrorAction SilentlyContinue
      Say "stopped the background tornado watch"
    }
  }
}
$run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
if (Get-ItemProperty -Path $run -Name "skydrop tornado watch" -ErrorAction SilentlyContinue) {
  Remove-ItemProperty -Path $run -Name "skydrop tornado watch" -ErrorAction SilentlyContinue
  Say "removed the tornado watch from your login programs"
}

# shortcuts and the Windows Terminal profiles
if (Test-Path $StartMenu) { Remove-Item -Force $StartMenu; Say "removed the Start menu shortcut" }
if (Test-Path $Fragment) { Remove-Item -Recurse -Force $Fragment; Say "removed the Windows Terminal profiles" }

# PATH (the user's own, kept as stored)
$envKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
if ($envKey) {
  $userPath = $envKey.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  $parts = @("$userPath".Split(";") | Where-Object { $_ })
  $keep = @($parts | Where-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd("\") -ine $BinDir.TrimEnd("\") })
  if ($keep.Count -ne $parts.Count) {
    $envKey.SetValue("Path", ($keep -join ";"), [Microsoft.Win32.RegistryValueKind]::ExpandString)
    Say "took skydrop off your PATH"
  }
  $envKey.Close()
}

# the program, its private Python, the voices and the cache (this script runs from there:
# PowerShell has read it already, so it can go too)
if (Test-Path $Data) {
  Remove-Item -Recurse -Force $Data -ErrorAction SilentlyContinue
  if (Test-Path $Data) {
    Say "some files in $Data are in use; close skydrop and delete that folder"
  } else {
    Say "removed the program, its private Python, the downloaded voices and the cache"
  }
}

if ($Purge) {
  if (Test-Path $ConfDir) { Remove-Item -Recurse -Force $ConfDir; Say "deleted your settings" }
} elseif (Test-Path $ConfDir) {
  Say "kept your settings in $ConfDir (run uninstall.cmd -Purge to delete them)"
}
Write-Host "Done."
exit 0
