@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set SERVICE_NAME=ReclamationMonitor
set PROJECT_DIR=%~dp0..

:menu
cls
echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║         Reclamation Monitor - Control Panel        ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.

:: Get service status
nssm.exe status %SERVICE_NAME% >nul 2>&1
if %errorLevel% equ 0 (
    for /f "tokens=*" %%i in ('nssm.exe status %SERVICE_NAME% 2^>nul') do set STATUS=%%i
) else (
    set STATUS=НЕ УСТАНОВЛЕН
)
echo   Статус сервиса: !STATUS!

:: Check Ollama
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorLevel% equ 0 (
    echo   Ollama: работает
) else (
    echo   Ollama: НЕ РАБОТАЕТ!
)
echo.
echo ═══════════════════════════════════════════════════════════
echo.
echo   [1] Запустить сервис
echo   [2] Остановить сервис
echo   [3] Перезапустить сервис
echo   [4] Статус и диагностика
echo   [5] Просмотр логов
echo   [6] Установить сервис (требует Админа)
echo   [7] Удалить сервис (требует Админа)
echo.
echo   [0] Выход
echo.
echo ═══════════════════════════════════════════════════════════

set /p choice="  Выбор: "

if "%choice%"=="1" goto start_service
if "%choice%"=="2" goto stop_service
if "%choice%"=="3" goto restart_service
if "%choice%"=="4" goto status
if "%choice%"=="5" goto logs
if "%choice%"=="6" goto install
if "%choice%"=="7" goto uninstall
if "%choice%"=="0" exit /b 0
goto menu

:start_service
echo.
nssm.exe start %SERVICE_NAME%
timeout /t 2 >nul
goto menu

:stop_service
echo.
nssm.exe stop %SERVICE_NAME%
timeout /t 2 >nul
goto menu

:restart_service
echo.
nssm.exe restart %SERVICE_NAME%
timeout /t 2 >nul
goto menu

:status
cls
echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║                    Диагностика системы                    ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.
echo [Сервис %SERVICE_NAME%]
nssm.exe status %SERVICE_NAME% 2>nul || echo   Не установлен
echo.
echo [Ollama]
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorLevel% equ 0 (
    echo   API: работает
    curl -s http://localhost:11434/api/tags 2>nul | findstr "name"
) else (
    echo   API: НЕ ОТВЕЧАЕТ!
)
echo.
echo [Последние записи лога]
if exist "%PROJECT_DIR%\logs\service_stderr.log" (
    echo --- service_stderr.log ---
    powershell -Command "Get-Content '%PROJECT_DIR%\logs\service_stderr.log' -Tail 10" 2>nul
) else if exist "%PROJECT_DIR%\email_processor.log" (
    echo --- email_processor.log ---
    powershell -Command "Get-Content '%PROJECT_DIR%\email_processor.log' -Tail 10" 2>nul
)
echo.
pause
goto menu

:logs
cls
echo.
echo ╔═══════════════════════════════════════════════════════════╗
echo ║                     Просмотр логов                        ║
echo ╚═══════════════════════════════════════════════════════════╝
echo.
echo   [1] service_stderr.log (основной)
echo   [2] service_stdout.log
echo   [3] email_processor.log
echo   [4] Назад
echo.
set /p logchoice="  Выбор: "

if "%logchoice%"=="1" (
    echo.
    echo Логи в реальном времени (Ctrl+C для выхода)
    echo ─────────────────────────────────────────────
    powershell -Command "Get-Content '%PROJECT_DIR%\logs\service_stderr.log' -Wait -Tail 30"
)
if "%logchoice%"=="2" (
    powershell -Command "Get-Content '%PROJECT_DIR%\logs\service_stdout.log' -Wait -Tail 30"
)
if "%logchoice%"=="3" (
    powershell -Command "Get-Content '%PROJECT_DIR%\email_processor.log' -Wait -Tail 30"
)
goto menu

:install
echo.
echo Запускаю установку (требуется Администратор)...
powershell -Command "Start-Process '%~dp0install.bat' -Verb RunAs"
timeout /t 3 >nul
goto menu

:uninstall
echo.
set /p confirm="Удалить сервис? [y/N]: "
if /i "%confirm%"=="y" (
    powershell -Command "Start-Process '%~dp0uninstall.bat' -Verb RunAs"
)
timeout /t 3 >nul
goto menu
