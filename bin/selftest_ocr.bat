@echo off
REM ============================================================
REM  selftest_ocr.bat - One-click deployment self-test for the
REM  PP-OCRv6 HTTP OCR service.
REM
REM  What it checks:
REM    [1/4] Python version + runtime dependencies
REM    [2/4] model cache (missing -> first recognition downloads ~146MB)
REM    [3/4] real recognition of an image/PDF, printing the text
REM    [4/4] HTTP round-trip - only when the service is already
REM          listening; otherwise it is skipped (not a failure)
REM
REM  Usage:
REM    bin\selftest_ocr.bat                    bundled invoice sample
REM    bin\selftest_ocr.bat D:\invoices\a.png   your own image/PDF
REM    bin\selftest_ocr.bat --no-http           skip the HTTP probe
REM    bin\selftest_ocr.bat --port 18010        probe another port
REM    bin\selftest_ocr.bat --no-pause          do not pause at the end
REM
REM  Exit code: 0 = PASS, 1 = FAIL
REM ============================================================
setlocal
cd /d "%~dp0.."
chcp 65001 >nul
set "PYTHONUTF8=1"

set "PAUSE_AT_END=1"
set "PYARGS="
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--no-pause" (
    set "PAUSE_AT_END="
    shift
    goto parse_args
)
set PYARGS=%PYARGS% "%~1"
shift
goto parse_args
:args_done

set "VENV_PY=.venv\Scripts\python.exe"
set "SELFTEST=bin\selftest_ocr.py"

echo ============================================================
echo  PP-OCRv6 HTTP OCR service - deployment self-test
echo ============================================================

if not exist "%VENV_PY%" (
    echo ERROR: %VENV_PY% not found.
    echo        Run bin\deploy_env.bat first to set up the environment.
    goto fail
)
if not exist "%SELFTEST%" (
    echo ERROR: %SELFTEST% not found.
    goto fail
)

"%VENV_PY%" "%SELFTEST%" %PYARGS%
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo [OK] self-test PASSED
) else (
    echo [!!] self-test FAILED ^(exit code %RC%^)
)
if defined PAUSE_AT_END pause
exit /b %RC%

:fail
echo.
echo [!!] self-test could not run - see the message above.
if defined PAUSE_AT_END pause
exit /b 1
