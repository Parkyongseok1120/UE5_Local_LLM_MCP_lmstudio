@echo off
setlocal
cd /d "%~dp0"
echo Unreal58-RAG Portable MCP Installer
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\Install-UnrealMcp.ps1" %*
if errorlevel 1 (
  echo.
  echo Install failed. See messages above.
  pause
  exit /b 1
)
echo.
echo Done. Restart LM Studio and connect MCP servers.
pause
