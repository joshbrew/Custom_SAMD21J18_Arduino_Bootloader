@echo off
setlocal EnableExtensions DisableDelayedExpansion
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0build.py" --verify-repeat %*
  goto done
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0build.py" --verify-repeat %*
  goto done
)
echo Python 3 is required: https://www.python.org/downloads/
exit /b 1
:done
set "J18_EXIT=%ERRORLEVEL%"
if not "%J18_NO_PAUSE%"=="1" pause
exit /b %J18_EXIT%
