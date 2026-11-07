@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "VENV=%CD%\.venv\Scripts\python.exe"

echo Installing dependencies...
"%VENV%" -m pip install --upgrade pip --disable-pip-version-check --quiet
if errorlevel 1 exit /b 1

"%VENV%" -m pip install -e ".[dev]" --disable-pip-version-check --quiet
if errorlevel 1 exit /b 1

exit /b 0
