@echo off
REM ============================================================
REM  deploy_env.bat - One-click environment setup for the
REM  PP-OCRv6 HTTP OCR service on a fresh deployment machine.
REM
REM  Preferred path (uv in PATH + internet):
REM    uv fetches CPython 3.11 by itself, so the target machine
REM    does NOT need Python preinstalled. 3.11 is the version this
REM    project is verified against (CI matrix and local dev).
REM
REM  Fallback path (no uv): uses the system `python` + pip.
REM    Requires Python 3.11~3.13 to be installed already; 3.12/3.13
REM    are accepted with a warning (verified on 3.11 only), and
REM    anything outside 3.11~3.13 is rejected with a clear message.
REM    Force this path with --pip.
REM
REM  What it does:
REM    [1/4] check toolchain        [2/4] create .venv (+ version gate)
REM    [3/4] install requirements   [4/4] verify imports + versions
REM
REM  Usage:
REM    bin\deploy_env.bat                 setup (re-runnable; keeps .venv)
REM    bin\deploy_env.bat --recreate      delete .venv first, rebuild
REM    bin\deploy_env.bat --pip           force the pip fallback (no uv)
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
set "FORCE_PIP="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-pause" set "PAUSE_AT_END="
if /i "%~1"=="--recreate" set "RECREATE=1"
if /i "%~1"=="--pip" set "FORCE_PIP=1"
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--help" goto usage
shift
goto parse_args
:args_done

set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ=requirements.txt"
set "CREATED_VENV="

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
if defined FREE_GB echo Free space on %DRVLETTER% %FREE_GB% GB ^(venv ~0.6 GB + models ~0.15 GB; 2 GB recommended^)
echo.

REM ====== [1/4] toolchain: uv (preferred) or python (fallback) ======
set "UV="
if not defined FORCE_PIP where uv >nul 2>nul && set "UV=uv"
echo [1/4] Checking the toolchain ...
if defined UV (
    for /f "delims=" %%V in ('uv --version') do echo       uv     %%V
    echo       mode   uv  ^(CPython 3.11 is fetched automatically^)
) else (
    if defined FORCE_PIP echo       --pip given: skipping uv on purpose.
    where python >nul 2>nul
    if errorlevel 1 (
        echo ERROR: neither uv nor python is available.
        echo        Install uv ^(recommended, no admin needed^), then open a NEW terminal:
        echo          powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
        goto fail
    )
    for /f "delims=" %%V in ('python -c "import sys;print(sys.version.split()[0], sys.executable)"') do echo       python %%V
    echo       mode   pip fallback  ^(needs Python 3.11~3.13; 3.11 recommended^)
)
echo.

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
) else if defined UV (
    echo [2/4] Creating venv with CPython 3.11 ^(uv downloads it if needed^) ...
    uv venv --python 3.11 "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        goto fail
    )
    set "CREATED_VENV=1"
) else (
    echo [2/4] Creating venv with the system python ...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: failed to create the virtual environment.
        goto fail
    )
    set "CREATED_VENV=1"
)
if not exist "%VENV_PY%" (
    echo ERROR: %VENV_PY% not found after venv creation.
    goto fail
)

REM 版本门禁: <3.11 或 >=3.14 直接拒绝, 3.12/3.13 放行但告警
"%VENV_PY%" -c "import sys;sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,14) else 1)"
if errorlevel 1 (
    echo.
    echo ERROR: unsupported Python interpreter:
    "%VENV_PY%" -c "import sys;print('      ',sys.version.split()[0],sys.executable)"
    echo        This project is verified on CPython 3.11 only.
    if defined CREATED_VENV (
        echo        Removing the venv just created ...
        rmdir /s /q "%VENV_DIR%"
    )
    echo        Recommended fix - install uv so this script fetches 3.11:
    echo          powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"
    echo        Then re-run:  bin\deploy_env.bat
    goto fail
)
"%VENV_PY%" -c "import sys;print('      interpreter:',sys.executable);print('      Python     ',sys.version.split()[0])"
"%VENV_PY%" -c "import sys;sys.exit(0 if sys.version_info[:2]==(3,11) else 1)"
if errorlevel 1 echo       WARN: not 3.11 - the project is only verified on CPython 3.11.
echo.

REM ====== [3/4] dependencies ======
if defined UV (
    echo [3/4] Installing dependencies with uv pip ...
    uv pip install --python "%VENV_PY%" -r "%REQ%"
) else (
    "%VENV_PY%" -m pip --version >nul 2>nul
    if errorlevel 1 (
        echo [3/4] Bootstrapping pip into the venv ...
        "%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
    )
    echo [3/4] Installing dependencies with pip ...
    "%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
    "%VENV_PY%" -m pip install -r "%REQ%"
)
if errorlevel 1 (
    echo.
    echo ERROR: dependency installation failed.
    echo        ONNX Runtime is the only inference engine needed. Do NOT install
    echo        paddlepaddle / paddlepaddle-gpu on a machine without an NVIDIA GPU.
    goto fail
)
echo.

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
echo    venv : %VENV_DIR%
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
echo Usage: bin\deploy_env.bat [--recreate] [--pip] [--no-pause]
echo   --recreate   remove .venv first and rebuild it from scratch
echo   --pip        force the pip fallback path (skip uv)
echo   --no-pause   do not wait for a key press at the end
if defined PAUSE_AT_END pause
exit /b 0

:fail
echo.
echo Environment setup FAILED - see the message above.
if defined PAUSE_AT_END pause
exit /b 1
