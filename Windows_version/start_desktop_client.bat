@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%SCRIPT_DIR%desktop_client.py"
) else (
    start "" python "%SCRIPT_DIR%desktop_client.py"
)
