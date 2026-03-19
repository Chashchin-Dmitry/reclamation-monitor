@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: ========================================
::   Reclamation Monitor - Install
:: ========================================

set SERVICE_NAME=ReclamationMonitor
set PROJECT_DIR=%~dp0..
set NSSM_URL=https://nssm.cc/release/nssm-2.24.zip

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Reclamation Monitor - Installation   ║
echo ╚══════════════════════════════════════════════╝
echo.

:: 1. Check admin rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Требуются права Администратора!
    echo         ПКМ → "Запуск от имени администратора"
    pause
    exit /b 1
)
echo [OK] Права администратора

:: 2. Check Python venv
if not exist "%PROJECT_DIR%\venv\Scripts\python.exe" (
    echo [ERROR] venv не найден! Сначала запустите setup.bat
    pause
    exit /b 1
)
echo [OK] Python venv найден

:: 3. Check Ollama
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorLevel% neq 0 (
    echo [WARN] Ollama не запущена! Сервис не сможет анализировать письма.
    echo        Запустите Ollama перед стартом сервиса.
)
echo [OK] Ollama проверена

:: 4. Download NSSM if needed
if not exist "%~dp0nssm.exe" (
    echo [....] Скачиваю NSSM...
    curl -L -o "%~dp0nssm.zip" %NSSM_URL% 2>nul
    if !errorLevel! neq 0 (
        echo [ERROR] Не удалось скачать NSSM
        pause
        exit /b 1
    )
    tar -xf "%~dp0nssm.zip" -C "%~dp0"
    copy "%~dp0nssm-2.24\win64\nssm.exe" "%~dp0" >nul
    rmdir /s /q "%~dp0nssm-2.24"
    del "%~dp0nssm.zip"
)
echo [OK] NSSM готов

:: 5. Remove old service if exists
"%~dp0nssm.exe" status %SERVICE_NAME% >nul 2>&1
if %errorLevel% equ 0 (
    echo [....] Удаляю старый сервис...
    "%~dp0nssm.exe" stop %SERVICE_NAME% >nul 2>&1
    "%~dp0nssm.exe" remove %SERVICE_NAME% confirm >nul 2>&1
)

:: 6. Install service
echo [....] Устанавливаю сервис...
"%~dp0nssm.exe" install %SERVICE_NAME% "%PROJECT_DIR%\venv\Scripts\python.exe" "%PROJECT_DIR%\email_processor_improved.py"

:: 7. Configure service
"%~dp0nssm.exe" set %SERVICE_NAME% AppDirectory "%PROJECT_DIR%"
"%~dp0nssm.exe" set %SERVICE_NAME% AppEnvironmentExtra "TEST_MODE=monitor" "USE_CURRENT_DATE=true" "PYTHONIOENCODING=utf-8"
"%~dp0nssm.exe" set %SERVICE_NAME% DisplayName "Reclamation Monitor"
"%~dp0nssm.exe" set %SERVICE_NAME% Description "Автоматическая обработка рекламаций из входящей почты"
"%~dp0nssm.exe" set %SERVICE_NAME% Start SERVICE_AUTO_START
"%~dp0nssm.exe" set %SERVICE_NAME% AppStdout "%PROJECT_DIR%\logs\service_stdout.log"
"%~dp0nssm.exe" set %SERVICE_NAME% AppStderr "%PROJECT_DIR%\logs\service_stderr.log"
"%~dp0nssm.exe" set %SERVICE_NAME% AppStdoutCreationDisposition 4
"%~dp0nssm.exe" set %SERVICE_NAME% AppStderrCreationDisposition 4
"%~dp0nssm.exe" set %SERVICE_NAME% AppRotateFiles 1
"%~dp0nssm.exe" set %SERVICE_NAME% AppRotateBytes 10485760
"%~dp0nssm.exe" set %SERVICE_NAME% AppRestartDelay 10000

:: 8. Create logs directory
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"

echo [OK] Сервис установлен
echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Установка завершена!                        ║
echo ╠══════════════════════════════════════════════╣
echo ║  Команды:                                    ║
echo ║    start.bat   - Запустить                   ║
echo ║    stop.bat    - Остановить                  ║
echo ║    status.bat  - Статус                      ║
echo ║    logs.bat    - Смотреть логи               ║
echo ║    uninstall.bat - Удалить сервис            ║
echo ╚══════════════════════════════════════════════╝
echo.

set /p START_NOW="Запустить сервис сейчас? [Y/n]: "
if /i not "%START_NOW%"=="n" (
    "%~dp0nssm.exe" start %SERVICE_NAME%
    echo [OK] Сервис запущен!
)

pause
