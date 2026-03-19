@echo off
chcp 65001 >nul
setlocal

set PROJECT_DIR=%~dp0..

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Reclamation Monitor - Status         ║
echo ╚══════════════════════════════════════════════╝
echo.

:: Service status
echo [Сервис]
"%~dp0nssm.exe" status ReclamationMonitor 2>nul || echo   Не установлен
echo.

:: Ollama status
echo [Ollama]
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorLevel% equ 0 (
    echo   Работает
    for /f "tokens=*" %%i in ('curl -s http://localhost:11434/api/tags 2^>nul ^| findstr "name"') do echo   %%i
) else (
    echo   НЕ РАБОТАЕТ!
)
echo.

:: Last processed
echo [Последняя обработка]
if exist "%PROJECT_DIR%\processed_reclamations.json" (
    for /f "tokens=2 delims=:" %%a in ('findstr "last_run" "%PROJECT_DIR%\processed_reclamations.json"') do echo   %%a
) else (
    echo   Нет данных
)
echo.

:: Log tail
echo [Последние записи лога]
if exist "%PROJECT_DIR%\logs\service_stderr.log" (
    powershell -Command "Get-Content '%PROJECT_DIR%\logs\service_stderr.log' -Tail 5"
) else if exist "%PROJECT_DIR%\email_processor.log" (
    powershell -Command "Get-Content '%PROJECT_DIR%\email_processor.log' -Tail 5"
) else (
    echo   Логи не найдены
)
echo.

pause
