# Reclamation Monitor

Автоматическая система обработки рекламаций из email: IMAP → AI-классификация → Bitrix24.

## Что делает

1. Мониторит входящую почту каждые N минут (IMAP)
2. Классифицирует письма: рекламация или нет (blacklist + LLaMA)
3. Анализирует через LLM (Ollama + Qwen3): продукт, проблема, серийник, категория
4. OCR для сканов и PDF-вложений (Tesseract)
5. Создаёт запись в Bitrix24 с заполненными полями
6. Пересылает ответственным сотрудникам

## Быстрый старт

```bash
# 1. Скопируй .env.example → .env, заполни реальные данные
cp .env.example .env

# 2. Установка зависимостей
setup.bat          # Windows
# или: pip install -r requirements.txt

# 3. Запуск
python email_processor_improved.py

# 4. Или как Windows-сервис (от Администратора)
cd service && install.bat
```

## Конфигурация

| Параметр | Описание |
|----------|----------|
| `EMAIL_USER` / `IMAP_PASSWORD` | Почтовый ящик для мониторинга |
| `BITRIX24_WEBHOOK` | Вебхук REST API Bitrix24 |
| `LLAMA_API_URL` / `LLAMA_MODEL` | Ollama endpoint и модель |
| `TEST_MODE` | `monitor` — непрерывно, `email` — по дате, `demo` — без почты |
| `OUR_EMAIL_DOMAINS` | Домены компании (фильтр исходящих) |

Подробнее: `.env.example`

## Структура

```
├── email_processor_improved.py      # Главный процессор
├── reclamation_classifier.py        # Классификатор + blacklist
├── llama_api_module.py              # LLaMA/Ollama API
├── reclamation_bitrix_connector.py  # Создание записей в Bitrix24
├── improved_llama_prompts.py        # Промпты для LLaMA
├── ocr_processor.py                 # OCR для сканов
├── bitrix24_integration.py          # Bitrix24 REST API wrapper
├── classifier_config.json           # Blacklist + ключевые слова
├── bitrix_field_mapping.json        # Маппинг полей Bitrix (настроить под свой инстанс)
├── docs/                            # Детальная документация
└── service/                         # Windows-сервис (NSSM)
```

## Pipeline

```
Email → Blacklist? → (да) skip
                  → (нет) → LLaMA → is_reclamation?
                                  → (да) → OCR вложений → Bitrix24 → Пересылка
                                  → (нет) skip
```

**Причинно-следственные связи** при отладке задокументированы в `docs/troubleshooting.md` и `docs/migration-history.md` по схеме: Симптом → Причина → Решение.

## Требования

- Python 3.10+
- Ollama + модель (по умолчанию `qwen3:30b-a3b`)
- Tesseract OCR (для сканов)
- Windows (для сервиса) или Linux/macOS (ручной запуск)

## Документация

| Файл | Содержание |
|------|------------|
| `docs/setup.md` | Установка, Ollama, .env, чеклист |
| `docs/architecture.md` | Компоненты, data flow, API |
| `docs/bitrix24.md` | Поля, маппинг, настройка |
| `docs/troubleshooting.md` | Известные проблемы и решения |
| `docs/migration-history.md` | История изменений с причинами |
| `docs/service.md` | Windows-сервис NSSM |
