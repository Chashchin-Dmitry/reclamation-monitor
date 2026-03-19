# Bitrix24 Configuration

## Основные параметры

| Параметр | Значение |
|----------|----------|
| URL списка | https://your_company.bitrix24.ru/bizproc/processes/IBLOCK_ID/view/0/ |
| IBLOCK_TYPE_ID | `bitrix_processes` |
| IBLOCK_ID | `100` |
| Webhook | `.env` → `BITRIX24_WEBHOOK` |

## Необходимые права webhook
- `lists` — работа со списками
- `bizproc` — работа с бизнес-процессами

## Ключевые поля

| Поле | PROPERTY_ID | Источник |
|------|-------------|----------|
| Сырой текст email | PROPERTY_1092 | `result['body']` |
| Номер рекламационного акта | PROPERTY_1094 | `llama_analysis['act_number']` |
| Дата получения | PROPERTY_1008 | `parse_date()` → YYYY-MM-DD |

## Файлы конфигурации

- **bitrix_field_mapping.json** — маппинг полей (48 полей)
- **bitrix24_integration.py** — REST API wrapper
- **reclamation_bitrix_connector.py** — connector layer

## ВАЖНЫЕ ПРАВИЛА (из философии разработки)

**ЗАПРЕЩЕНО:**
- Удалять существующие поля из списка 100
- Переименовывать поля
- Менять типы полей

**РАЗРЕШЕНО:**
- Создавать НОВЫЕ поля
- Обновлять маппинг в `bitrix_field_mapping.json`

## Проверка подключения
```bash
python test_connectivity_bitrix.py
# Должен найти список ID=100 в bitrix_processes
```
