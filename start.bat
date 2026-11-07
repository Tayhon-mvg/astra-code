@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=run"

if /I "%MODE%"=="help" goto usage
if /I "%MODE%"=="-h" goto usage
if /I "%MODE%"=="--help" goto usage

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

where py >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
) else (
    set "PY=python"
)

echo ============================================
echo  Astra Code - bootstrap / build / test
echo  mode: %MODE%
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto fail
) else (
    echo [1/4] Virtual environment: OK
)

set "VENV=%CD%\.venv\Scripts\python.exe"

echo [2/4] Installing dependencies...
"%VENV%" -m pip install --upgrade pip --disable-pip-version-check
if errorlevel 1 goto fail
"%VENV%" -m pip install -e ".[dev]" --disable-pip-version-check
if errorlevel 1 goto fail

echo [3/4] Running tests...
"%VENV%" -m pytest -q
if errorlevel 1 goto fail

if /I "%MODE%"=="test" goto done

if /I "%MODE%"=="build" (
    echo [4/4] Building wheel and sdist...
    "%VENV%" -m build
    if errorlevel 1 goto fail
    echo.
    echo Build artifacts are in .\dist
    goto done
)

if /I "%MODE%"=="demo" (
    echo [4/4] Offline demo...
    "%VENV%" -m astra demo
    if errorlevel 1 goto fail
    goto done
)

if /I "%MODE%"=="doctor" (
    echo [4/4] Diagnostics...
    "%VENV%" -m astra doctor
    if errorlevel 1 goto fail
    goto done
)

if /I "%MODE%"=="run" (
    echo [4/4] Starting Astra...
    echo.
    "%VENV%" -m astra
    if errorlevel 1 goto fail
    goto done
)

echo Unknown mode: %MODE%
goto usage

:done
echo.
echo Done.
if not "%ASTRA_NO_PAUSE%"=="1" pause
exit /b 0

:usage
echo.
echo Usage: start.bat [run^|test^|demo^|doctor^|build^|help]
echo.
echo   run     install, test, then launch interactive Astra (default)
echo   test    install and run pytest only
echo   demo    install, test, then run the offline demo
echo   doctor  install, test, then run diagnostics
echo   build   install, test, then build wheel + sdist into .\dist
echo.
exit /b 0

:fail
echo.
echo FAILED. Fix the error above and run start.bat again.
if not "%ASTRA_NO_PAUSE%"=="1" pause
exit /b 1
