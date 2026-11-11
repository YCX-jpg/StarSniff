@echo off
setlocal
cd /d "%~dp0"

rem Prefer pythonw.exe so no console window appears; fall back to python.exe.
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0app.py"
    exit /b 0
)

where python.exe >nul 2>nul
if %errorlevel%==0 (
    python.exe "%~dp0app.py"
    exit /b %errorlevel%
)

echo.
echo  Python 3.9+ not found. Please install it first:
echo  https://www.python.org/downloads/
echo.
pause
exit /b 1
