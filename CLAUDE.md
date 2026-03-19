# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Документация (индекс)

Вся детальная документация в `docs/`:

| Файл | Содержание |
|------|------------|
| [docs/setup.md](docs/setup.md) | Первый запуск, установка Ollama, venv, .env, Pre-Launch Checklist |
| [docs/architecture.md](docs/architecture.md) | Компоненты, pipeline, data flow, API format, таймауты, расширение системы |
| [docs/bitrix24.md](docs/bitrix24.md) | Конфигурация Bitrix24, поля, маппинг, правила |
| [docs/service.md](docs/service.md) | Windows сервис NSSM, команды запуска |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Все известные проблемы и решения |
| [docs/migration-history.md](docs/migration-history.md) | История всех миграций и исправлений |
| [docs/rtx-blackwell-guide.md](docs/rtx-blackwell-guide.md) | Legacy: гайд по RTX 5080/5090 + vLLM |

---

## ФИЛОСОФИЯ РАЗРАБОТКИ (ОБЯЗАТЕЛЬНО К ПРОЧТЕНИЮ)

### Принцип #0: СПРАШИВАЙ ПЕРЕД УДАЛЕНИЕМ — ВСЕГДА!

**ЛЮБОЕ удаление требует явного подтверждения пользователя:**
- Удаление записей из Bitrix → СПРОСИ
- Удаление полей из Bitrix → СПРОСИ
- Удаление файлов → СПРОСИ
- Удаление кода → СПРОСИ

Если пользователь говорит "проверь запись X" — это НЕ значит "удали".
Сначала ИЗУЧИ, потом ПОКАЖИ результат, потом СПРОСИ что делать.

### Принцип #0.1: НЕ УДАЛЯЙ ПОЛЯ BITRIX — НИКОГДА!

**ЗАПРЕЩЕНО** удалять поля из Bitrix списка 100!
- Над проектом работает команда, не только ИИ
- Поля могут использоваться другими людьми/процессами
- Удаление поля = потеря данных без возможности восстановления

**РАЗРЕШЕНО:** Создавать НОВЫЕ поля, обновлять маппинг.
**ЗАПРЕЩЕНО:** Удалять, переименовывать, менять типы полей.

### Принцип #0.2: АРХИТЕКТУРА КЛАССИФИКАЦИИ (v2.0)

```
Email → Blacklist? → (да) → is_reclamation=False (быстрый выход)
                  → (нет) → LLaMA → is_reclamation (ФИНАЛЬНОЕ РЕШЕНИЕ)
                         → Classifier → category (только помощь)
```

**Ключевые файлы:**
- `classifier_config.json` — blacklist + keywords (НЕ хардкод!)
- `reclamation_classifier.py` — `is_blacklisted()`, `is_reclamation=None` всегда
- `email_processor_improved.py` — blacklist check, LLaMA decision
- `llama_api_module.py` — merge без override

**Принципы:**
1. **LLaMA решает** — классификатор НЕ определяет is_reclamation
2. **Blacklist** — быстрый фильтр бухгалтерии (счёт, оплата, invoice)
3. **При ошибке LLaMA** → is_reclamation=False (лучше пропустить)
4. **Reasoning** сохраняется в processing_results.json

### Принцип #1: СНАЧАЛА ТЕСТИРУЙ — ПОТОМ ЗАПУСКАЙ

**НИКОГДА** не запускай сервис, не меняй продакшен-данные, не делай API-вызовы к Bitrix
пока не выполнил полную симуляцию и проверку:

1. **Синтаксис** — все файлы импортируются без ошибок
2. **Маппинг** — все используемые поля существуют в bitrix_field_mapping.json
3. **Симуляция** — прогнать код на тестовых данных (разные сценарии)
4. **Edge cases** — проверить граничные случаи

```bash
# Перед ЛЮБЫМ изменением в connector/processor:
python test_fields_simulation.py
```

### Принцип #2: НЕ ДОВЕРЯЙ — ПРОВЕРЯЙ

После каждого изменения:
- Проверь логи на ошибки
- Проверь созданные записи в Bitrix вручную
- Убедись что данные корректны

### Принцип #3: ДОКУМЕНТИРУЙ ПРИЧИННО-СЛЕДСТВЕННЫЕ СВЯЗИ

При исправлении багов документируй:
```
Симптом → Причина → Решение → Файлы → Тесты
```

---

## Project Overview

Email-based reclamation (complaint/claim) management system. Automatically identifies, classifies, and processes customer complaints from email correspondence using AI (Ollama + LLaMA), then creates records in Bitrix24 and routes them to the right team.

## Project Structure

```
Рекламации/
├── email_processor_improved.py    # Главный процессор
├── reclamation_classifier.py      # Классификатор + blacklist
├── llama_api_module.py            # LLaMA/Ollama API (source of truth)
├── reclamation_bitrix_connector.py # Bitrix интеграция
├── improved_llama_prompts.py      # Промпты для LLaMA
├── ocr_processor.py               # OCR для сканов
├── bitrix24_integration.py        # Bitrix24 API wrapper
├── multi_reclamation_processor.py # Множественные рекламации
├── zip_processor.py               # ZIP-вложения
├── classifier_config.json         # Blacklist + keywords
├── bitrix_field_mapping.json      # Маппинг полей Bitrix (48 полей)
├── docs/                          # Документация (см. индекс выше)
├── service/                       # Windows сервис (NSSM)
├── venv/                          # Python окружение
├── attachments/                   # Вложения писем
└── logs/                          # Логи сервиса
```

## Quick Start

```bash
# Установка (подробнее: docs/setup.md)
setup.bat

# Запуск
TEST_MODE=monitor python email_processor_improved.py

# Сервис (подробнее: docs/service.md)
cd service && menu.bat
```

## Core Pipeline

```
EmailProcessor → ReclamationClassifier → LLaMAAnalyzer → Bitrix24Integration
```

Три режима (`TEST_MODE`): `monitor` | `email` | `demo`

Подробнее: [docs/architecture.md](docs/architecture.md)

## Bitrix24

- **IBLOCK_TYPE_ID**: `bitrix_processes` (не `lists`!)
- **IBLOCK_ID**: задаётся в `bitrix24_integration.py` (ваш ID списка)
- **Webhook**: `.env` → `BITRIX24_WEBHOOK`

Подробнее: [docs/bitrix24.md](docs/bitrix24.md)

## AI Model (Ollama + Qwen3-30B-A3B)

- **API**: Native Ollama (`/api/chat`, port 11434) — НЕ OpenAI-compatible!
- **Модель**: `qwen3:30b-a3b` (MoE: 30B total, 3B active)
- **Контекст**: `num_ctx: 32768`

Подробнее: [docs/architecture.md](docs/architecture.md#api-request-format)
