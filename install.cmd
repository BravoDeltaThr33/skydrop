@echo off
rem skydrop installer for Windows: runs install.ps1 (see it for the options).
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "rc=%errorlevel%"
rem opened by double-clicking: keep the window open to read the result
echo %cmdcmdline% | findstr /i /c:"%~nx0" >nul && pause
exit /b %rc%
