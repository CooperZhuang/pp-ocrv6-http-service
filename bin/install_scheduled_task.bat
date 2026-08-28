@echo off
REM ============================================================
REM  install_scheduled_task.bat - Register PP-OCRv6 HTTP service
REM  as a Windows Task Scheduler task (autostart at logon).
REM
REM  NO ADMIN REQUIRED. Uses PowerShell Register-ScheduledTask to
REM  add a task running AS THE CURRENT USER at logon, registered
REM  at the root of the Task Scheduler library (no admin needed;
REM  custom root-library folders would require elevation).
REM
REM  The task runs `python ocr_service.py http` (config.json is
REM  read for host/port/engine/device/idle_unload_s).
REM
REM  Usage:
REM    install_scheduled_task.bat           install (or reinstall)
REM    install_scheduled_task.bat --remove  uninstall
REM ============================================================

setlocal
cd /d "%~dp0"

REM ====== Project root (script lives in bin\) & python path ======
for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHON_EXE=%PROJECT_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
REM 用 WScript 隐藏启动器 (run_hidden.vbs, window style 0) 启动服务:
REM 无任何控制台窗口, 纯后台进程. 不依赖 pythonw (uv venv 的 pythonw 可能是
REM 指向 base python.exe 的符号链接, 仍是控制台子系统).
set "LAUNCHER=%PROJECT_DIR%\bin\run_hidden.vbs"

if not exist "%PROJECT_DIR%\ocr_service.py" (
    echo ERROR: Cannot find ocr_service.py in %PROJECT_DIR%
    pause
    exit /b 1
)

set "TASK_FULL=PP-OCRv6\PP-OCRv6_HTTP"
set "TASK_PATH=\PP-OCRv6\"
set "TASK_NAME=PP-OCRv6_HTTP"

REM ====== Uninstall ======
if /i "%~1"=="--remove" (
    echo Removing task %TASK_NAME% (from folder) ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "\$t = Get-ScheduledTask -TaskPath '%TASK_PATH%' -TaskName '%TASK_NAME%' -ErrorAction SilentlyContinue;" ^
      "if (\$t) { Unregister-ScheduledTask -InputObject \$t -Confirm:\$false; Write-Host 'Removed.' } else { Write-Host 'Task not found.' }"
    pause
    exit /b 0
)

echo Registering task %TASK_NAME% (at logon, in folder %TASK_PATH%, no admin needed) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument '\"%LAUNCHER%\"' -WorkingDirectory '%PROJECT_DIR%';" ^
  "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME;" ^
  "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries;" ^
  "Register-ScheduledTask -TaskName '%TASK_FULL%' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null;" ^
  "Write-Host 'Task %TASK_FULL% registered (no admin needed).'"
if errorlevel 1 (
    echo ERROR: Failed to register the scheduled task.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  PP-OCRv6 HTTP service task registered (no admin needed):
echo  - Task : %TASK_FULL%
echo  - Runs : ocr_service.py http at current-user logon (config.json sets port/engine/etc.)
echo  - Config: host/port/engine/device/idle_unload_s in config.json
echo.
echo  To uninstall later: run this script with --remove
echo ============================================================
pause
exit /b 0
