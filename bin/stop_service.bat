@echo off
REM ============================================================
REM  stop_service.bat - Stop the PP-OCRv6 HTTP OCR service
REM
REM  Kills the python process started by start_http.bat for the
REM  given port (default 18010). Safe: only kills the process
REM  actually LISTENING on that 127.0.0.1 port.
REM ============================================================
setlocal
cd /d "%~dp0.."
chcp 65001 >nul

set "PORT=18010"
if not "%~1"=="" set "PORT=%~1"

echo Looking for process listening on 127.0.0.1:%PORT% ...
set "FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%%PORT%%" ^| findstr "LISTENING"') do (
    echo Killing PID %%P ...
    taskkill /PID %%P /T /F >nul 2>&1
    set "FOUND=1"
)
if "%FOUND%"=="0" (
    echo No service listening on 127.0.0.1:%PORT%. Nothing to stop.
) else (
    echo Stopped.
)
exit /b 0
