@echo off
chcp 65001 >nul
"%~dp0nssm.exe" stop ReclamationMonitor
echo Сервис остановлен.
