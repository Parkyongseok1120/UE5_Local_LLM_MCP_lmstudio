@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    py -3.12 install.py %*
    goto finish
)
py -3 install.py %*
goto finish
:use_python
where python3.12 >nul 2>nul
if not errorlevel 1 (
    python3.12 install.py %*
    goto finish
)
python install.py %*
:finish
set "INSTALL_EXIT=%ERRORLEVEL%"
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
