@echo off
chcp 65001 >nul

echo.
echo ╔══════════════════════════════════════════════╗
echo ║  Reclamation Monitor - Uninstall      ║
echo ╚══════════════════════════════════════════════╝
echo.

:: Check admin
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] Требуются права Администратора!
    pause
    exit /b 1
)

set /p CONFIRM="Удалить сервис ReclamationMonitor? [y/N]: "
if /i not "%CONFIRM%"=="y" (
    echo Отменено.
    pause
    exit /b 0
)

echo Останавливаю сервис...
"%~dp0nssm.exe" stop ReclamationMonitor >nul 2>&1

echo Удаляю сервис...
"%~dp0nssm.exe" remove ReclamationMonitor confirm

echo.
echo [OK] Сервис удалён.
echo     Файлы проекта и логи сохранены.
pause
