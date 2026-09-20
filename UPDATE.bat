@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "UPDATE_DRY_RUN=0"
if /I "%~1"=="--help" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--dry-run" (
    set "UPDATE_DRY_RUN=1"
    shift
)

set "MCP_CONFIG=%USERPROFILE%\.lmstudio\mcp.json"
if defined LMSTUDIO_HOME set "MCP_CONFIG=%LMSTUDIO_HOME%\mcp.json"
if not "%~1"=="" (
    set "MCP_CONFIG=%~1"
    shift
)
if not "%~1"=="" goto usage_error
if not exist "%MCP_CONFIG%" goto config_missing

set "PYTHON_CMD="
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
    goto update_unity
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto update_unity
)

:use_python
where python >nul 2>nul
if errorlevel 1 goto python_missing
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto python_missing
set "PYTHON_CMD=python"

:update_unity
echo Checking the existing Unity MCP binding and runtime...
if "%UPDATE_DRY_RUN%"=="1" goto check_unity
%PYTHON_CMD% scripts\update_unity_mcp.py --mcp-config "%MCP_CONFIG%" --if-present
if errorlevel 1 goto unity_failed
goto update_unreal

:check_unity
%PYTHON_CMD% scripts\update_unity_mcp.py --mcp-config "%MCP_CONFIG%" --if-present --dry-run
if errorlevel 1 goto unity_failed

:update_unreal
echo Checking the existing Unreal Agent and RAG runtimes...
if "%UPDATE_DRY_RUN%"=="1" goto check_unreal
%PYTHON_CMD% scripts\update_unreal_mcp.py --mcp-config "%MCP_CONFIG%" --if-present
if errorlevel 1 goto unreal_failed
goto update_plugin

:check_unreal
%PYTHON_CMD% scripts\update_unreal_mcp.py --mcp-config "%MCP_CONFIG%" --if-present --dry-run
if errorlevel 1 goto unreal_failed

:update_plugin
echo Updating the context compactor plugin...
set "INSTALL_NO_PAUSE=1"
if "%UPDATE_DRY_RUN%"=="1" goto check_plugin
call "%SCRIPT_DIR%INSTALL.bat" --profile custom --components context_compactor --yes
if errorlevel 1 goto plugin_failed
goto success

:check_plugin
call "%SCRIPT_DIR%INSTALL.bat" --profile custom --components context_compactor --yes --dry-run --skip-runtime-bootstrap
if errorlevel 1 goto plugin_failed
echo Dry run complete. No update was applied.
set "UPDATE_EXIT=0"
goto finish

:success
echo Update complete. Restart LM Studio to load the new MCP process and plugin.
set "UPDATE_EXIT=0"
goto finish

:usage
echo Usage: UPDATE.bat [--dry-run] [path-to-mcp.json]
echo Default MCP config: %%LMSTUDIO_HOME%%\mcp.json or %%USERPROFILE%%\.lmstudio\mcp.json
set "UPDATE_EXIT=0"
goto finish

:usage_error
echo Too many arguments. Run UPDATE.bat --help for usage. 1>&2
set "UPDATE_EXIT=2"
goto finish

:config_missing
echo MCP config not found: "%MCP_CONFIG%" 1>&2
echo Pass the existing mcp.json path as the final argument. 1>&2
set "UPDATE_EXIT=2"
goto finish

:python_missing
echo Python 3.10 or newer was not found. Run INSTALL.bat to set up the runtime first. 1>&2
set "UPDATE_EXIT=9009"
goto finish

:unity_failed
echo Unity MCP update check failed. The context compactor was not reinstalled. 1>&2
set "UPDATE_EXIT=1"
goto finish

:unreal_failed
echo Unreal Agent/RAG update check failed. The context compactor was not reinstalled. 1>&2
set "UPDATE_EXIT=1"
goto finish

:plugin_failed
echo Context compactor update failed. Check the installer output above. 1>&2
set "UPDATE_EXIT=1"

:finish
if /I "%UPDATE_NO_PAUSE%"=="1" exit /b %UPDATE_EXIT%
echo Press any key to exit.
pause >nul
exit /b %UPDATE_EXIT%
