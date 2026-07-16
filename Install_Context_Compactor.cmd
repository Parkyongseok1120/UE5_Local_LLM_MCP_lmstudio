@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
echo Installing Unreal Context Compactor for LM Studio...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\install_context_compactor.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" (
  echo Installation completed successfully. Y
) else (
  echo Installation failed with exit code %EXIT_CODE%.
)
if /I not "%~1"=="--no-pause" pause
exit /b %EXIT_CODE%
