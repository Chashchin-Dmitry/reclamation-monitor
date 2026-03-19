# Windows Service (EpotosReclamation)

Сервис работает автономно, переживает перезагрузку, автоперезапускается при падении.

## Установка (один раз, от Администратора)
```cmd
cd service
install.bat
```

## Управление (рекомендуется menu.bat)
```cmd
cd service
menu.bat               # Интерактивное меню (рекомендуется)
# Или отдельные команды:
start.bat              # Запустить
stop.bat               # Остановить
restart.bat            # Перезапустить (после git pull)
status.bat             # Статус: сервис + Ollama + логи
logs.bat               # Просмотр логов в реальном времени
uninstall.bat          # Удалить сервис (от Админа)
```

## Через NSSM напрямую
```cmd
nssm start EpotosReclamation
nssm stop EpotosReclamation
nssm status EpotosReclamation
nssm restart EpotosReclamation
```

## Логи сервиса
```
logs/service_stdout.log   # stdout
logs/service_stderr.log   # stderr (основной)
email_processor.log       # лог приложения
```

## Структура service/
```
service/
├── menu.bat        # Единое интерактивное меню
├── install.bat     # Установка сервиса
├── start.bat       # Запуск
├── stop.bat        # Остановка
├── restart.bat     # Перезапуск
├── status.bat      # Диагностика
├── logs.bat        # Просмотр логов
├── uninstall.bat   # Удаление
├── nssm.exe        # Non-Sucking Service Manager
└── README.md       # Документация
```

## Команды запуска (без сервиса)

```bash
# Monitor mode (continuous) — основной режим
TEST_MODE=monitor python email_processor_improved.py

# Process specific date
TEST_MODE=email TEST_DATE=25-Nov-2025 python email_processor_improved.py

# Demo mode (no email connection)
TEST_MODE=demo python email_processor_improved.py

# Test Bitrix24 connection
python test_connectivity_bitrix.py

# Process date range (catch up missed dates) — в archive/utils/
python archive/utils/process_date_range.py
```
