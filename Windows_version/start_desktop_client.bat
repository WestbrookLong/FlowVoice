@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw desktop_client.py
) else (
    start "" python desktop_client.py
)
