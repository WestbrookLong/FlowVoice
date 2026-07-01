@echo off
setlocal
set "FLOWVOICE_DESKTOP_ENGINE=baidu"
set "FLOWVOICE_BAIDU_API_KEY=cYXOBUcJknhJmAiYVhG12QpR"
set "FLOWVOICE_BAIDU_SECRET_KEY=yMkhI4giBklKhG994wmoHz7Kq1cA1Nkh"
set "FLOWVOICE_BAIDU_DEV_PID=80001"
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%SCRIPT_DIR%desktop_client.py"
) else (
    start "" python "%SCRIPT_DIR%desktop_client.py"
)
