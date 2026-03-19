# Troubleshooting

## Ollama не отвечает
```bash
# Проверить статус
curl http://localhost:11434/api/tags

# Проверить модели
ollama list

# Перезапустить Ollama (Windows Service)
# Или запустить вручную: ollama serve
```

## Сервис EpotosReclamation не работает
```bash
cd ./service
status.bat      # Диагностика
logs.bat        # Посмотреть ошибки
restart.bat     # Перезапустить
```

## Model download slow
- Ollama: модель (~18GB) кэшируется в ~/.ollama/models
- Повторные запуски используют кэш

## Classification issues
- Check `CATEGORY_METADATA` keywords
- Review logs in `reclamation_classifier.log`

## LLaMA timeout при большом количестве документов
- **Симптом**: В логах `[ANALYZE_DOC] TIMEOUT для filename.pdf`
- **Причина**: GPU-контенция при параллельной обработке. 4 worker'а делят GPU, каждый запрос замедляется в 3-4 раза
- **Решение (B24)**: Таймаут теперь динамический: `400 * (1 + total_docs / workers)`
  - 1 док = 500s, 3 дока = 700s, 8 доков = 1200s
- **Диагностика**: В логах `[ANALYZE_DOC] timeout=XXXs (docs=N, workers=M)`

## Дубликаты вложений в письме (MIME multipart)
- **Симптом**: Письмо обрабатывается дольше чем ожидается, в логах одинаковые имена файлов
- **Причина**: Почтовый клиент дублирует вложения в text/plain и text/html частях
- **Решение (B23)**: Дедупликация по имени файла -- если имя 1:1, оставляем последний
- **Диагностика**: В логах `Дубликат вложения 'X': заменяем предыдущий на последний`

## Ложная рекламация (LLaMA сказал НЕТ, но система пометила ДА)
- **Симптом**: Письмо помечено как рекламация, хотя в reasoning видно "0/N рекламационных"
- **Причина**: Был keyword-override по doc_type ("уведомление", "претензия" и др.)
- **Решение (B22)**: Override удалён. LLaMA -- единственный источник решения is_reclamation
- **Диагностика**: Проверить reasoning в дашборде (вкладка "Все письма" -> карточка)

## Дубли рекламаций в Bitrix
- Проверить наличие файла `processed_reclamations.json` в директории проекта
- Если файла нет, создать: `{"processed_ids": [], "processed_details": {}, "last_run": null}`
- Проверить логи на предмет сообщений "уже была отправлена" или "уже существует"
- При необходимости очистить кэш (удалить файл) для повторной обработки

## Bitrix integration failing
```bash
python test_connectivity_bitrix.py
```

## Вложения не сохраняются (Errno 2: No such file or directory)
- **Причина**: Слишком длинное имя папки из темы письма (>100 символов)
- **Решение**: Исправлено в версии 2026-02-09, имена обрезаются до 100 символов
- Проверить логи: `logs/service_stderr.log`

## Excel .xls не читается (Import xlrd failed)
- **Причина**: pandas требует явный engine для старого формата
- **Решение**: Исправлено в версии 2026-02-09
- Проверить: `venv\Scripts\pip.exe show xlrd` (должен быть >= 2.0.1)

## PDF-скан не распознаётся (OCR извлекает мало текста)
- **Причина**: Старый метод `get_images()` не работал для некоторых PDF
- **Решение**: Исправлено в версии 2026-02-11 — теперь используется `get_pixmap(dpi=300)`
- **Диагностика**: Проверить логи на "OCR извлёк X символов"
- Если OCR даёт мало текста:
  - Проверить качество скана (низкое разрешение?)
  - Проверить установку Tesseract: `C:\Program Files\Tesseract-OCR\tesseract.exe`
  - Проверить русский языковой пакет: `rus.traineddata`

## Точки в именах файлов ломают пути
- **Причина**: Двойные точки (напр. `файл 06.02.2026 г..pdf`) создавали проблемы
- **Решение**: Исправлено в версии 2026-02-11 — точки внутри имени заменяются на `_`

## Docker (Legacy — vLLM)

> **Примечание:** vLLM больше не используется (проблемы с RTX 5080 Blackwell).
> Оставлено для справки. Кэш модели GPT-OSS-20B: `~/.cache/huggingface` (26GB).

```bash
docker ps -a
docker images
docker stop vllm-gpt-oss-20b
docker start vllm-gpt-oss-20b
```

## Important Notes

- **Russian language processing**: All content is in Russian, Qwen3 handles Cyrillic correctly
- **Attachment storage**: ~2.1 GB, organized by email subject
- **Ollama model**: Qwen3-30B-A3B (MoE) uses only `content` field in responses
- **Model cache**: First run downloads model (~18GB), stored in ~/.ollama
- **Email credentials**: Stored in .env, never commit
- **Bitrix webhook**: OAuth-style webhook URL in config
