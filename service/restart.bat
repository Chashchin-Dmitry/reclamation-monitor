@echo off
chcp 65001 >nul
echo Перезапуск сервиса...
"%~dp0nssm.exe" restart ReclamationMonitor
echo Готово. Статус:
"%~dp0nssm.exe" status ReclamationMonitor
