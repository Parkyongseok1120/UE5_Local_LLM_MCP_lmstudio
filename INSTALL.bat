@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 install.py %*
exit /b %ERRORLEVEL%
:use_python
python install.py %*
