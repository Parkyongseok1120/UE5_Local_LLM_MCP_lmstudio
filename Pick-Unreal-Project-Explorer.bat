@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\pick_active_project.ps1" -Explorer %*
if errorlevel 1 pause
