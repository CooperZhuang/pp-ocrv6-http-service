@echo off
REM ============================================================
REM  deploy_env.bat - One-click environment setup for the
REM  PP-OCRv6 HTTP OCR service on a fresh deployment machine.
REM
REM  Requirements on the target machine:
REM    * uv in PATH        (https://docs.astral.sh/uv/)
REM    * internet access   (uv fetches CPython 3.11 + PyPI deps)
REM  Python does NOT need to be installed: uv downloads CPython
REM  3.11 by itself, which is the version this project is
REM  verified against (CI matrix and local dev are both 3.11).
REM
REM  What it does:
REM    [1/4] check uv                [2/4] create .venv (CPython 3.11)
REM    [3/4] install requirements    [4/4] verify imports + versions
REM
REM  Usage:
REM    bin\deploy_env.bat                 setup (re-runnable; keeps .venv)
REM    bin\deploy_env.bat --recreate      delete .venv first, rebuild
REM    bin\deploy_env.bat --no-pause      do not pause at the end
REM
REM  Next: bin\selftest_ocr.bat   (first run downloads models, ~146MB)
REM        bin\start_http.bat     (start the service in the background)
REM ============================================================
setlocal
cd /d "%~dp0.."
chcp 65001 >nul
set "PYTHONUTF8=1"

set "PAUSE_AT_END=1"
set "RECREATE="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-pause" set "PAUSE_AT_END="
if /i "%~1"=="--recreate" set "RECREATE=1"
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
shift
goto parse_args
:args_done

set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ=requirements.txt"

echo ============================================================
echo  PP-OCRv6 HTTP OCR service - environment setup
echo  project : %CD%
echo ============================================================
echo.

if not exist "%REQ%" (
    echo ERROR: %REQ% not found in %CD%
    echo        Run this script from inside the project directory.
    goto fail
)

REM ====== disk space (informational) ======
for %%d in ("%~d0") do set "DRVLETTER=%%~d"
for /f "delims=" %%f in ('powershell -NoProfile -Command "[math]::Round((Get-PSDrive %DRVLETTER:~0,1%).Free/1GB,1)" 2^>nul') do set "FREE_GB=%%f"
if defined FREE_GB echo Free space on %DRVLETTER% %FREE_GB% GB ^(venv ~3 GB + models ~0.15 GB; 5 GB recommended^)
echo.

REM ====== [1/4] uv ======
echo [1/4] Checking uv ...
where uv >nul 2>nul
if errorlevel 1 (
    echo ERROR: uv not found in PATH.
    echo        Install it first ^(no admin needed^), then open a NEW terminal:
    echo          powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo.
    echo        No uv? Use prepare_env.bat instead ^(pip fallback, needs Python 3.11^).
    goto fail
)
for /f "delims=" %%V in ('uv --version') do echo       %%V

REM ====== [2/4] virtual environment ======
if defined RECREATE if exist "%VENV_DIR%" (
    echo [2/4] --recreate: removing existing %VENV_DIR% ...
    rmdir /s /q "%VENV_DIR%"
    if exist "%VENV_DIR%" (
        echo ERROR: could not remove %VENV_DIR% ^(is the service still running?^).
        goto fail
    )
)
if exist "%VENV_PY%" (
    echo [2/4] Reusing existing venv %VENV_DIR% ...
) else (
    echo [2/4] Creating venv with CPython 3.11 ^(uv downloads it if needed^) ...
    uv venv --python 3.11 "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        goto fail
    )
)
if not exist "%VENV_PY%" (
    echo ERROR: %VENV_PY% not found after venv creation.
    goto fail
)
echo       interpreter:
"%VENV_PY%" -c "import sys;print('      ',sys.executable);print('      Python',sys.version.split()[0])"
if errorlevel 1 (
    echo ERROR: the venv interpreter does not run.
    goto fail
)

REM ====== [3/4] dependencies ======
echo [3/4] Installing dependencies from %REQ% ...
uv pip install --python "%VENV_PY%" -r "%REQ%"
if errorlevel 1 (
    echo.
    echo ERROR: dependency installation failed.
    echo        ONNX Runtime is the only inference engine needed. Do NOT install
    echo        paddlepaddle / paddlepaddle-gpu on a machine without an NVIDIA GPU.
    goto fail
)

REM ====== [4/4] verify ======
echo [4/4] Verifying the environment ...
"%VENV_PY%" -c "import sys,importlib.metadata as md;print('      python                ',sys.version.split()[0]);print('      fastapi               ',md.version('fastapi'));print('      uvicorn               ',md.version('uvicorn'));print('      python-multipart      ',md.version('python-multipart'));print('      onnxruntime           ',md.version('onnxruntime'));print('      paddleocr             ',md.version('paddleocr'));print('      paddlex               ',md.version('paddlex'));import ocr_service;print('      ocr_service import     OK')"
if errorlevel 1 (
    echo.
    echo ERROR: verification failed - see the traceback above.
    goto fail
)

echo.
echo ============================================================
echo  Environment ready.
echo    venv : %VENV_DIR%  ^(CPython 3.11^)
echo    deps : installed from %REQ%
echo.
echo  Next steps:
echo    1^) bin\selftest_ocr.bat            verify recognition ^(downloads models on first run^)
echo    2^) bin\start_http.bat              start the HTTP service ^(no window^)
echo    3^) bin\install_scheduled_task.bat  autostart at logon
echo ============================================================
if defined PAUSE_AT_END pause
exit /b 0

:usage
echo Usage: bin\deploy_env.bat [--recreate] [--no-pause]
echo   --recreate   remove .venv first and rebuild it from scratch
echo   --no-pause   do not wait for a key press at the end
if defined PAUSE_AT_END pause
exit /b 0

:fail
echo.
echo Environment setup FAILED - see the message above.
if defined PAUSE_AT_END pause
exit /b 1
