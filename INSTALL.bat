@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 install.py %*
goto finish
:use_python
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
