@echo off
rem Remove skydrop from Windows: runs uninstall.ps1 (add -Purge to delete your settings too).
rem It all runs on the next line, from the temporary folder, because the uninstaller
rem deletes the folder this file lives in.
cd /d "%TEMP%" & copy /y "%~dp0uninstall.ps1" "%TEMP%\skydrop-uninstall.ps1" >nul & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\skydrop-uninstall.ps1" %* & del "%TEMP%\skydrop-uninstall.ps1" >nul 2>&1 & (echo %cmdcmdline% | findstr /i /c:"%~nx0" >nul && pause) & exit /b
