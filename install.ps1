<#
skydrop installer for Windows 10 and 11.

  install.cmd                install or upgrade; your settings are kept
  install.cmd -Voice NAME    use another Piper voice, e.g. en_US-lessac-medium
  install.cmd -NoVoice       skip the Piper neural voice (read-aloud uses Windows' own voice)
  install.cmd -Yes           answer yes to every question

Everything goes in %LOCALAPPDATA%\skydrop: the program, a private Python with Pillow,
the voices, and the skydrop command in its bin folder (added to your PATH). It also adds
a Start menu shortcut and, where Windows Terminal is installed, two profiles to it:
"skydrop" and "skydrop CRT". Your settings live in %APPDATA%\skydrop.
Remove it all with %LOCALAPPDATA%\skydrop\uninstall.cmd.
#>
[CmdletBinding()]
param(
  [string]$Voice = "",
  [switch]$NoVoice,
  [switch]$Yes
)

$Version = "3.14.0"
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Src      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data     = Join-Path $env:LOCALAPPDATA "skydrop"
$AppDir   = Join-Path $Data "app"
$Venv     = Join-Path $Data "venv"
$Py       = Join-Path $Venv "Scripts\python.exe"
$BinDir   = Join-Path $Data "bin"
$Launcher = Join-Path $BinDir "skydrop.cmd"
$Icon     = Join-Path $Data "skydrop.ico"
$Cache    = Join-Path $Data "cache"
$ConfDir  = Join-Path $env:APPDATA "skydrop"
$StartMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$Fragment = Join-Path $env:LOCALAPPDATA "Microsoft\Windows Terminal\Fragments\skydrop"
$Watching = $false

function Step($t) { Write-Host ""; Write-Host "==> " -ForegroundColor Yellow -NoNewline; Write-Host $t -ForegroundColor White }
function Ok($t)   { Write-Host "  + " -ForegroundColor Green -NoNewline; Write-Host $t }
function Note($t) { Write-Host "  - " -ForegroundColor Cyan -NoNewline; Write-Host $t }
function Warn($t) { Write-Host "  ! " -ForegroundColor Red -NoNewline; Write-Host $t }
function Die($t)  { Write-Host ""; Write-Host "skydrop install stopped: $t" -ForegroundColor Red; exit 1 }
function Ask($q) {
  if ($Yes) { return $true }
  try { $r = Read-Host "  $q [Y/n]" } catch { return $true }
  return -not ($r -match '^[nN]')
}
function Tilde($p) {
  foreach ($v in @("LOCALAPPDATA", "APPDATA", "USERPROFILE")) {
    $base = [Environment]::GetEnvironmentVariable($v)
    if ($base -and $p.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) { return "%$v%" + $p.Substring($base.Length) }
  }
  return $p
}

if (-not (Test-Path (Join-Path $Src "skydrop\__main__.py"))) {
  Die "run this from the unpacked skydrop folder (the skydrop folder is missing)."
}
if ([Environment]::OSVersion.Version.Major -lt 10) { Die "skydrop needs Windows 10 or 11." }

Write-Host ""
Write-Host "  skydrop $Version" -ForegroundColor White -NoNewline
Write-Host "   terminal weather: NEXRAD radar, a warnings crawl and a tornado alarm"

# --------------------------------------------------------------------------- python
Step "Checking Python"
function Get-Python($exe, [string[]]$pre) {
  try {
    $out = & $exe @pre -c "import sys; print(sys.executable); print('%d.%d' % sys.version_info[:2])" 2>$null
  } catch { return $null }
  if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
  $lines = @($out)
  if ($lines.Count -lt 2) { return $null }
  $path = "$($lines[0])".Trim()
  if ($path -match '\\WindowsApps\\') { return $null }   # the Microsoft Store's Python keeps its files apart; skip it
  try { $ver = [version]("$($lines[1])".Trim()) } catch { return $null }
  if ($ver -lt [version]"3.8") { return $null }
  return [pscustomobject]@{ Exe = $path; Ver = $ver }
}
function Find-Python {
  $found = @()
  if (Get-Command py -ErrorAction SilentlyContinue) { $found += Get-Python "py" @("-3") }
  foreach ($name in @("python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $found += Get-Python $cmd.Source @() }
  }
  $roots = @((Join-Path $env:LOCALAPPDATA "Programs\Python"), $env:ProgramFiles)
  foreach ($root in $roots) {
    if ($root -and (Test-Path $root)) {
      Get-ChildItem $root -Directory -Filter "Python3*" -ErrorAction SilentlyContinue | ForEach-Object {
        $exe = Join-Path $_.FullName "python.exe"
        if (Test-Path $exe) { $found += Get-Python $exe @() }
      }
    }
  }
  return $found | Where-Object { $_ } | Sort-Object Ver -Descending | Select-Object -First 1
}
$PySys = Find-Python
if (-not $PySys) {
  Note "Python 3 isn't installed (skydrop needs Python 3.8 or newer)"
  if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Python 3.13 from python.org with winget now?")) {
    & winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    $PySys = Find-Python
  }
  if (-not $PySys) {
    Die "install Python 3 from https://www.python.org/downloads/ (or: winget install Python.Python.3.13), then run install.cmd again."
  }
}
Ok "Python $($PySys.Ver) ($(Tilde $PySys.Exe))"

# --------------------------------------------------------------------------- a running skydrop
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
  Where-Object { "$($_.CommandLine)" -like "*\skydrop\app\run.py*" -and "$($_.CommandLine)" -notmatch '--watch' }
if ($running) { Die "skydrop is running; close it (press q in it) and run install.cmd again." }

# --------------------------------------------------------------------------- the running watch
$PidFile = Join-Path $Cache "watch.pid"
if (Test-Path $PidFile) {
  $wpid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($wpid -match '^\d+$') {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$wpid" -ErrorAction SilentlyContinue
    if ($proc -and "$($proc.CommandLine)" -match '--watch') {
      Stop-Process -Id ([int]$wpid) -Force -ErrorAction SilentlyContinue
      $Watching = $true
      Note "paused the background tornado watch while updating"
    }
  }
}

# --------------------------------------------------------------------------- private python
Step "Setting up skydrop's private Python"
New-Item -ItemType Directory -Force -Path $Data | Out-Null
if (Test-Path $Py) {
  $vv = & $Py -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
  & $Py -m pip --version *> $null
  if ("$vv".Trim() -ne "$($PySys.Ver.Major).$($PySys.Ver.Minor)" -or $LASTEXITCODE -ne 0) {
    Note "rebuilding it (Python $($PySys.Ver))"
    Remove-Item -Recurse -Force $Venv -ErrorAction SilentlyContinue
  }
}
if (-not (Test-Path $Py)) {
  & $PySys.Exe -m venv $Venv
  if (-not (Test-Path $Py)) { Die "couldn't create a private Python in $(Tilde $Venv)" }
}
Ok "virtual environment in $(Tilde $Venv)"

function Pip-Install {
  param([string[]]$Packages)
  & $Py -m pip install --upgrade --disable-pip-version-check @Packages 2>&1 | ForEach-Object {
    $line = "$_"
    if ($line -match '^(Collecting|Downloading|Successfully|ERROR)') {
      Write-Host ("    " + $line.Substring(0, [Math]::Min(110, $line.Length))) -ForegroundColor DarkGray
    }
  }
  return ($LASTEXITCODE -eq 0)
}
& $Py -m pip install --quiet --upgrade --disable-pip-version-check pip *> $null
if (Pip-Install @("pillow>=8.2", "tzdata")) {
  Ok "Pillow (pictures: radar maps, icons, charts) and time zone data"
} else {
  Warn "Pillow didn't install; skydrop will draw everything with text characters"
}
$Piper = $false
if (-not $NoVoice) {
  Note "installing Piper, an offline neural text-to-speech engine (about 70 MB, one time)"
  if (Pip-Install @("--only-binary=:all:", "piper-tts>=1.3")) {
    Ok "Piper voice engine"
    $Piper = $true
  } else {
    Warn "Piper didn't install here; read-aloud will use Windows' own voice"
  }
}

# --------------------------------------------------------------------------- program files
Step "Installing skydrop $Version"
$New = "$AppDir.new"
Remove-Item -Recurse -Force $New, "$AppDir.old" -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $New | Out-Null
Copy-Item -Recurse -Force (Join-Path $Src "skydrop") $New
Get-ChildItem $New -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$RunPy = @'
"""Starts skydrop from its install folder (keeps the current folder out of sys.path)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skydrop.cli import main  # noqa: E402

sys.exit(main())
'@
$Utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText((Join-Path $New "run.py"), $RunPy, $Utf8)
if (Test-Path $AppDir) { Move-Item $AppDir "$AppDir.old" -Force }
Move-Item $New $AppDir -Force
if (-not (Test-Path (Join-Path $AppDir "run.py"))) { Die "couldn't move the program into place" }
Remove-Item -Recurse -Force "$AppDir.old" -ErrorAction SilentlyContinue
& $Py -m compileall -q (Join-Path $AppDir "skydrop") *> $null
foreach ($f in @("README.md", "LICENSE", "uninstall.ps1", "uninstall.cmd")) {
  $p = Join-Path $Src $f
  if (Test-Path $p) { Copy-Item -Force $p (Join-Path $Data $f) }
}
Ok "program files in $(Tilde $AppDir)"

# --------------------------------------------------------------------------- the command
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$Cmd = "@echo off`r`nrem skydrop launcher (written by install.ps1; remove with %LOCALAPPDATA%\skydrop\uninstall.cmd)`r`n" +
       "`"%LOCALAPPDATA%\skydrop\venv\Scripts\python.exe`" `"%LOCALAPPDATA%\skydrop\app\run.py`" %*`r`n"
[System.IO.File]::WriteAllText($Launcher, $Cmd, (New-Object System.Text.ASCIIEncoding))
Ok "command: $(Tilde $Launcher)"

# PATH: the user's own, read and written as it is stored (so %VARIABLES% in it survive)
$PathNote = $false
$envKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Environment", $true)
if ($envKey) {
  $userPath = $envKey.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  $parts = @("$userPath".Split(";") | Where-Object { $_ })
  $already = $parts | Where-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd("\") -ieq $BinDir.TrimEnd("\") }
  if (-not $already) {
    $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
    $envKey.SetValue("Path", ((@($parts) + "%LOCALAPPDATA%\skydrop\bin") -join ";"), $kind)
    $PathNote = $true
    try {
      Add-Type -Namespace SkydropInstall -Name Win -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
    uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
      $r = [UIntPtr]::Zero
      [void][SkydropInstall.Win]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 3000, [ref]$r)
    } catch {}
    Ok "added $(Tilde $BinDir) to your PATH"
  }
  $envKey.Close()
}
$env:Path = "$env:Path;$BinDir"

# --------------------------------------------------------------------------- shortcuts
Step "Adding skydrop to the Start menu"
& $Launcher --write-icon $Icon *> $null
$Wt = (Get-Command wt.exe -ErrorAction SilentlyContinue)
$Shell = New-Object -ComObject WScript.Shell
$lnk = $Shell.CreateShortcut((Join-Path $StartMenu "skydrop.lnk"))
if ($Wt) {
  $lnk.TargetPath = $Wt.Source
  $lnk.Arguments = '-p "skydrop"'
} else {
  $lnk.TargetPath = $Launcher
}
$lnk.WorkingDirectory = $env:USERPROFILE
$lnk.Description = "Retro Weather Channel style forecasts, NEXRAD radar, warnings and a tornado alarm"
if (Test-Path $Icon) { $lnk.IconLocation = "$Icon,0" }
$lnk.Save()
Ok "Start menu: skydrop$(if ($Wt) { ' (opens in Windows Terminal)' })"

# Windows Terminal profiles, as a fragment Windows Terminal picks up by itself
New-Item -ItemType Directory -Force -Path $Fragment | Out-Null
$line = "`"$Py`" `"$(Join-Path $AppDir 'run.py')`""
$base = [ordered]@{ commandline = $line; startingDirectory = $env:USERPROFILE; padding = "0";
                    tabTitle = "skydrop"; suppressApplicationTitle = $true; closeOnExit = "always" }
if (Test-Path $Icon) { $base.icon = $Icon }
$plain = [ordered]@{ guid = "{f36c5301-ba04-4a69-91f3-9eb1ec009279}"; name = "skydrop" }
$crt = [ordered]@{ guid = "{baf97af6-3046-4387-9a7e-a31ab80cbfef}"; name = "skydrop CRT" }
foreach ($k in $base.Keys) { $plain[$k] = $base[$k]; $crt[$k] = $base[$k] }
$crt["experimental.retroTerminalEffect"] = $true
$json = (@{ profiles = @($plain, $crt) } | ConvertTo-Json -Depth 5)
[System.IO.File]::WriteAllText((Join-Path $Fragment "skydrop.json"), $json, $Utf8)
if ($Wt) {
  Ok "Windows Terminal profiles: skydrop, and skydrop CRT (a scanline glass from Windows Terminal itself)"
} else {
  Note "Windows Terminal isn't installed; its profiles will appear once it is (Microsoft Store: Windows Terminal)"
}

# --------------------------------------------------------------------------- settings
Step "Your settings"
if (Test-Path (Join-Path $ConfDir "config.json")) {
  Ok "keeping your ZIP code, keys and preferences ($(Tilde $ConfDir)\config.json)"
} else {
  Ok "first start creates $(Tilde $ConfDir)\config.json (change everything on the Settings screen, key 0)"
}

# --------------------------------------------------------------------------- voice
if ($Piper) {
  Step "Piper voice $(if ($Voice) { $Voice } else { '(the one in your settings)' })"
  if ($Voice) { & $Launcher --get-voice $Voice } else { & $Launcher --get-voice }
  if ($LASTEXITCODE -eq 0) {
    & $Launcher --voice-check *> $null
    if ($LASTEXITCODE -eq 0) {
      Ok "voice ready"
    } else {
      Warn "Piper couldn't run on this computer, so read-aloud will use Windows' own voice"
      & $Launcher --set voice_engine=sapi *> $null
    }
  } else {
    Warn "the voice didn't download; try again later with: skydrop --get-voice $Voice"
  }
}

# --------------------------------------------------------------------------- check
Step "Checking the installation"
& $Launcher --version
if ($LASTEXITCODE -ne 0) { Die "the skydrop command didn't start; see the messages above" }
& $Launcher --watch-sync *> $null
if ($LASTEXITCODE -eq 0) {
  $conf = Join-Path $ConfDir "config.json"
  if ((Test-Path $conf) -and ((Get-Content $conf -Raw) -match '"background_watch":\s*"on"')) {
    Ok "background tornado watch running (Settings > Tornado alarm > Watch when closed)"
  } elseif ($Watching) {
    Note "the background tornado watch is off in Settings, so it stays off"
  }
}
if (Ask "Test the weather data sources now? (takes a few seconds)") {
  & $Launcher --check
  if ($LASTEXITCODE -ne 0) { Note "a source that failed above is skipped automatically; you can turn sources off in Settings" }
}

Write-Host ""
Write-Host "  skydrop $Version is installed." -ForegroundColor Green
Write-Host ""
if ($Wt) {
  Write-Host "  Start it:   the skydrop shortcut in the Start menu, or the skydrop profile in Windows Terminal"
} else {
  Write-Host "  Start it:   the skydrop shortcut in the Start menu"
}
if ($PathNote) {
  Write-Host "              or type skydrop in a NEW terminal window (this one doesn't know the new PATH yet)"
} else {
  Write-Host "              or type skydrop in a terminal"
}
Write-Host "  Graphics:   full-color pictures in Windows Terminal 1.22 or newer (sixel), a block-graphics look"
Write-Host "              in the older console; chosen automatically (Settings, key 0, to change)"
Write-Host "  CRT mode:   Settings, Display, CRT effect; the skydrop CRT profile adds Windows Terminal's scanlines"
Write-Host "  Alarm:      a new Tornado Warning for your ZIP code sounds the NOAA alarm; test it with skydrop --test-alarm"
Write-Host "  Help:       press ? inside the app, or see $(Tilde $Data)\README.md"
Write-Host "  Uninstall:  $(Tilde $Data)\uninstall.cmd"
Write-Host ""
exit 0
