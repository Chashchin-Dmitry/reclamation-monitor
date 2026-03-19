# Установка и первый запуск

## Автоматическая настройка (рекомендуется)
```bash
setup.bat
```

## Ручная настройка

### 1. Создать .env файл
```bash
copy .env.example .env
# Заполнить реальными данными (см. .env.example для всех переменных)
```

### 2. Установить Ollama и скачать модель
```bash
# Установка Ollama (Windows)
winget install Ollama.Ollama
# Или скачать: https://ollama.com/download

# Скачать модель Qwen3-30B-A3B (~18 GB)
ollama pull qwen3:30b-a3b

# Проверить установку
ollama list
curl http://localhost:11434/v1/models
```
> **Почему Ollama**: vLLM не работает на RTX 5080/5090 (Blackwell) без сложной сборки.
> Ollama работает из коробки на любой GPU.

### 3. Проверить что Ollama запущена
```bash
curl http://localhost:11434/v1/models
# Должен вернуть JSON с моделью qwen3:30b-a3b
```

### 4. Установить Python зависимости
```bash
# Рекомендуется в venv
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

pip install -r requirements.txt
```
> **КРИТИЧНО**: PyMuPDF (fitz) обязателен для извлечения текста из PDF!
> Без него система НЕ СМОЖЕТ анализировать PDF-вложения.

### 5. Запустить систему
```bash
python email_processor_improved.py
```

---

## Pre-Launch Checklist

Перед запуском системы выполнить проверки:

### 1. Проверка LLM (Ollama)
```bash
curl http://localhost:11434/v1/models
# Должен вернуть JSON с моделью qwen3:30b-a3b
```
Если не работает:
```bash
ollama list
ollama pull qwen3:30b-a3b
```

### 2. Проверка подключения к почте (IMAP)
```bash
py -3 -c "
import imaplib, os
from dotenv import load_dotenv
load_dotenv()
mail = imaplib.IMAP4_SSL(os.getenv('EMAIL_HOST'))
mail.login(os.getenv('EMAIL_USER'), os.getenv('IMAP_PASSWORD'))
print('OK!')
mail.logout()
"
```

### 3. Проверка подключения к Bitrix24
```bash
py -3 test_connectivity_bitrix.py
# Должен найти список ID=100 в bitrix_processes
# И создать тестовую запись
```

### 4. Запуск системы
```bash
# Обработка текущей даты
py -3 email_processor_improved.py

# Или конкретная дата
TEST_MODE=email TEST_DATE=28-Nov-2025 py -3 email_processor_improved.py

# Режим мониторинга (непрерывный)
TEST_MODE=monitor py -3 email_processor_improved.py
```

---

## Переменные окружения (.env)

| Переменная | Описание | Пример |
|------------|----------|--------|
| `TEST_MODE` | Режим работы | `monitor` / `email` / `demo` |
| `MONITOR_INTERVAL` | Интервал проверки (мин) | `5` |
| `USE_CURRENT_DATE` | Текущая дата | `true` / `false` |
| `TEST_DATE` | Конкретная дата | `25-Nov-2025` |
| `LLAMA_API_URL` | Ollama endpoint | `http://localhost:11434/api/chat` |
| `LLAMA_MODEL` | Модель | `qwen3:30b-a3b` |
| `EMAIL_USER` | Email аккаунт | `your_email@company.ru` |
| `IMAP_PASSWORD` | Пароль IMAP | — |
| `SMTP_PASSWORD` | Пароль SMTP | — |
| `BITRIX24_WEBHOOK` | Webhook URL | — |
