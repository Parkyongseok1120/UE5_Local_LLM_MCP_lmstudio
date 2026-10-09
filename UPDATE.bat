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
for %%I in ("%MCP_CONFIG%") do set "UPDATE_LMSTUDIO_HOME=%%~dpI."

set "PYTHON_CMD="
if not defined UPDATE_STATE_HOME set "UPDATE_STATE_HOME=%USERPROFILE%\.evidence-first"
if defined UPDATE_PYTHON goto explicit_python
if not exist "%UPDATE_STATE_HOME%\runtime-python.path" goto discover_python
rem The installer writes this pointer as UTF-8, regardless of CMD code page.
set "UPDATE_PATH_CODEPAGE="
for /f "tokens=2 delims=:" %%C in ('chcp') do set "UPDATE_PATH_CODEPAGE=%%C"
if not defined UPDATE_PATH_CODEPAGE goto discover_python
chcp 65001 >nul
if errorlevel 1 goto discover_python
set /p "UPDATE_PYTHON="<"%UPDATE_STATE_HOME%\runtime-python.path"
chcp %UPDATE_PATH_CODEPAGE% >nul
if not defined UPDATE_PYTHON goto discover_python
if not exist "%UPDATE_PYTHON%" goto discover_python
"%UPDATE_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto discover_python
set PYTHON_CMD="%UPDATE_PYTHON%"
goto preflight

:explicit_python
if not exist "%UPDATE_PYTHON%" goto python_missing
"%UPDATE_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto python_missing
set PYTHON_CMD="%UPDATE_PYTHON%"
goto preflight

:discover_python
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
    goto preflight
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto preflight
)

:use_python
where python >nul 2>nul
if errorlevel 1 goto python_missing
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto python_missing
set "PYTHON_CMD=python"

:preflight
echo Checking all installed MCP bindings before updating dependencies...
%PYTHON_CMD% scripts\update_unity_mcp.py --mcp-config "%MCP_CONFIG%" --if-present --dry-run
if errorlevel 1 goto unity_failed
%PYTHON_CMD% scripts\update_unreal_mcp.py --mcp-config "%MCP_CONFIG%" --if-present --dry-run
if errorlevel 1 goto unreal_failed
if "%UPDATE_DRY_RUN%"=="1" goto check_plugin

:update_unity
echo Checking the existing Unity MCP binding and runtime...
%PYTHON_CMD% scripts\update_unity_mcp.py --mcp-config "%MCP_CONFIG%" --if-present
if errorlevel 1 goto unity_failed

:update_unreal
echo Checking the existing Unreal Agent and RAG runtimes...
%PYTHON_CMD% scripts\update_unreal_mcp.py --mcp-config "%MCP_CONFIG%" --if-present
if errorlevel 1 goto unreal_failed

:update_plugin
echo Updating the context compactor plugin...
%PYTHON_CMD% install.py --profile custom --components context_compactor --yes --lmstudio-home "%UPDATE_LMSTUDIO_HOME%" --state-home "%UPDATE_STATE_HOME%"
if errorlevel 1 goto plugin_failed
goto success

:check_plugin
%PYTHON_CMD% install.py --profile custom --components context_compactor --yes --dry-run --skip-runtime-bootstrap --lmstudio-home "%UPDATE_LMSTUDIO_HOME%" --state-home "%UPDATE_STATE_HOME%"
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
echo The selected MCP config folder is also the context compactor installation home.
echo Optional: UPDATE_PYTHON=absolute Python path; UPDATE_STATE_HOME=installer state folder.
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
echo Python 3.10 or newer was not found. Set UPDATE_PYTHON or UPDATE_STATE_HOME for a custom managed runtime, or run INSTALL.bat first. 1>&2
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
