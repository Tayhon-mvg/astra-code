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
echo  Astra Code - bootstrap / run
echo  mode: %MODE%
echo  tests are separate: start.bat test
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 goto fail
) else (
    echo [1/3] Virtual environment: OK
)

call .\install_deps.bat
if errorlevel 1 goto fail

if /I "%MODE%"=="test" (
    echo [3/3] Running tests on demand...
    "%VENV%" -m pytest -q
    if errorlevel 1 goto fail
    goto done
)

if /I "%MODE%"=="build" (
    echo [3/3] Building wheel and sdist...
    "%VENV%" -m build
    if errorlevel 1 goto fail
    echo.
    echo Build artifacts are in .\dist
    goto done
)

if /I "%MODE%"=="media-setup" (
    call .\install_media.bat
    if errorlevel 1 goto fail
    goto done
)

if /I "%MODE%"=="demo" (
    echo [3/3] Offline demo...
    "%VENV%" -m astra demo
    if errorlevel 1 goto fail
    goto done
)

if /I "%MODE%"=="doctor" (
    echo [3/3] Diagnostics...
    "%VENV%" -m astra doctor
    if errorlevel 1 goto fail
    goto done
)

if /I "%MODE%"=="run" (
    echo [3/3] Starting Astra...
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
echo Usage: start.bat [run^|test^|demo^|doctor^|build^|media-setup^|help]
echo.
echo   run          install dependencies and launch interactive Astra (default)
echo   test         install dependencies and run pytest on demand
echo   demo         install dependencies and run the offline demo
echo   doctor       install dependencies and run diagnostics
echo   build        install dependencies and build wheel + sdist into .\dist
echo   media-setup  install media dependencies (Pillow, opencv-python)
echo.
echo Tests are intentionally NOT run by run/demo/doctor/build.
echo.
exit /b 0

:fail
echo.
echo FAILED. Fix the error above and run start.bat again.
if not "%ASTRA_NO_PAUSE%"=="1" pause
exit /b 1
