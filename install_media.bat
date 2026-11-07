@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "VENV=%CD%\.venv\Scripts\python.exe"

echo Installing media dependencies (Pillow, opencv-python)...
"%VENV%" -m pip install Pillow opencv-python --quiet
if errorlevel 1 exit /b 1

echo Media tools ready: media_tools.py
exit /b 0
