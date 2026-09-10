@echo off
REM ============================================================
REM  prepare_env.bat - Prepare the Python environment for the
REM  PP-OCRv6 HTTP OCR service (for deployment on other machines).
REM
REM  NOTE: 推荐用 bin\deploy_env.bat (uv + CPython 3.11 + 依赖 + 环境自检);
REM        本脚本是本机「没有 uv」时的 pip 回退路径, 要求系统已有 Python 3.11.
REM
REM  优先使用 uv 安装依赖; 若本机无 uv 或 uv 安装失败, 自动回退
REM  到普通 pip 方式. 适配未装 GPU/CUDA 的任意 Windows 机器.
REM
REM  Usage:  prepare_env.bat
REM ============================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"

set "VENV_PY=.venv\Scripts\python.exe"
set "REQ=requirements.txt"

if not exist "%REQ%" (
    echo ERROR: %REQ% not found in %~dp0
    pause
    exit /b 1
)

REM ====== Locate uv ======
set "UV="
where uv >nul 2>nul && set "UV=uv"

REM ====== Step 1: create venv if missing ======
if exist "%VENV_PY%" (
    echo [1/3] Using existing venv .venv ...
) else if defined UV (
    echo [1/3] Creating venv with uv ...
    uv venv --python 3.11
) else (
    echo [1/3] Creating venv with python -m venv ...
    python -m venv .venv
)

if not exist "%VENV_PY%" (
    echo ERROR: Failed to create the virtual environment.
    pause
    exit /b 1
)

REM ====== Step 2: install deps (prefer uv, fallback pip) ======
if defined UV (
    echo [2/3] Installing dependencies via uv pip ...
    uv pip install --python "%VENV_PY%" -r "%REQ%"
    if not errorlevel 1 (
        echo [3/3] Done (uv). Environment ready.
        goto :done
    )
    echo WARN: uv install failed, falling back to pip ...
)

echo [2/3] Installing dependencies via pip ...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r "%REQ%"
if errorlevel 1 (
    echo ERROR: Dependency install failed.
    pause
    exit /b 1
)
echo [3/3] Done (pip). Environment ready.

:done
echo.
echo ============================================================
echo  Environment ready:
echo    venv : .venv
echo    deps : installed from %REQ%
echo    next : bin\start_http.bat   (start HTTP service, headless)
echo          bin\install_scheduled_task.bat   (autostart at logon)
echo.
echo  Note: first service run downloads ONNX models once (online).
echo ============================================================
pause
exit /b 0
