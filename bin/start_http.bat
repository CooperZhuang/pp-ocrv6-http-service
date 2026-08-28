@echo off
REM ============================================================
REM  start_http.bat - Start the PP-OCRv6 local HTTP OCR service
REM  (NO console window - pure background process).
REM
REM  Launches bin\run_hidden.vbs via wscript, which starts
REM  `python ocr_service.py http` with a hidden window (style 0).
REM  Settings come from config.json (host/port/engine/device/
REM  idle_unload_s).
REM
REM  To run with console output (debugging), use directly:
REM    .venv\Scripts\python.exe ocr_service.py http
REM ============================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul

start "" wscript.exe "%~dp0run_hidden.vbs"
exit /b 0
