@echo off
setlocal

set "INSTALLER_DIR=%~dp0"
set "REPO_ROOT=%~dp0.."

cd /d "%REPO_ROOT%"

echo Unreal58-RAG Portable MCP Installer (Agent Mode + RAG Index)
echo.
echo Agent mode: file writes, commands, and Unreal builds are ENABLED.
echo This will install MCP settings, configure project paths, run the full indexing pipeline, and run doctor.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER_DIR%Install-UnrealMcp.ps1" -EnableAgentMode
if errorlevel 1 goto :fail_install

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER_DIR%Configure-Knowledge.ps1" -NonInteractive -SkipBuild -WorkspaceRoot "%REPO_ROOT%"
if errorlevel 1 goto :fail_config

powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER_DIR%Sync-InstallMachinePaths.ps1" -PortableRoot "%REPO_ROOT%"
if errorlevel 1 goto :fail_config

powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\run_index_pipeline.ps1" -WorkspaceRoot "%REPO_ROOT%"
if errorlevel 1 goto :fail_rag

call :RunRag doctor
if errorlevel 1 goto :fail_doctor

echo.
echo Done. Agent mode is ON and the RAG index was rebuilt.
echo Restart LM Studio and reconnect MCP servers.
echo.
echo Editor metadata: exported automatically during indexing when autoEditorExport is enabled.
echo If export failed, run: .\rag.ps1 export-editor-metadata
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
echo RAG indexing pipeline failed. See messages above.
pause
exit /b 1

:fail_doctor
echo.
echo RAG index was built, but doctor still reports issues. See messages above.
pause
exit /b 1
