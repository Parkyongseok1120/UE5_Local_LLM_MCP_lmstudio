@echo off
setlocal
cd /d "%~dp0"
echo Unreal58-RAG Portable MCP Installer (Agent Mode)
echo.
echo Agent mode: file writes, commands, and Unreal builds ENABLED.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-UnrealMcp.ps1" -EnableAgentMode %*
if errorlevel 1 (
  echo.
  echo Install failed. See messages above.
  pause
  exit /b 1
)
echo.
echo Done. Agent mode is ON. Restart LM Studio and connect MCP servers.
pause
