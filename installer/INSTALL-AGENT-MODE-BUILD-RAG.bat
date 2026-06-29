@echo off
setlocal

set "INSTALLER_DIR=%~dp0"
set "REPO_ROOT=%~dp0.."

cd /d "%REPO_ROOT%"

echo Unreal58-RAG Portable MCP Installer (Agent Mode + RAG Index)
echo.
echo Agent mode: file writes, commands, and Unreal builds are ENABLED.
echo This will install MCP settings, collect RAG inputs, build rag.sqlite, and run doctor.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER_DIR%Install-UnrealMcp.ps1" -EnableAgentMode
if errorlevel 1 goto :fail_install

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER_DIR%Configure-Knowledge.ps1" -NonInteractive -SkipBuild
if errorlevel 1 goto :fail_config

call :RunRag collect-projects -CopyProjectText
if errorlevel 1 goto :fail_rag

call :RunRag collect-symbols
if errorlevel 1 goto :fail_rag

call :RunRag collect-module-graph
if errorlevel 1 goto :fail_rag

call :RunRag build
if errorlevel 1 goto :fail_rag

call :RunRag doctor
if errorlevel 1 goto :fail_doctor

echo.
echo Done. Agent mode is ON and the RAG index was rebuilt.
echo Restart LM Studio and reconnect MCP servers.
pause
exit /b 0

:RunRag
echo.
echo === rag.ps1 %* ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\rag.ps1" %*
exit /b %ERRORLEVEL%

:fail_install
echo.
echo Install failed. See messages above.
pause
exit /b 1

:fail_config
echo.
echo Knowledge configuration failed. See messages above.
pause
exit /b 1

:fail_rag
echo.
echo RAG setup failed while running rag.ps1. See messages above.
pause
exit /b 1

:fail_doctor
echo.
echo RAG index was built, but doctor still reports issues. See messages above.
pause
exit /b 1
