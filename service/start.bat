@echo off
chcp 65001 >nul
"%~dp0nssm.exe" start ReclamationMonitor
echo Сервис запущен. Проверка статуса:
"%~dp0nssm.exe" status ReclamationMonitor
