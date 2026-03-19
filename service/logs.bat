@echo off
chcp 65001 >nul
setlocal

set PROJECT_DIR=%~dp0..

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Reclamation Monitor - Logs           ║
echo ╚══════════════════════════════════════════════╝
echo.
echo Выберите лог:
echo   1. Сервис stderr (ошибки и инфо)
echo   2. Сервис stdout
echo   3. Email processor log
echo   4. Все логи (последние 50 строк)
echo.

set /p choice="Выбор [1-4]: "

if "%choice%"=="1" (
    echo.
    echo === service_stderr.log (Ctrl+C для выхода) ===
    powershell -Command "Get-Content '%PROJECT_DIR%\logs\service_stderr.log' -Wait -Tail 50"
)
if "%choice%"=="2" (
    echo.
    echo === service_stdout.log (Ctrl+C для выхода) ===
    powershell -Command "Get-Content '%PROJECT_DIR%\logs\service_stdout.log' -Wait -Tail 50"
)
if "%choice%"=="3" (
    echo.
    echo === email_processor.log (Ctrl+C для выхода) ===
    powershell -Command "Get-Content '%PROJECT_DIR%\email_processor.log' -Wait -Tail 50"
)
if "%choice%"=="4" (
    echo.
    echo === Последние 50 строк из всех логов ===
    echo.
    echo --- service_stderr.log ---
    if exist "%PROJECT_DIR%\logs\service_stderr.log" (
        powershell -Command "Get-Content '%PROJECT_DIR%\logs\service_stderr.log' -Tail 20"
    )
    echo.
    echo --- email_processor.log ---
    if exist "%PROJECT_DIR%\email_processor.log" (
        powershell -Command "Get-Content '%PROJECT_DIR%\email_processor.log' -Tail 30"
    )
    pause
)
