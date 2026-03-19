"""
Модуль для взаимодействия с LLM API (Ollama Native API)

v2.0 (2026-02-13): Параллельный анализ документов
- SINGLE_DOCUMENT_PROMPT: анализ каждого документа отдельно
- analyze_documents_parallel(): параллельные запросы через ThreadPoolExecutor
- merge_document_analyses(): интеллектуальное слияние результатов
"""
import os
import json
import logging
import requests
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Загружаем .env ПЕРЕД чтением переменных окружения
load_dotenv()

# Импортируем улучшенные промпты
from improved_llama_prompts import (
    format_reclamation_detection_prompt,
    format_details_extraction_prompt
)

# Настройка логирования
logger = logging.getLogger("LLaMAAnalyzer")

# Конфигурация для Ollama API (Native API для поддержки num_ctx)
# ВАЖНО: Используем native API /api/chat вместо OpenAI-совместимого /v1/chat/completions
# чтобы передавать num_ctx и избежать зависания на длинных промптах
LLAMA_API = {
    'url': os.getenv('LLAMA_API_URL', 'http://localhost:11434/api/chat'),
    'model': os.getenv('LLAMA_MODEL', 'qwen3:30b-a3b'),
    'temperature': 0.1,
    'num_ctx': 16384  # Уменьшаем контекст для параллельных запросов (было 32768)
}

# Количество параллельных LLaMA запросов
# RTX 5080: 16GB VRAM, модель ~10GB, свободно ~6GB, 4 запроса по 16K контекста
MAX_LLAMA_WORKERS = int(os.getenv('OLLAMA_NUM_PARALLEL', '4'))


@dataclass
class DocumentAnalysis:
    """Результат анализа одного документа"""
    filename: str
    doc_type: str = "unknown"
    is_reclamation_related: bool = False
    confidence: float = 0.0
    products: List[Dict[str, str]] = field(default_factory=list)
    dates: Dict[str, str] = field(default_factory=dict)
    organizations: Dict[str, str] = field(default_factory=dict)
    contacts: Dict[str, str] = field(default_factory=dict)
    document_numbers: Dict[str, str] = field(default_factory=dict)
    severity: str = "n/a"
    category: str = "Неизвестно"
    raw_findings: str = ""
    error: Optional[str] = None


# Промпт для анализа ОДНОГО документа (per-document analysis)
SINGLE_DOCUMENT_PROMPT = """Ты - специалист по анализу технической документации ЭПОТОС.

ДОКУМЕНТ: {filename}
ТИП ФАЙЛА: {content_type}

ПОЛНЫЙ ТЕКСТ ДОКУМЕНТА:
{full_text}

КОНТЕКСТ ПИСЬМА:
- Тема: {email_subject}
- Отправитель: {email_sender}
- Дата: {email_date}

ЗАДАЧА: Извлеки ВСЕ данные из этого документа.

Верни JSON:
{{
  "doc_type": "тип документа (акт расследования, рекламация, фото, письмо, счёт, накладная, УВЕДОМЛЕНИЕ О НЕСООТВЕТСТВИИ, АКТ ОБСЛЕДОВАНИЯ, ПРЕТЕНЗИЯ, etc)",
  "is_reclamation_related": true/false,
  "confidence": 0.0-1.0,

  "products": [
    {{
      "name": "название продукта (ИП-212, МПП Тунгус, ДИП-34А и т.д.)",
      "code": "артикул/код",
      "model": "модель/модификация",
      "serial_number": "серийный номер",
      "issue": "описание проблемы с ЭТИМ продуктом"
    }}
  ],

  "dates": {{
    "production": "дата производства",
    "installation": "дата установки",
    "issue_occurred": "дата возникновения проблемы",
    "document_date": "дата документа"
  }},

  "organizations": {{
    "customer": "заказчик/клиент (название организации)",
    "dealer": "дилер/поставщик",
    "manufacturer": "производитель"
  }},

  "contacts": {{
    "person": "контактное лицо",
    "phone": "телефон",
    "email": "email",
    "address": "адрес"
  }},

  "document_numbers": {{
    "invoice": "номер счёта",
    "contract": "номер договора",
    "act": "номер акта",
    "reclamation": "номер рекламации"
  }},

  "severity": "низкая/средняя/высокая/критическая",
  "category": "Наземка/Метро/Спецтехника/ЖДТ/Неизвестно",

  "raw_findings": "любые другие важные данные из документа"
}}

ОПРЕДЕЛЕНИЕ РЕКЛАМАЦИИ:
Рекламация - это НОВАЯ жалоба/претензия клиента на дефект, неисправность или
несоответствие продукции ЭПОТОС. Клиент сообщает о ПРОБЛЕМЕ с конкретным изделием.

ЭТО РЕКЛАМАЦИЯ (is_reclamation_related = true):
- Клиент сообщает о дефекте/неисправности продукции (ИП-212 не работает, МПП протекает)
- Акт рекламации, акт о браке, акт расследования дефекта
- Уведомление о несоответствии продукции ТУ/ГОСТ
- Претензия по качеству с описанием конкретной проблемы
- Письмо с фото/описанием неисправного оборудования ЭПОТОС

ЭТО НЕ РЕКЛАМАЦИЯ (is_reclamation_related = false):
- Счет, invoice, акт сверки - любые ФИНАНСОВЫЕ документы
  (даже если упоминают "гарантийный ремонт" или "по претензии №...")
- ОТВЕТ/обсуждение по существующей рекламации (не новая жалоба, а переписка)
- Извещение об изменении конструкции (внутренний документ)
- Уведомление о регистрации/приемке (подтверждение, не жалоба)
- Коммерческое предложение, согласование цен, договоры
- Автоматические уведомления систем (newsletter, auto-reply, spam)
- Техническое задание, конструкторская документация
- Запрос на изменение параметров продукции (не дефект, а доработка)
- Счет на согласование, предварительный счет, проформа

ВАЖНО:
1. Если данных нет - пиши "n/a"
2. Если несколько продуктов - добавь все в массив products
3. Сохраняй ТОЧНЫЕ значения из документа (номера, даты, названия)
4. Не выдумывай данные
5. Рекламация - это ВСЕГДА про ПРОБЛЕМУ с конкретным продуктом. Если нет описания дефекта/неисправности - скорее всего НЕ рекламация
6. При сомнении -> is_reclamation_related = false (лучше точность чем recall)
7. Один физический продукт = одна запись в products. Если продукт упоминается несколько раз - объедини в одну запись
8. Категория: определи по контексту (Наземка/Метро/Спецтехника/ЖДТ)
/no_think
"""


def _get_prompt_from_db(key: str, fallback: str) -> str:
    """Читает промпт из БД (таблица settings), fallback на хардкод."""
    try:
        import database as _db
        custom = _db.get_setting(key)
        if custom and custom.strip():
            return custom
    except Exception:
        pass
    return fallback


def _concat_product_field(products, field, default='n/a'):
    """Конкатенация поля из списка продуктов: 1 -> значение, N -> нумерованный список."""
    if not products:
        return default
    if len(products) == 1:
        val = products[0].get(field, default)
        return val if val and val != 'n/a' else default
    parts = []
    for i, p in enumerate(products, 1):
        val = (p.get(field, '') or '').strip()
        parts.append(f"{i}) {val if val and val != 'n/a' else '-'}")
    return '  '.join(parts)


def _concat_issues(products):
    """Конкатенация описаний проблем: 1 -> issue, N -> [product_name] issue."""
    if not products:
        return 'n/a'
    if len(products) == 1:
        return products[0].get('issue', 'n/a') or 'n/a'
    lines = []
    for p in products:
        name = p.get('name', '?')
        issue = (p.get('issue', '') or '').strip()
        if issue and issue != 'n/a':
            lines.append(f"[{name}] {issue}")
    return '\n'.join(lines) if lines else 'n/a'


class LLaMAAnalyzer:
    """Класс для анализа писем с помощью LLaMA API (Native Ollama API)"""

    def __init__(self):
        self.api_url = LLAMA_API['url']
        self.model = LLAMA_API['model']
        self.temperature = LLAMA_API['temperature']
        self.num_ctx = LLAMA_API['num_ctx']  # Контекст 32K для длинных промптов
    

    def analyze_email(self, email_data: Dict[str, Any], attachments: list) -> Dict[str, Any]:
        """
        Анализирует письмо с помощью LLaMA для определения, является ли оно рекламацией
        
        Args:
            email_data: Данные письма (тема, содержимое и т.д.)
            attachments: Информация о вложениях
            
        Returns:
            Результат анализа
        """
        max_retries = 5  # Максимальное количество попыток
        retry_delay = 3   # Задержка между попытками в секундах
        
        # Формируем промпт с данными из письма и вложений
        logger.info(f"Начинаем анализ письма с темой: {email_data.get('subject', 'без темы')}")
        prompt = format_reclamation_detection_prompt(email_data, attachments)
        
        # Подготавливаем запрос к Ollama Native API
        # ВАЖНО: используем /api/chat с options.num_ctx для увеличенного контекста
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Ты - специалист по определению рекламаций на продукцию ЭПОТОС.\nРекламация - это НОВАЯ жалоба клиента на дефект/неисправность продукции ЭПОТОС.\nНЕ рекламация: счета, финансовые документы, ответы на существующие рекламации, извещения об изменении, КД, ТЗ.\nПри сомнении - выбирай 'НЕ рекламация'. Лучше точность чем recall."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature
            }
        }
        
        try:
            logger.info(f"Отправка запроса в LLaMA API: {self.api_url}")
            logger.info(f"Параметры запроса: {json.dumps(payload, ensure_ascii=False)[:100]}...")
            
            # Отправляем запрос к LLaMA API
            try:
                start_time = time.time()
                response = requests.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=300  # 5 минут максимум
                )
                logger.info(f"Ответ получен. Статус: {response.status_code}")
            except requests.exceptions.Timeout:
                elapsed = time.time() - start_time
                logger.error(f"[ANALYZE_EMAIL] TIMEOUT after {elapsed:.1f}s")
                return {"is_reclamation": None, "error": f"Timeout after {elapsed:.1f}s"}
            except Exception as e:
                logger.error(f"Ошибка при отправке запроса: {e}")
                import traceback
                logger.error(f"Трассировка ошибки: {traceback.format_exc()}")
                # None а не False - это техническая ошибка!
                return {"is_reclamation": None, "error": f"API Request Failed: {str(e)}"}
            
            
            # Если запрос успешный, обрабатываем ответ
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    # Native Ollama API формат (не OpenAI!)
                    message = response_data.get('message', {})
                    done = response_data.get('done', False)
                    done_reason = response_data.get('done_reason', 'unknown')

                    # Проверяем done_reason - если "length", модель не закончила
                    if done_reason == 'length':
                        logger.warning(f"Модель не закончила ответ (done_reason=length). Контекста недостаточно!")

                    # Qwen3 использует только content
                    ai_response = message.get('content', '')

                    # ЛОГИРУЕМ ответ для отладки
                    logger.info(f"Сырой ответ LLaMA (первые 500 символов): {ai_response[:500] if ai_response else '(пустой)'}")
                    logger.info(f"done_reason: {done_reason}, done: {done}")

                    # Проверяем что content не пустой
                    if not ai_response or not ai_response.strip():
                        logger.error(f"LLaMA вернул пустой content! done_reason={done_reason}")
                        # Возвращаем None чтобы email_processor использовал классификатор
                        return {"is_reclamation": None, "error": "Empty response", "done_reason": done_reason}

                    # Пытаемся извлечь JSON из ответа
                    json_match = re.search(r'(\{.*\})', ai_response, re.DOTALL)
                    if json_match:
                        ai_response = json_match.group(1)
                    else:
                        logger.error(f"Не удалось найти JSON в ответе: {ai_response[:200]}")
                        return {"is_reclamation": None, "error": "No JSON in response", "raw_response": ai_response[:500]}

                    result = json.loads(ai_response)

                    # Проверяем наличие обязательного поля
                    if 'is_reclamation' not in result:
                        logger.warning("Отсутствует обязательное поле 'is_reclamation' в ответе")
                        result['is_reclamation'] = None  # None а не False - неизвестно!

                    logger.info(f"Успешный анализ письма: is_reclamation={result.get('is_reclamation')}")
                    return result
                except json.JSONDecodeError as e:
                    logger.error(f"Ошибка парсинга JSON из ответа ИИ: {e}")
                    logger.error(f"Ответ который не удалось распарсить: {ai_response[:500] if ai_response else '(пустой)'}")
                    # None а не False - это техническая ошибка, не бизнес-решение!
                    return {"is_reclamation": None, "error": "JSON Parse Error", "raw_response": ai_response[:500] if ai_response else ""}
            else:
                logger.error(f"Ошибка при обращении к LLaMA API: {response.status_code} - {response.text}")
                return {"is_reclamation": None, "error": f"API Error: {response.status_code}"}

        except Exception as e:
            logger.error(f"Ошибка при отправке запроса к LLaMA API: {e}")
            return {"is_reclamation": None, "error": f"API Error: {str(e)}"}

    
    def extract_details(self, email_data: Dict[str, Any], attachments: list, reclamation_category: str) -> Dict[str, Any]:
        """
        Извлекает детали из письма и вложений для формирования расширенной информации о рекламации
        
        Args:
            email_data: Данные письма
            attachments: Информация о вложениях
            reclamation_category: Категория рекламации
            
        Returns:
            Детальная информация о рекламации
        """
        try:
            # Формируем промпт для извлечения деталей
            logger.info(f"Извлечение деталей рекламации категории: {reclamation_category}")
            prompt = format_details_extraction_prompt(email_data, attachments, reclamation_category)
            
            # Подготавливаем запрос к Ollama Native API
            # ВАЖНО: используем /api/chat с options.num_ctx для увеличенного контекста
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты - специалист по извлечению подробной информации из рекламаций."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "stream": False,
                "options": {
                    "num_ctx": self.num_ctx,
                    "temperature": self.temperature
                }
            }
            
            # Отправляем запрос к LLaMA API без таймаута
            start_time = time.time()
            prompt_len = len(prompt)
            logger.info(f"[EXTRACT_DETAILS] START: prompt_len={prompt_len}, category={reclamation_category}")

            try:
                response = requests.post(
                    self.api_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=600  # 10 минут максимум
                )
                elapsed = time.time() - start_time
                logger.info(f"[EXTRACT_DETAILS] RESPONSE: status={response.status_code}, time={elapsed:.1f}s")
            except requests.exceptions.Timeout:
                elapsed = time.time() - start_time
                logger.error(f"[EXTRACT_DETAILS] TIMEOUT after {elapsed:.1f}s")
                return self._create_default_details(f"Timeout after {elapsed:.1f}s")
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"[EXTRACT_DETAILS] ERROR after {elapsed:.1f}s: {e}")
                return self._create_default_details(f"Request Error: {e}")

            # Обрабатываем ответ
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    # Native Ollama API формат (не OpenAI!)
                    message = response_data.get('message', {})
                    # Qwen3 использует только content
                    ai_response = message.get('content', '{}')

                    # Пытаемся извлечь JSON из ответа
                    json_match = re.search(r'(\{.*\})', ai_response, re.DOTALL)
                    if json_match:
                        ai_response = json_match.group(1)

                    result = json.loads(ai_response)
                    logger.info(f"[EXTRACT_DETAILS] SUCCESS: {len(result)} fields extracted")
                    
                    # Проверяем полученные данные и добавляем отсутствующие поля
                    default_fields = self._create_default_details("")
                    for field in default_fields:
                        if field not in result and field != "error":
                            result[field] = "n/a"
                            
                    return result
                except json.JSONDecodeError as e:
                    logger.error(f"Ошибка парсинга JSON из ответа ИИ: {e}")
                    logger.error(f"Ответ ИИ: {ai_response}")
                    return self._create_default_details("JSON Parse Error")
            else:
                logger.error(f"Ошибка при обращении к LLaMA API: {response.status_code} - {response.text}")
                return self._create_default_details(f"API Error: {response.status_code}")
        
        except Exception as e:
            logger.error(f"Ошибка при извлечении деталей: {e}")
            import traceback
            logger.error(f"Трассировка: {traceback.format_exc()}")
            return self._create_default_details(str(e))
        
    def _create_default_details(self, error_message: str = "") -> Dict[str, str]:
        """
        Создает словарь с полями по умолчанию для деталей рекламации
        
        Args:
            error_message: Сообщение об ошибке (опционально)
            
        Returns:
            Словарь с полями по умолчанию
        """
        details = {
            "product_code": "n/a",
            "model_number": "n/a",
            "serial_number": "n/a",
            "purchase_date": "n/a",
            "issue_date": "n/a",
            "customer_id": "n/a",
            "store_location": "n/a",
            "warranty_status": "n/a",
            "dealer_name": "n/a",
            "contact_person": "n/a",
            "phone_number": "n/a",
            "return_address": "n/a",
            "tracking_number": "n/a",
            "invoice_number": "n/a"
        }
        
        if error_message:
            details["error"] = error_message

        return details

    # =====================================================================
    # v2.0: Параллельный анализ документов
    # =====================================================================

    def analyze_single_document(self, document: Dict[str, Any],
                                 email_context: Dict[str, str],
                                 total_docs: int = 1) -> DocumentAnalysis:
        """
        Анализирует ОДИН документ с полным текстом.
        Используется в параллельном режиме.

        Args:
            document: {'filename': str, 'text': str, 'content_type': str}
            email_context: {'subject': str, 'sender': str, 'date': str}

        Returns:
            DocumentAnalysis с извлечёнными данными
        """
        filename = document.get('filename', 'unknown')
        text = document.get('text', '')
        content_type = document.get('content_type', 'unknown')

        # Пропускаем пустые документы
        if not text or len(text.strip()) < 10:
            logger.debug(f"[ANALYZE_DOC] Пропуск пустого документа: {filename}")
            return DocumentAnalysis(filename=filename, error="empty_text")

        # Формируем промпт (live из БД или хардкод)
        live_prompt = _get_prompt_from_db('llama_prompt', SINGLE_DOCUMENT_PROMPT)
        prompt = live_prompt.format(
            filename=filename,
            content_type=content_type,
            full_text=text[:30000],  # Ограничиваем 30K символов на документ
            email_subject=email_context.get('subject', 'n/a'),
            email_sender=email_context.get('sender', 'n/a'),
            email_date=email_context.get('date', 'n/a')
        )

        system_prompt = _get_prompt_from_db(
            'llama_system_prompt',
            "Ты - специалист по анализу документов. Правила:\n1. Один физический продукт = одна запись в products\n2. Если продукт упоминается несколько раз — объедини в одну запись\n3. Категория: определи по контексту (Наземка/Метро/Спецтехника/ЖДТ)"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": self.temperature
            }
        }

        try:
            start_time = time.time()
            logger.info(f"[ANALYZE_DOC] START: {filename} ({len(text)} символов)")

            # Динамический таймаут: больше документов = больше нагрузка на GPU
            workers = MAX_LLAMA_WORKERS or 4
            doc_timeout = int(400 * (1 + total_docs / workers))
            logger.debug(f"[ANALYZE_DOC] timeout={doc_timeout}s (docs={total_docs}, workers={workers})")

            response = requests.post(
                self.api_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=doc_timeout
            )

            elapsed = time.time() - start_time
            logger.info(f"[ANALYZE_DOC] RESPONSE: {filename} ({elapsed:.1f}s)")

            if response.status_code != 200:
                logger.error(f"[ANALYZE_DOC] HTTP {response.status_code} для {filename}")
                return DocumentAnalysis(filename=filename, error=f"HTTP {response.status_code}")

            # Парсим ответ
            response_data = response.json()
            message = response_data.get('message', {})
            ai_response = message.get('content', '')

            # Извлекаем JSON
            json_match = re.search(r'(\{.*\})', ai_response, re.DOTALL)
            if not json_match:
                logger.warning(f"[ANALYZE_DOC] Нет JSON в ответе для {filename}")
                return DocumentAnalysis(filename=filename, error="no_json")

            result = json.loads(json_match.group(1))

            # Конвертируем в DocumentAnalysis
            return DocumentAnalysis(
                filename=filename,
                doc_type=result.get('doc_type', 'unknown'),
                is_reclamation_related=result.get('is_reclamation_related', False),
                confidence=result.get('confidence', 0.0),
                products=result.get('products', []),
                dates=result.get('dates', {}),
                organizations=result.get('organizations', {}),
                contacts=result.get('contacts', {}),
                document_numbers=result.get('document_numbers', {}),
                severity=result.get('severity', 'n/a'),
                category=result.get('category', 'Неизвестно'),
                raw_findings=result.get('raw_findings', '')
            )

        except requests.exceptions.Timeout:
            logger.error(f"[ANALYZE_DOC] TIMEOUT для {filename}")
            return DocumentAnalysis(filename=filename, error="timeout")
        except json.JSONDecodeError as e:
            logger.error(f"[ANALYZE_DOC] JSON parse error для {filename}: {e}")
            return DocumentAnalysis(filename=filename, error="json_parse_error")
        except Exception as e:
            logger.error(f"[ANALYZE_DOC] Ошибка для {filename}: {e}")
            return DocumentAnalysis(filename=filename, error=str(e))

    def analyze_documents_parallel(self, documents: List[Dict[str, Any]],
                                    email_context: Dict[str, str],
                                    max_workers: int = None) -> List[DocumentAnalysis]:
        """
        Параллельно анализирует все документы.

        Args:
            documents: Список документов [{'filename': str, 'text': str, 'content_type': str}, ...]
            email_context: {'subject': str, 'sender': str, 'date': str}
            max_workers: Количество параллельных запросов (default: MAX_LLAMA_WORKERS)

        Returns:
            Список DocumentAnalysis для каждого документа
        """
        if not documents:
            return []

        workers = max_workers or MAX_LLAMA_WORKERS
        total = len(documents)
        results = []

        logger.info(f"[PARALLEL_LLAMA] Начинаем анализ {total} документов в {workers} потоках")

        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_doc = {
                    executor.submit(self.analyze_single_document, doc, email_context, total): doc
                    for doc in documents
                }

                global_timeout = int(400 * (1 + total / workers)) * (total // workers + 1)
                for future in as_completed(future_to_doc, timeout=global_timeout):
                    doc = future_to_doc[future]
                    filename = doc.get('filename', 'unknown')

                    try:
                        result = future.result(timeout=600)
                        results.append(result)
                        if result.error:
                            logger.warning(f"[PARALLEL_LLAMA] {filename}: ошибка {result.error}")
                        else:
                            logger.info(f"[PARALLEL_LLAMA] {filename}: is_recl={result.is_reclamation_related}")
                    except Exception as e:
                        logger.error(f"[PARALLEL_LLAMA] Исключение для {filename}: {e}")
                        results.append(DocumentAnalysis(filename=filename, error=str(e)))

        except Exception as e:
            logger.error(f"[PARALLEL_LLAMA] Критическая ошибка: {e}")
            logger.warning(f"[PARALLEL_LLAMA] Получено {len(results)} из {total} результатов")

        logger.info(f"[PARALLEL_LLAMA] Завершено: {len(results)}/{total} документов")

        # Статистика
        recl_count = sum(1 for r in results if r.is_reclamation_related)
        error_count = sum(1 for r in results if r.error)
        logger.info(f"[PARALLEL_LLAMA] Рекламаций: {recl_count}, ошибок: {error_count}")

        return results

    def merge_document_analyses(self, analyses: List[DocumentAnalysis],
                                 classifier_result: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Объединяет результаты анализа всех документов в итоговую структуру.

        Правила слияния:
        1. is_reclamation = ANY(is_reclamation_related=True) → True
        2. products = все уникальные продукты из всех документов
        3. Для других полей берём первое НЕ-n/a значение
        4. При конфликтах сохраняем оба значения

        Args:
            analyses: Список DocumentAnalysis
            classifier_result: Результат классификатора (для fallback)

        Returns:
            Объединённый словарь со всеми данными
        """
        if not analyses:
            # Нет документов для анализа — is_reclamation = False
            # (классификатор НЕ определяет is_reclamation, только категорию)
            return {
                'is_reclamation': False,
                'category': classifier_result.get('category', 'Неизвестно') if classifier_result else 'Неизвестно',
                'products': [],
                'source': 'no_analyses'
            }

        # Фильтруем ошибочные результаты
        valid = [a for a in analyses if not a.error]
        if not valid:
            logger.warning("[MERGE] Все документы с ошибками, is_reclamation=False")
            return {
                'is_reclamation': False,  # НЕТ fallback на классификатор!
                'category': classifier_result.get('category', 'Неизвестно') if classifier_result else 'Неизвестно',
                'products': [],
                'source': 'all_documents_failed',
                'errors': [a.error for a in analyses]
            }

        # 1. is_reclamation: ANY(True) → True
        # LLaMA = source of truth, БЕЗ OVERRIDE!
        # Никаких keyword-override — решает ТОЛЬКО LLaMA через промпт
        is_reclamation = any(a.is_reclamation_related for a in valid)

        # 2. Собираем все продукты (с нормализованной дедупликацией)
        all_products = []
        seen_products = set()
        for a in valid:
            for p in a.products:
                # LLaMA иногда возвращает строку вместо dict
                if isinstance(p, str):
                    if p and p != 'n/a':
                        p = {'name': p, 'serial_number': 'n/a', 'quantity': 'n/a'}
                    else:
                        continue
                if not isinstance(p, dict):
                    continue
                if p.get('name', 'n/a') == 'n/a' or not p.get('name', '').strip():
                    continue

                # Нормализация для дедупликации
                name = p.get('name', '').strip().lower()
                serial = p.get('serial_number', '') or ''
                serial = serial.strip() if serial and serial != 'n/a' else ''

                # С serial → ключ (name, serial), без serial → ключ только name
                product_key = (name, serial) if serial else name

                if product_key in seen_products:
                    # Обновить существующий продукт дополнительной информацией
                    if not serial:
                        for existing in all_products:
                            if existing.get('name', '').strip().lower() == name:
                                # Дополняем пустые поля
                                for field in ('issue', 'code', 'model', 'serial_number'):
                                    new_val = p.get(field, 'n/a')
                                    old_val = existing.get(field, 'n/a')
                                    if new_val and new_val != 'n/a' and (not old_val or old_val == 'n/a'):
                                        existing[field] = new_val
                                break
                else:
                    seen_products.add(product_key)
                    all_products.append(p)

        # 2b. Cross-reference: если name(B) совпадает с model/code(A) -> merge B в A
        to_remove = set()
        for i, prod_a in enumerate(all_products):
            model_a = (prod_a.get('model', '') or '').strip().lower()
            code_a = (prod_a.get('code', '') or '').strip().lower()
            for j, prod_b in enumerate(all_products):
                if i == j or j in to_remove:
                    continue
                name_b = (prod_b.get('name', '') or '').strip().lower()
                # Если имя B = модель A или имя B = код A
                if name_b and ((model_a and name_b == model_a) or (code_a and name_b == code_a)):
                    # Merge данные B в A (дополняем пустые поля)
                    for fld in ('issue', 'serial_number', 'code', 'model'):
                        val_b = prod_b.get(fld, 'n/a')
                        val_a = prod_a.get(fld, 'n/a')
                        if val_b and val_b != 'n/a' and (not val_a or val_a == 'n/a'):
                            prod_a[fld] = val_b
                    to_remove.add(j)

        if to_remove:
            logger.info(f"[MERGE] Cross-reference dedup: удалено {len(to_remove)} дублей (name=model/code)")
            all_products = [p for i, p in enumerate(all_products) if i not in to_remove]

        # 3. Определяем категорию (первая не-Неизвестно)
        category = 'Неизвестно'
        for a in valid:
            if a.category and a.category != 'Неизвестно':
                category = a.category
                break
        # Fallback на классификатор
        if category == 'Неизвестно' and classifier_result:
            category = classifier_result.get('category', 'Неизвестно')

        # 4. Собираем организации (первые НЕ-n/a)
        organizations = {}
        for a in valid:
            for key, value in a.organizations.items():
                if key not in organizations and value and value != 'n/a':
                    organizations[key] = value

        # 5. Контакты
        contacts = {}
        for a in valid:
            for key, value in a.contacts.items():
                if key not in contacts and value and value != 'n/a':
                    contacts[key] = value

        # 6. Номера документов
        document_numbers = {}
        for a in valid:
            for key, value in a.document_numbers.items():
                if key not in document_numbers and value and value != 'n/a':
                    document_numbers[key] = value

        # 7. Даты (все уникальные)
        dates = {}
        for a in valid:
            for key, value in a.dates.items():
                if value and value != 'n/a':
                    if key not in dates:
                        dates[key] = value
                    elif dates[key] != value:
                        # Конфликт дат - сохраняем оба
                        dates[key] = f"{dates[key]} / {value}"

        # 8. Severity (наивысшая)
        severity_order = ['критическая', 'высокая', 'средняя', 'низкая', 'n/a']
        severity = 'n/a'
        for a in valid:
            if a.severity in severity_order:
                if severity_order.index(a.severity) < severity_order.index(severity):
                    severity = a.severity

        # 9. Собираем doc_types для отладки
        doc_types = [a.doc_type for a in valid if a.doc_type != 'unknown']

        # Собираем raw_findings
        raw_findings = []
        for a in valid:
            if a.raw_findings and a.raw_findings != 'n/a':
                raw_findings.append(f"[{a.filename}]: {a.raw_findings}")

        # 10. Формируем reasoning (объяснение решения)
        reasoning_parts = []
        for a in valid:
            status = "РЕКЛАМАЦИЯ" if a.is_reclamation_related else "не рекламация"
            reasoning_parts.append(f"[{a.filename}] {a.doc_type} -> {status} (conf={a.confidence:.2f})")
        recl_count = sum(1 for a in valid if a.is_reclamation_related)
        total_count = len(valid)
        if is_reclamation:
            reasoning_summary = f"Решение: РЕКЛАМАЦИЯ ({recl_count}/{total_count} документов рекламационные, {len(all_products)} продуктов)"
        else:
            if not all_products:
                reasoning_summary = f"Решение: НЕ рекламация ({recl_count}/{total_count} рекламационных, 0 продуктов)"
            else:
                reasoning_summary = f"Решение: НЕ рекламация ({recl_count}/{total_count} документов рекламационные)"
        reasoning = reasoning_summary + '\n' + '\n'.join(reasoning_parts)

        # Формируем итоговый результат
        merged = {
            'is_reclamation': is_reclamation,
            'category': category,
            'products': all_products,
            'organizations': organizations,
            'contacts': contacts,
            'document_numbers': document_numbers,
            'dates': dates,
            'severity': severity,
            'doc_types': doc_types,
            'raw_findings': '\n'.join(raw_findings) if raw_findings else 'n/a',
            'reasoning': reasoning,
            'documents_analyzed': len(valid),
            'documents_with_errors': len(analyses) - len(valid),
            'confidence': max(a.confidence for a in valid) if valid else 0.0
        }

        # Извлекаем основные поля для совместимости со старым форматом
        # При N продуктах — конкатенируем в одну строку (1 email = 1 запись Bitrix)
        merged['product_name'] = _concat_product_field(all_products, 'name')
        merged['serial_number'] = _concat_product_field(all_products, 'serial_number')
        merged['issue_description'] = _concat_issues(all_products)

        merged['customer_name'] = organizations.get('customer', 'n/a')
        merged['dealer_name'] = organizations.get('dealer', 'n/a')
        merged['contact_person'] = contacts.get('person', 'n/a')
        merged['phone_number'] = contacts.get('phone', 'n/a')
        merged['invoice_number'] = document_numbers.get('invoice', 'n/a')

        # === Compat-алиасы для connector (BUG-3, BUG-4 FIX) ===
        merged['reclamation_category'] = category
        merged['act_number'] = document_numbers.get('act', 'n/a')
        merged['product_code'] = _concat_product_field(all_products, 'code')
        merged['model_number'] = _concat_product_field(all_products, 'model')
        merged['purchase_date'] = dates.get('production', 'n/a')
        merged['issue_date'] = dates.get('issue_occurred', 'n/a')
        merged['store_location'] = contacts.get('address', 'n/a')
        merged['return_address'] = contacts.get('address', 'n/a')
        merged['tracking_number'] = 'n/a'
        merged['warranty_status'] = 'n/a'
        merged['customer_id'] = 'n/a'

        if len(all_products) > 1:
            logger.info(f"[MERGE] {len(all_products)} продуктов -> конкатенация в 1 запись Bitrix")
        logger.info(f"[MERGE] Итог: is_recl={is_reclamation}, products={len(all_products)}, category={category}")
        return merged