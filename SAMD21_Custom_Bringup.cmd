@echo off
setlocal EnableExtensions DisableDelayedExpansion
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\samd21_menu.ps1" %*
set "J18_MENU_EXIT=%ERRORLEVEL%"
if "%~1"=="" if not "%J18_MENU_EXIT%"=="0" pause
exit /b %J18_MENU_EXIT%
