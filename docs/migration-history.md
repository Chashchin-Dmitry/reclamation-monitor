# Migration History

## 2026-03-10: B25-B27 -- 1 email = 1 Bitrix, company_to_category, убрана обрезка [:500]

### B25: 1 email = 1 элемент Bitrix (HIGH)
- **Симптом**: N продуктов -> N элементов Bitrix. Дублирование данных, каждый продукт = отдельная запись
- **Причина**: Цикл `for product in products: copy.deepcopy(result) + process_reclamation()`
- **Решение**: Конкатенация product-полей в merge (`_concat_product_field`, `_concat_issues`).
  1 продукт -> значение как есть. N продуктов -> "1) X  2) Y  3) Z"
  Убран deep copy loop, убран `import copy`, `unique_key = str(email_id)`
- **Файлы**: `llama_api_module.py` (helpers + merge), `email_processor_improved.py` (phase 4+5), `reclamation_bitrix_connector.py` (unique_key)
- **DB**: Продукты по-прежнему сохраняются отдельными строками в `reclamations`, но все с одним `bitrix_id`

### B26: Расширен company_to_category (MEDIUM)
- **Симптом**: 22 компании из ~40 в маппинге. Часть писем без категории
- **Решение**: Добавлены 20 компаний:
  - Наземка: МАЗ, Мосгортранс, АЗ НАЗ, ПАЗ, ПК ТС, СЭтранс, Электротранссервис, Русские автобусы, УХК БКМ
  - Метро: МВМ, МВМС, ЦТОВ, Промтехсервис, СПЕЦАВТОМАТИКА, ЭЛСИЭЛ, МПС, КСК СП, КРОСНА-ЭЛЕКТРА, Гамем
  - keywords_for_categories обновлены соответственно
- **Файл**: `classifier_config.json` (42 компании вместо 22)

### B27: Убрана обрезка полей [:500] (MEDIUM)
- **Симптом**: Длинные описания обрезались до 500 символов
- **Причина**: `str(val)[:500]` в process_reclamation()
- **Факт**: Тест Bitrix API подтвердил: PROPERTY_1022 принимает 765+ символов без потерь
- **Решение**: Убрана `unlimited_fields` и `[:500]`. Все поля пишутся как `str(val)` без обрезки
- **Файл**: `reclamation_bitrix_connector.py`

---

## 2026-03-04: B22-B24 -- удаление keyword override, дедуп вложений, динамические таймауты

### B22: Удалён override по doc_type ключевым словам (HIGH)
- **Симптом**: Письмо 41589 "Об извещении об изменении руководства по эксплуатации" помечено как рекламация
- **Факт**: LLaMA правильно определил все 3 документа как НЕ рекламация (0/3, conf=0.95)
- **Причина**: `merge_document_analyses()` содержал блок override -- если doc_type содержит
  "уведомление", "претензия", "несоответствие" и т.д., перезаписывал is_reclamation на True
- **Корень**: Слово "уведомление" слишком широкое. "Уведомление об изменении руководства" != жалоба
- **Противоречие**: Нарушал Принцип #0.2 (LLaMA -- единственный источник решения)
- **Решение**: Удалён весь блок override. Промпт уже содержит правила (строка 145: "Извещение об изменении конструкции -> НЕ рекламация")
- **Файл**: `llama_api_module.py` (merge_document_analyses)
- **ВАЖНО**: Этот override был добавлен в 2026-02-26 (см. ниже) для борьбы с false negatives.
  Теперь он больше не нужен -- промпт достаточно точный после рефакторинга B17

### B23: Дедупликация вложений по имени файла (MEDIUM)
- **Симптом**: Письмо с 5 вложениями содержит 2 пары дублей (MIME multipart дублирует файлы)
- **Причина**: `download_attachments()` собирает ВСЕ MIME-части без проверки по имени
- **Следствие**: LLaMA обрабатывает N*2 документов, тратит x2 времени и GPU
- **Решение**: После сбора -- дедуп dict `{filename: attachment}`, последний перезаписывает
- **Файл**: `email_processor_improved.py` (download_attachments)

### B24: Динамический таймаут LLaMA (HIGH)
- **Симптом**: "претензия в ТЕХНО.pdf" (email 41600, 8 доков) -- TIMEOUT после 480 сек
- **Причина**: Фиксированный таймаут 480s не учитывает GPU-контенцию.
  4 worker'а делят GPU -> каждый запрос замедляется в 3-4 раза.
  1 документ = ~100-120 сек, 4 параллельных = ~350-450 сек
- **Решение**: `timeout = 400 * (1 + total_docs / workers)`
  - 1 док = 500s, 8 доков = 1200s (20 мин)
- **Файлы**: `llama_api_module.py` (analyze_single_document + analyze_documents_parallel)

---

## 2026-02-26: Исправление ложных отрицаний LLaMA + дедупликация продуктов

### Проблема 1: LLaMA пропускала рекламации с ключевыми словами в теме
- **Симптом**: Письмо ТМХ "Уведомление об обнаружении несоответствий товара" (Исх.6154-ТДТМХ) → `is_recl=False`
- **Симптом**: "Претензия 679" (26.02) — слово "претензия" в теме, но LLaMA первоначально пропускала подобные
- **Корневая причина 1**: Определение рекламации слишком формальное — "официальное заявление о несоответствии"
  - Модель требовала формальный документ с печатью, отвергала уведомления и претензии
- **Корневая причина 2**: Ключевые слова ("рекламация", "претензия", "несоответствие") были на 5-м месте в списке индикаторов (последний пункт) — модель воспринимала как наименее важный критерий
- **Корневая причина 3**: System prompt пустой — "Ты - анализатор писем" без конкретных правил
- **Корневая причина 4**: merge_document_analyses игнорировал doc_type — даже если doc_type="уведомление о несоответствии", is_reclamation_related=false → финальный False
- **Решение (промпт)**: `improved_llama_prompts.py`
  - Определение расширено: "ЛЮБОЕ обращение клиента о проблемах, дефектах, неисправностях"
  - КРИТИЧЕСКИЕ ПРАВИЛА — ключевые слова = правило #1 с приоритетом 100%
  - Принцип: "лучше false positive, чем false negative"
- **Решение (system prompt)**: `llama_api_module.py:172`
  - Жёсткие правила в system message: если ключевые слова → РЕКЛАМАЦИЯ 100%
- **Решение (per-doc prompt)**: `llama_api_module.py:82-134`
  - Добавлены типы документов: УВЕДОМЛЕНИЕ О НЕСООТВЕТСТВИИ, АКТ ОБСЛЕДОВАНИЯ, ПРЕТЕНЗИЯ
  - Правило: если doc_type содержит ключевые слова → is_reclamation_related = true
- **Решение (merge override)**: `llama_api_module.py:608-615`
  - Проверка doc_type на ключевые слова рекламации
  - Если doc_type содержит "рекламация", "претензия", "несоответствие" и т.д. -> override is_reclamation=True
  - **УДАЛЕНО в B22 (2026-03-04)**: Override вызывал ложные срабатывания. Промпт теперь достаточно точный
- **Результат**: Перезапуск за 25-е нашёл 5 рекламаций вместо 1 (было False → стало True)

### Проблема 2: Дедупликация пропускала продукты с одинаковым названием
- **Симптом**: Email 41424 содержал 4 продукта "Минор", но только 1 попал в Bitrix
- **Причина**: `unique_key = email_id + product_name` — все 4 совпадали
- **Решение**: unique_key теперь включает serial_number; при отсутствии — счётчик
- **Файл**: `reclamation_bitrix_connector.py:62-72`

### Проблема 3: LLaMA возвращала строку вместо dict в products
- **Симптом**: Crash на email 41436: `'str' object has no attribute 'get'`
- **Причина**: LLaMA иногда возвращает product name как строку, не объект
- **Решение**: isinstance проверка + конверсия str→dict в merge_document_analyses
- **Файл**: `llama_api_module.py:614-619`

### Причинно-следственная цепочка
```
23.02 email_processor завис (нет IMAP таймаута)
     ↓
24-25.02 рекламации не обработаны (2 дня)
     ↓
25.02 Исправлены таймауты, перезапуск за 24-25.02
     ↓
24.02: 4 рекламации найдены, 1 из 4 продуктов потеряна (баг дедупликации)
25.02: 1 рекламация найдена, ТМХ ПРОПУЩЕНА (LLaMA false negative)
     ↓
26.02 Анализ: промпт слишком формальный, ключевые слова не приоритетные
     ↓
26.02 Исправлен промпт + merge override + дедупликация
     ↓
Перезапуск 25.02: 5 рекламаций (вместо 1), ТМХ теперь определяется
```

### Изменённые файлы
- `improved_llama_prompts.py` — определение, критические правила, индикаторы
- `llama_api_module.py` — system prompt, per-doc prompt, merge override, str→dict fix
- `reclamation_bitrix_connector.py` — unique_key с serial_number

---

## 2026-02-25: Таймауты + .eml поддержка + исправление disconnect()

- **Проблема**: 23 февраля email_processor завис на IMAP-операции (нет таймаута на сокете)
  - Процесс формально "Running", но мёртв 2 дня → рекламации за 24-25.02 не обработаны
- **Исправление 1**: `disconnect()` — убран `close()` (падал если INBOX не выбран), добавлен `finally: self.imap = None`
  - **Файл**: `email_processor_improved.py:355`
- **Исправление 2**: IMAP таймаут 60 сек (`IMAP4_SSL(host, timeout=60)`)
  - **Файл**: `email_processor_improved.py:347`
- **Исправление 3**: LLaMA analyze_email таймаут 300 сек (было `timeout=None`)
  - Добавлен отдельный `except requests.exceptions.Timeout`
  - **Файл**: `llama_api_module.py:192`
- **Исправление 4**: SMTP fallback таймаут 30 сек (было None)
  - **Файл**: `email_processor_improved.py:1461`
- **Новая фича**: Поддержка .eml вложений
  - Парсинг через stdlib `email` module
  - Извлечение заголовков (Subject, From, To, Date) и тела (text/plain + html fallback)
  - Рекурсивная обработка вложенных message/rfc822
  - **Файл**: `reclamation_classifier.py` — метод `extract_text_from_eml()`
- **Улучшение**: ZipProcessor теперь делегирует AttachmentProcessor для PDF, DOCX, EML и т.д.
  - Раньше внутри ZIP обрабатывались только текстовые файлы
  - **Файл**: `zip_processor.py` — `__init__(attachment_processor=None)`

---

## 2026-02-16: Исправление критических багов + уборка проекта

- **Баг 1**: Сырой текст email не сохранялся в Bitrix (PROPERTY_1092 пустое)
  - **Причина**: В `email_processor_improved.py:1032` поле `body` не добавлялось в `result`
  - **Решение**: Добавлено `"body": email_data.get('body', '')` в result
  - **Файл**: `email_processor_improved.py:1037`
- **Баг 2**: Номер рекламационного акта всегда n/a (PROPERTY_1094)
  - **Причина**: В `improved_llama_prompts.py` не было поля `act_number` в промпте
  - **Решение**: Добавлено `- act_number: номер рекламационного акта (если нет - "n/a")`
  - **Файл**: `improved_llama_prompts.py:75`
- **Баг 3**: Даты в разных форматах
  - **Решение**: Функция `parse_date()` теперь возвращает единый формат YYYY-MM-DD
  - **Файл**: `reclamation_bitrix_connector.py:308-330`
- **Уборка проекта**:
  - Удалено 200+ временных файлов `tmpclaude-*`
  - Очищен лог 293MB (сохранены последние 10000 строк в archive)
  - vLLM папки перемещены в `archive/vllm-legacy/` (перешли на Ollama)
  - Старые доки перемещены в `archive/docs-old/`
  - Созданы README.md в archive папках

---

## 2026-02-13: Рефакторинг классификатора — LLaMA = source of truth

- **Проблема**: Письмо "Счёт на оплату" от Камаза попало как рекламация (ID 20558)
  - **Причина**: Хардкод `company_to_category['Камаз']` давал +3 очка → score >= 100 → override LLaMA
  - **Корень**: Классификатор всегда возвращал `is_reclamation=True`, нет blacklist
- **Решение**: Новая архитектура v2.0
  ```
  Email → Blacklist? → (да) → is_reclamation=False (быстрый выход)
                    → (нет) → LLaMA → is_reclamation (ФИНАЛЬНОЕ)
                           → Classifier → category (помощь)
  ```
- **Изменённые файлы**:
  - `classifier_config.json` (НОВЫЙ) — blacklist + keywords вынесены из кода
  - `reclamation_classifier.py` — добавлен `is_blacklisted()`, `is_reclamation=None` всегда
  - `email_processor_improved.py` — убран override (2 места), добавлен blacklist check
  - `llama_api_module.py` — убран override в `merge_document_analyses()`
- **Ключевые принципы**:
  - LLaMA = единственный источник правды для is_reclamation
  - Классификатор только помогает с категорией
  - При ошибке LLaMA → is_reclamation=False (лучше пропустить)
  - Reasoning сохраняется в processing_results.json

---

## 2026-02-11: Улучшение OCR и санитизации имён файлов

- **Проблема 1**: Email 41240 (СТМ Калугапутьмаш) не распознан как рекламация
  - **Причина**: OCR использовал `get_images()` — извлёк только 44 символа из PDF-скана
  - **Решение**: Теперь сначала `get_pixmap(dpi=300)` (рендер всей страницы), затем fallback на `get_images()`
  - **Файл**: `ocr_processor.py` функция `_ocr_all_pages()`
  - **Результат**: OCR теперь извлекает 1283 символа вместо 44
- **Проблема 2**: Точки в именах файлов (напр. `ИП-471-1558 от 06.02.2026 г..pdf`)
  - **Причина**: Двойные точки и точки в середине имени могут ломать пути
  - **Решение**:
    - Точки внутри имени → заменяются на `_`
    - Только расширение сохраняет точку (`.pdf`, `.xlsx`)
    - Длина имени ≤ 100 символов + MD5 хэш
    - try-except с fallback на `error_decode_{n}.ext`
  - **Файл**: `email_processor_improved.py` строки 592-633

---

## 2026-02-09: Исправление ошибок обработки вложений

- **Проблема 1**: Письма с длинными темами не обрабатывались (ТМХ, 41183)
  - **Причина**: Имя папки > 150 символов → превышение Windows MAX_PATH (260)
  - **Решение**: Обрезка имени папки до 100 символов + MD5 хэш для уникальности
  - **Файл**: `email_processor_improved.py` строки 524-545
  - **Добавлено**: try-except с fallback на `email_{id}`
- **Проблема 2**: Excel .xls файлы не читались
  - **Причина**: pandas требует явный `engine='xlrd'` для старого формата
  - **Решение**: Определение engine по расширению (.xls → xlrd, .xlsx → openpyxl)
  - **Файл**: `reclamation_classifier.py` строки 696-706
- **Зависимости venv**: xlrd 2.0.2, openpyxl 3.1.5

---

## 2026-01-30: Миграция с vLLM на Ollama + Qwen3-30B-A3B

- **Причина**: vLLM не работает на RTX 5080 (Blackwell sm_120) без сложной сборки
  - Попытка 1: Свой Dockerfile на NGC PyTorch 25.02 → Float8_e8m0fnu ошибка
  - Попытка 2: BoltzmannEntropy/vLLM-5090 → та же ошибка
  - Попытка 3: docker/Blackwell → прервано из-за медленного интернета
- **Решение**: Ollama + Qwen3-30B-A3B
  - Работает из коробки на любой GPU
  - OpenAI-совместимый API (минимум изменений в коде)
  - MoE модель: 30B параметров, 3B активных
- **Изменённые файлы**:
  - `llama_api_module.py`: URL (11434), модель (qwen3:30b-a3b), убран reasoning_content
  - `.env`: LLAMA_API_URL, LLAMA_MODEL
  - `CLAUDE.md`: обновлена документация
  - `current_stage.md`: план миграции

---

## 2025-12-29: Исправление критической ошибки с timedelta

- **Проблема**: Система не обрабатывала письма после 15 декабря
- **Причина**: В `email_processor_improved.py` отсутствовал импорт `timedelta`
- **Ошибка**: `NameError: name 'timedelta' is not defined` на строке 289
- **Исправление**: Добавлен импорт `from datetime import datetime, timedelta`
- **Новый скрипт**: `process_date_range.py` для обработки диапазона дат

---

## 2025-12-15: Исправление дублирования рекламаций в Bitrix24

- **Проблема**: При повторном запуске или обработке писем с несколькими продуктами создавались дубли
- **Корневые причины**:
  - Файл кэша `processed_reclamations.json` не создавался
  - Двойная отправка: `process_email()` и `process_multiple_reclamations()` оба отправляли в Bitrix
  - Разные форматы ключей кэша: `email_id` vs `email_id_product`
  - Отсутствие проверки существующих записей в Bitrix
- **Исправления**:
  - Добавлен параметр `skip_bitrix` в `process_email()` для предотвращения двойной отправки
  - `reclamation_bitrix_connector.py`: исправлен путь к файлу кэша (абсолютный)
  - Добавлена проверка дублей в Bitrix по теме письма (`find_reclamation_by_subject()`)
  - Генерация `unique_key = email_id + product_name` для множественных рекламаций
  - `_is_email_processed()` теперь проверяет prefix для совместимости ключей
  - Создан начальный файл кэша `processed_reclamations.json`

---

## 2025-11-28: Миграция Bitrix24 списка в бизнес-процессы

- Список перенесён из универсальных списков (`lists`) в бизнес-процессы (`bitrix_processes`)
- Обновлены файлы: `bitrix24_integration.py`, `reclamation_bitrix_connector.py`, `test_connectivity_bitrix.py`
- IBLOCK_ID остался 100, IBLOCK_TYPE_ID изменён на `bitrix_processes`
- Добавлено право `bizproc` в webhook

---

## 2025-11-25: Миграция с t-pro (GGUF) на GPT-OSS-20B (vLLM 0.10.2)

- Cleaned up 25 old containers, freed ~49 GB
- Updated `llama_api_module.py` for OpenAI format and reasoning support
- Configured persistent model storage
- All tests passing with new model
