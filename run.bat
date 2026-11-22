@echo off
setlocal
title Starkist
cd /d "%~dp0"

rem uv installs to this folder; add it in case this window was opened before PATH was updated.
where uv >NUL 2>&1
if errorlevel 1 if exist "%USERPROFILE%\.local\bin\uv.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >NUL 2>&1
if errorlevel 1 goto need_uv

:have_uv
if not exist ".venv\Scripts\starkist-gui.exe" echo Setting up Starkist. This happens once and takes about a minute...
uv sync --quiet
if errorlevel 1 goto sync_failed
start "" ".venv\Scripts\starkist-gui.exe"
exit /b 0

:need_uv
echo Starkist needs "uv", a small free tool that installs Python and the libraries Starkist uses.
echo It comes from https://docs.astral.sh/uv/ and installs for your user account only.
echo.
choice /c YN /m "Install uv now"
if errorlevel 2 goto declined
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto install_failed
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >NUL 2>&1
if errorlevel 1 goto reopen
goto have_uv

:declined
echo.
echo Nothing was installed. Install uv from https://docs.astral.sh/uv/ and run this file again.
pause
exit /b 1

:install_failed
echo.
echo uv could not be installed. Install it from https://docs.astral.sh/uv/ and run this file again.
pause
exit /b 1

:reopen
echo.
echo uv is installed. Close this window and run this file again to finish.
pause
exit /b 0

:sync_failed
echo.
echo Setup did not finish. Check your internet connection and run this file again.
pause
exit /b 1
