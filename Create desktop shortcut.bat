@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$desktop = [Environment]::GetFolderPath('Desktop'); $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Starkist.lnk')); $link.TargetPath = (Join-Path $PWD.Path 'run.bat'); $link.WorkingDirectory = $PWD.Path; $link.IconLocation = (Join-Path $PWD.Path 'assets\ui\starkist.ico'); $link.WindowStyle = 7; $link.Description = 'Starkist'; $link.Save()"
if errorlevel 1 (
    echo The shortcut could not be created.
) else (
    echo A Starkist shortcut is now on your desktop.
)
pause
