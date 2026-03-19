# Архитектура системы

## Структура проекта

```
Рекламации/
├── email_processor_improved.py    # Главный процессор (orchestrator)
├── reclamation_classifier.py      # Классификатор + blacklist
├── llama_api_module.py            # LLaMA/Ollama API + merge (source of truth)
├── reclamation_bitrix_connector.py # Bitrix интеграция (connector)
├── bitrix24_integration.py        # Bitrix24 REST API wrapper
├── ocr_processor.py               # OCR для сканов (Tesseract + OpenCV)
├── improved_llama_prompts.py      # Промпты для LLaMA (legacy)
├── multi_reclamation_processor.py # Множественные рекламации (legacy)
├── zip_processor.py               # ZIP-вложения
├──
├── database.py                    # SQLite модуль (4 таблицы)
├── dashboard_api.py               # FastAPI backend (:8080)
├── dashboard.html                 # Frontend (Tailwind CSS + vanilla JS)
├──
├── classifier_config.json         # Blacklist + keywords (НЕ хардкод!)
├── bitrix_field_mapping.json      # Маппинг полей Bitrix (автообновляется)
├── .env                           # Секреты (IMAP, SMTP, Bitrix webhook)
├──
├── reclamations.db                # SQLite БД (история, статистика)
├── processed_reclamations.json    # Кэш обработанных email IDs
├── processing_results.json        # Результат последнего запуска (legacy)
├──
├── docs/                          # Документация
│   ├── current_stage.md           # Текущий статус, причинно-следственные связи
│   ├── architecture.md            # Этот файл
│   ├── setup.md                   # Установка и настройка
│   ├── bitrix24.md                # Конфигурация Bitrix24
│   ├── service.md                 # Windows сервис NSSM
│   ├── troubleshooting.md         # Решения проблем
│   ├── migration-history.md       # История миграций
│   └── rtx-blackwell-guide.md     # Legacy: vLLM гайд
├── service/                       # Windows сервис
│   ├── install.bat                # Установка NSSM сервиса
│   ├── menu.bat                   # Управление (start/stop/status/logs)
│   ├── start.bat / stop.bat       # Быстрые команды
│   ├── status.bat / logs.bat      # Мониторинг
│   ├── uninstall.bat              # Удаление сервиса
│   └── nssm.exe                   # NSSM бинарник
├── venv/                          # Python окружение
├── attachments/                   # Вложения писем (по папкам)
├── logs/                          # Логи сервиса
│   ├── service_stdout.log         # Stdout от NSSM
│   └── service_stderr.log         # Stderr от NSSM (основные логи)
└── archive/                       # Устаревшие файлы
```

---

## Core Pipeline (v2.0 — parallel)

```
EmailProcessor → ReclamationClassifier → LLaMAAnalyzer → Bitrix24Connector
     ↓                   ↓                    ↓                  ↓
   IMAP              Blacklist            OCR + AI           REST API
   fetch             check                analysis           upload
     ↓                   ↓                    ↓                  ↓
   SQLite            SQLite              SQLite              SQLite
   save_email        save_email          save_email          save_reclamation
```

Система работает в трёх режимах (`TEST_MODE`):
- **monitor**: Непрерывный опрос почты с интервалом 2 мин
- **email**: Обработка писем за конкретную дату
- **demo**: Тест на примерах, без подключения к почте

---

## Архитектура классификации (v2.0)

```
Email → Blacklist? → (да) → is_reclamation=False (быстрый выход, save_email)
                  → (нет) → LLaMA → is_reclamation (ФИНАЛЬНОЕ РЕШЕНИЕ)
                         → Classifier → category (только помощь)
```

**Принципы:**
1. **LLaMA решает** — классификатор НЕ определяет is_reclamation. Никаких keyword-override (B22)
2. **Blacklist** — быстрый фильтр бухгалтерии (счёт, оплата, invoice, + англо-blacklist)
3. **При ошибке LLaMA** -> is_reclamation=False (лучше пропустить)
4. **0 продуктов** -> is_reclamation=False (защита от false positives)
5. **Reasoning** сохраняется в SQLite

---

## Компоненты

### email_processor_improved.py (Main orchestrator)

Главный файл. Управляет всем pipeline.

**Режим monitor:**
```
continuous_monitoring() → while True:
  run(date_str) → find_emails_by_date() → for each email:
    db.start_run()
    connect() → fetch_email() → download_attachments()
    process_email_parallel():
      Phase 0: classify -> blacklist check
      Phase 1: download_attachments() + dedup по filename (B23)
      Phase 1b: parallel OCR (12 workers)
      Phase 2: parallel LLaMA per-document (4 workers, dynamic timeout B24)
      Phase 3: merge_document_analyses() → 20 compat-алиасов
      Phase 4: N products → N × deep_copy → bitrix_connector.process()
      Phase 5: db.save_email() + db.save_reclamation()
    _mark_email_processed() → save cache
    db.finish_run()
  sleep(120)
```

### reclamation_classifier.py (Category helper + Blacklist filter)
- **НЕ определяет is_reclamation** — это делает только LLaMA!
- Blacklist check: счета, оплата, бухгалтерия, англ. спам → быстрый выход
- Определяет категорию (Наземка/Метро/ЖДТ/Спецтехника) для помощи
- Keywords и companies загружаются из `classifier_config.json` (не хардкод)
- Извлечение текста из PDF, DOCX, XLSX, PPTX, TXT, CSV, EML
- Обработка ZIP-архивов рекурсивно

### classifier_config.json (Configuration — НЕ хардкод!)
- `blacklist_keywords`: слова для быстрого исключения (счёт, invoice, оплата, account deactivat, password reset...)
- `keywords_for_categories`: ключевые слова для категорий (ОПС, аэрозоль → Спецтехника; СТМ → ЖДТ)
- `company_to_category`: маппинг компаний на категории
- `subcategories`: подкатегории для Метро и Спецтехники

### llama_api_module.py (AI analysis — source of truth)
- Подключение к Ollama через **Native API** (`/api/chat`, port 11434)
- **ВАЖНО**: Native API, НЕ OpenAI-compatible (`/v1/...`)!
- Parallel per-document analysis (4 workers)
- `merge_document_analyses()` -- объединение результатов:
  - is_reclamation = ANY(doc.is_reclamation_related), БЕЗ keyword-override (B22)
  - Нормализованная дедупликация products (name.lower() + serial)
  - Cross-reference dedup (name == model/code)
  - 20 compat-алиасов для connector
- Динамический таймаут: `400 * (1 + total_docs / workers)` (B24)
- Qwen3-30B-A3B (MoE) с `num_ctx: 32768`
- JSON extraction с regex fallback

### ocr_processor.py (OCR для сканов)
```
PDF → PyMuPDF get_text()
      ↓ (если 0 символов — скан)
      OCR Level 1: get_pixmap(dpi=300) → Tesseract
      ↓ (если < 100 символов)
      OCR Level 2: get_images() → Tesseract (fallback)
      ↓ (если < 50 символов)
      OCR Level 3: + предобработка OpenCV
```

### reclamation_bitrix_connector.py (Connector layer)
- `sync_field_mapping()` при `__init__()` — автообновление полей из Bitrix API
- Преобразует данные рекламации в формат Bitrix24 (48 полей)
- **Загрузка файлов**: base64 upload в PROPERTY_1102 (до 30MB)
- **Retry**: парсинг exception, перегенерация ELEMENT_CODE при дублях
- **Дедупликация**: `processed_reclamations.json` (не через Bitrix API)
- `unique_key = email_id + product_name + serial_number`

### bitrix24_integration.py (CRM integration)
- REST API wrapper для Bitrix24
- **IBLOCK_TYPE_ID**: `bitrix_processes` (не `lists`!)
- **IBLOCK_ID**: 100 (список "Работа с Рекламациями")
- Field mapping через `bitrix_field_mapping.json`
- Error handling with retry logic

### database.py (SQLite)
- Thread-safe (WAL mode, `threading.local()` для connections)
- 4 таблицы: `emails`, `reclamations`, `processing_runs`, `tasks`
- Автосоздание таблиц при `import database`
- Файл: `reclamations.db`

### dashboard_api.py (FastAPI backend)
- REST API + WebSocket live логов
- Автодекодирование cp1251 → UTF-8 для лог-файлов
- Определение статуса NSSM (UTF-16LE декодирование)
- Порт: 8080 (`DASHBOARD_PORT` env)

### dashboard.html (Frontend)
- Tailwind CSS + vanilla JS (один файл, без сборки)
- Статус-бейджи: Сервис, Ollama, WebSocket
- Запуск обработки за конкретную дату
- Вкладки: Рекламации (с фильтрами), Live логи, История запусков
- Автообновление: статус 30s, статистика 60s, задачи 10s

---

## API Request Format

**ВАЖНО**: Используется Native Ollama API, НЕ OpenAI-compatible!

```python
# Native Ollama API format
{
    "model": "qwen3:30b-a3b",
    "messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."}
    ],
    "stream": False,
    "options": {
        "num_ctx": 32768,    # Контекст 32K токенов
        "temperature": 0.1
    }
}
```

**Response handling (Native API):**
```python
# Native API возвращает другой формат!
message = response['message']  # НЕ response['choices'][0]['message']
ai_response = message.get('content', '{}')
```

---

## Data Flow

```
1. IMAP (imap.yandex.ru) → fetch emails by date
2. Download attachments → attachments/<subject>/
3. Classify: blacklist check (classifier_config.json)
4. Parallel OCR (12 workers): PDF/images → text
5. Parallel LLaMA (4 workers): per-document analysis
6. Merge: deduplicate products, create compat-aliases
7. Bitrix24: create records (lists.element.add)
8. SQLite: save email + reclamation + run stats
9. SMTP (smtp.yandex.ru:465): forward if needed
```

## Таймауты

| Операция | Таймаут |
|----------|---------|
| IMAP connect/select/search/fetch | 60s |
| LLaMA per-document analysis | Динамический: `400 * (1 + docs/workers)` (B24) |
| -- 1 документ | 500s |
| -- 3 документа | 700s |
| -- 8 документов | 1200s |
| LLaMA extract_details (legacy) | 600s |
| SMTP SSL | 30s |
| Bitrix24 API | 30s |
| Dashboard API status check | 3s (Ollama), 5s (NSSM) |

## Output Files

| Файл | Назначение |
|------|------------|
| `reclamations.db` | SQLite — полная история (emails, reclamations, runs, tasks) |
| `processing_results.json` | Результат последнего запуска (legacy, для совместимости) |
| `processed_reclamations.json` | Кэш обработанных email IDs (быстрая дедупликация) |
| `bitrix_field_mapping.json` | Маппинг полей Bitrix (автообновляется) |
| `logs/service_stderr.log` | Основные логи сервиса (ротация 10MB) |
| `logs/service_stdout.log` | Stdout сервиса |

## Расширение системы

**Добавить новую категорию:**
1. Обновить `classifier_config.json` (keywords, companies)
2. Добавить маппинг в `reclamation_bitrix_connector.py` → `map_category()`
3. Обновить Bitrix PROPERTY_1012 если нужно

**Изменить AI промпты:**
Редактировать в `llama_api_module.py`:
- `SINGLE_DOCUMENT_PROMPT` — промпт для анализа одного документа
- `SYSTEM_PROMPT` — системный промпт с правилами

**Новые форматы файлов:**
Добавить handler в `AttachmentProcessor._extract_text_from_file()` (reclamation_classifier.py)

## AI Model Configuration

### Qwen3-30B-A3B (текущая модель)
- Architecture: MoE (Mixture of Experts) — 30B total, 3B active
- VRAM: ~16-18 GB
- API: **Native Ollama API** (`/api/chat`, порт 11434)
- Response: использует только `content` (не reasoning_content)

### Управление Ollama
```bash
ollama list              # Проверить модели
ollama pull qwen3:30b-a3b  # Скачать модель
curl http://localhost:11434/api/tags  # API проверка
ollama rm model_name     # Удалить модель
```
