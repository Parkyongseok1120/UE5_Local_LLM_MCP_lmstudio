@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_CMD="
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
    goto run_installer
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto run_installer
)
:use_python
where python3.12 >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python3.12"
    goto run_installer
)
where python >nul 2>nul
if errorlevel 1 goto python_missing
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto python_missing
set "PYTHON_CMD=python"
:run_installer
%PYTHON_CMD% install.py %*
goto finish
:python_missing
where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo Python 3.10 or newer was not found, and Windows PowerShell is unavailable. 1>&2
    echo Install Python 3.12 from https://www.python.org/downloads/windows/ and retry. 1>&2
    set "INSTALL_EXIT=9009"
    goto report
)
if not exist "%~dp0installer\bootstrap_python.ps1" (
    echo Python 3.10 or newer was not found, and the Python bootstrap helper is missing. 1>&2
    echo Re-extract the complete package and retry INSTALL.bat. 1>&2
    set "INSTALL_EXIT=2"
    goto report
)
echo Python 3.10 or newer was not found. Bootstrapping managed Python 3.12... 1>&2
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap_python.ps1" -InstallerPath "%~dp0install.py" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
goto report
:finish
set "INSTALL_EXIT=%ERRORLEVEL%"
:report
if /I "%INSTALL_NO_PAUSE%"=="1" exit /b %INSTALL_EXIT%
echo.
if "%INSTALL_EXIT%"=="0" (
    echo Installation complete. Press any key to exit.
) else (
    echo Installation failed. Exit code: %INSTALL_EXIT%
    echo Press any key to exit.
)
pause >nul
exit /b %INSTALL_EXIT%
