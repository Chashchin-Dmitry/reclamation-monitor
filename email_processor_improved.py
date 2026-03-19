"""
Тестовый обработчик писем с рекламациями

Этот модуль содержит улучшенный код для:
1. Поиска и загрузки писем за указанную дату
2. Скачивания вложений и извлечения текста из них
3. Определения типа рекламации на основе содержимого
4. Пересылки рекламаций на тестовые адреса с подробными пояснениями
"""
import os
import imaplib
import email
import mimetypes
import hashlib
from email.header import decode_header
import email.utils
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import logging
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional

# Импортируем наши классы
from zip_processor import integrate_zip_processor
from multi_reclamation_processor import MultiReclamationProcessor
from reclamation_classifier import ReclamationClassifier, AttachmentProcessor
from llama_api_module import LLaMAAnalyzer
from bitrix24_integration import Bitrix24Integration
from reclamation_bitrix_connector import ReclamationBitrixConnector

# v2.0: Параллельная обработка
from ocr_processor import (
    process_attachments_parallel,
    merge_ocr_results_to_attachments,
    OCRResult
)

import requests
# Загрузка переменных из .env файла
from dotenv import load_dotenv
load_dotenv()  # загружает переменные окружения из .env файла


# Настройка логирования
log_file = os.path.join(os.path.dirname(__file__), 'email_processor.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),  # Используем переменную с путем
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("EmailProcessor")

# Проверка наличия важных переменных
if not os.getenv('EMAIL_USER'):
    print("ВНИМАНИЕ: EMAIL_USER не установлен в .env файле")

if not os.getenv('IMAP_PASSWORD'):
    print("ВНИМАНИЕ: IMAP_PASSWORD не установлен в .env файле")
    
if not os.getenv('SMTP_PASSWORD'):
    print("ВНИМАНИЕ: SMTP_PASSWORD не установлен в .env файле")

# Корректировка настроек EMAIL_CONFIG и SMTP_CONFIG
EMAIL_CONFIG = {
    'user': os.getenv('EMAIL_USER', ''),
    'password': os.getenv('IMAP_PASSWORD'),
    'host': os.getenv('EMAIL_HOST', 'imap.yandex.ru'),
}

SMTP_CONFIG = {
    'host': os.getenv('SMTP_HOST', 'smtp.yandex.ru'),
    'port': int(os.getenv('SMTP_PORT', 465)),
    'user': os.getenv('EMAIL_USER', ''),
    'password': os.getenv('SMTP_PASSWORD'),
}

# Адреса для пересылки рекламаций (из .env)
TEST_RECIPIENTS = {
    'default': os.getenv('DEFAULT_RECIPIENT', ''),
    'copy': os.getenv('COPY_RECIPIENT', '')
}

# Папка для сохранения вложений
ATTACHMENTS_FOLDER = os.path.join(os.path.dirname(__file__), 'attachments')
os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)

# v2.0: Режим параллельной обработки
# True = новая архитектура (параллельный OCR + per-document LLaMA)
# False = старая архитектура (последовательная обработка)
PARALLEL_MODE = os.getenv('PARALLEL_MODE', 'true').lower() == 'true'

# Тестовый режим - только поиск писем за указанную дату
TEST_DATE = os.getenv('TEST_DATE', '10-Apr-2025')  # Формат для IMAP

# Добавьте эту функцию в начало файла email_processor_improved.py
def get_date_for_imap():
        """
        Определяет дату для поиска писем в IMAP
        
        Приоритеты:
        1. Если в .env указан USE_CURRENT_DATE=true, используется текущая дата
        2. Если в .env указан TEST_DATE, используется он
        3. По умолчанию используется текущая дата
        
        Returns:
            Строка с датой в формате DD-Mon-YYYY (например, '17-Apr-2025')
        """
        from datetime import datetime
        
        # Проверяем настройку USE_CURRENT_DATE и явно показываем значение в логе
        use_current_date_str = os.getenv('USE_CURRENT_DATE', 'false')
        use_current_date = use_current_date_str.lower() == 'true'
        logger.info(f"Значение переменной USE_CURRENT_DATE: '{use_current_date_str}', преобразовано в {use_current_date}")
        
        # Проверяем наличие TEST_DATE
        test_date = os.getenv('TEST_DATE')
        logger.info(f"Значение переменной TEST_DATE: '{test_date}'")
        
        current_date = datetime.now()
        current_date_str = current_date.strftime('%d-%b-%Y')
        
        if use_current_date:
            # Если явно указано использовать текущую дату
            logger.info(f"Используем текущую дату (указано в USE_CURRENT_DATE): {current_date_str}")
            return current_date_str
        elif test_date:
            # Если указана тестовая дата, используем её
            logger.info(f"Используем тестовую дату из TEST_DATE: {test_date}")
            return test_date
        else:
            # По умолчанию - текущая дата
            logger.info(f"Используем текущую дату (по умолчанию): {current_date_str}")
            return current_date_str


class EmailProcessor:
    """Улучшенный обработчик писем с рекламациями"""

    # Путь к файлу кэша обработанных писем
    PROCESSED_CACHE_FILE = os.path.join(os.path.dirname(__file__), 'processed_reclamations.json')

    def __init__(self):
        self.imap = None
        self.classifier = ReclamationClassifier()
        self.attachment_processor = AttachmentProcessor()

        # Загружаем кэш обработанных писем
        self.processed_cache = self._load_processed_cache()
        # Расширяем функциональность для работы с ZIP-файлами
        self.attachment_processor = integrate_zip_processor(self.attachment_processor)
        # Кэш для хранения загруженных писем
        self.email_cache = {}
        self.llama_analyzer = LLaMAAnalyzer()

        # Инициализация интеграции с Битрикс24
        self.bitrix_connector = None
        webhook_url = os.getenv('BITRIX24_WEBHOOK')
        if webhook_url:
            try:
                self.bitrix_connector = ReclamationBitrixConnector(webhook_url)
                logger.info("Интеграция с Битрикс24 инициализирована")
            except Exception as e:
                logger.error(f"Ошибка при инициализации Битрикс24: {e}")
        
        # Загружаем карту распределения из файла
        distribution_file = os.path.join(os.path.dirname(__file__), 'Рекламаци Эпотос.xlsx')
        try:
            # Проверяем существование файла
            if os.path.exists(distribution_file):
                self.classifier.load_distribution_map_from_file(distribution_file)
                logger.info(f"Карта распределения рекламаций загружена из {distribution_file}")
            else:
                logger.warning(f"Файл распределения не найден: {distribution_file}")
                logger.warning("Используем тестовую карту распределения")
                self.classifier.set_test_distribution_map()
        except Exception as e:
            logger.error(f"Не удалось загрузить карту распределения: {e}")
            logger.warning("Используем тестовую карту распределения")
            self.classifier.set_test_distribution_map()
        
        # Проверяем наличие паролей
        if EMAIL_CONFIG['password'] is None:
            logger.warning("IMAP пароль не установлен в переменных окружения")
        
        if SMTP_CONFIG['password'] is None:
            logger.warning("SMTP пароль не установлен в переменных окружения")

    def check_llama_availability(self) -> bool:
        """
        Проверяет доступность LLaMA API перед отправкой запросов
        
        Returns:
            bool: True если API доступен, False в противном случае
        """
        try:
            import requests
            # Получаем базовый URL (без путей)
            api_url = self.llama_analyzer.api_url
            # Извлекаем base URL: http://localhost:11434
            if '/api/' in api_url:
                base_url = api_url.split('/api/')[0]
                check_url = f"{base_url}/api/tags"
            elif '/v1/' in api_url:
                base_url = api_url.split('/v1/')[0]
                check_url = f"{base_url}/v1/models"
            else:
                base_url = api_url.rsplit('/', 1)[0]
                check_url = f"{base_url}/api/tags"

            # Пробуем подключиться с увеличенным таймаутом
            response = requests.get(check_url, timeout=15)
            if response.status_code == 200:
                logger.info("LLaMA API доступен")
                return True
            else:
                logger.warning(f"LLaMA API вернул код {response.status_code}")
                return False
        except Exception as e:
            logger.warning(f"LLaMA API не доступен: {e}")
            return False

    def _load_processed_cache(self) -> Dict[str, Any]:
        """
        Загружает кэш обработанных писем из файла

        Returns:
            Словарь с processed_ids и метаданными
        """
        if os.path.exists(self.PROCESSED_CACHE_FILE):
            try:
                with open(self.PROCESSED_CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                    logger.info(f"Загружен кэш: {len(cache.get('processed_ids', []))} обработанных писем")
                    return cache
            except Exception as e:
                logger.error(f"Ошибка загрузки кэша обработанных писем: {e}")

        return {"processed_ids": [], "processed_details": {}, "last_run": None}

    def _save_processed_cache(self) -> None:
        """Сохраняет кэш обработанных писем в файл"""
        try:
            self.processed_cache["last_run"] = datetime.now().isoformat()
            with open(self.PROCESSED_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.processed_cache, f, ensure_ascii=False, indent=2)
            logger.info(f"Кэш сохранен: {len(self.processed_cache.get('processed_ids', []))} обработанных писем")
        except Exception as e:
            logger.error(f"Ошибка сохранения кэша: {e}")

    def _is_email_processed(self, email_id: str) -> bool:
        """
        Проверяет, было ли письмо уже обработано

        Args:
            email_id: ID письма

        Returns:
            True если письмо уже обработано
        """
        email_id_str = str(email_id)
        processed_ids = self.processed_cache.get("processed_ids", [])

        # Проверяем точное совпадение или совпадение с префиксом (для unique_key = email_id_product)
        for pid in processed_ids:
            if pid == email_id_str or pid.startswith(f"{email_id_str}_"):
                return True
        return False

    def _mark_email_processed(self, email_id: str, details: Dict[str, Any] = None) -> None:
        """
        Помечает письмо как обработанное

        Args:
            email_id: ID письма
            details: Дополнительные детали обработки (опционально)
        """
        email_id_str = str(email_id)
        if email_id_str not in self.processed_cache.get("processed_ids", []):
            self.processed_cache.setdefault("processed_ids", []).append(email_id_str)

            # Сохраняем детали обработки
            if details:
                self.processed_cache.setdefault("processed_details", {})[email_id_str] = {
                    "processed_at": datetime.now().isoformat(),
                    "subject": details.get("subject", ""),
                    "is_reclamation": details.get("is_reclamation", False),
                    "category": details.get("category", "")
                }

            logger.info(f"Письмо {email_id} помечено как обработанное")

    def _cleanup_old_cache_entries(self, days: int = 30) -> None:
        """
        Удаляет записи старше указанного количества дней

        Args:
            days: Количество дней для хранения записей
        """
        if "processed_details" not in self.processed_cache:
            return

        cutoff_date = datetime.now() - timedelta(days=days)
        old_ids = []

        for email_id, details in self.processed_cache.get("processed_details", {}).items():
            processed_at = details.get("processed_at")
            if processed_at:
                try:
                    processed_date = datetime.fromisoformat(processed_at)
                    if processed_date < cutoff_date:
                        old_ids.append(email_id)
                except:
                    pass

        # Удаляем старые записи
        for email_id in old_ids:
            if email_id in self.processed_cache.get("processed_ids", []):
                self.processed_cache["processed_ids"].remove(email_id)
            if email_id in self.processed_cache.get("processed_details", {}):
                del self.processed_cache["processed_details"][email_id]

        if old_ids:
            logger.info(f"Удалено {len(old_ids)} старых записей из кэша (старше {days} дней)")

    def connect(self) -> bool:
        """
        Подключение к почтовому серверу
        
        Returns:
            bool: Успешность подключения
        """
        try:

            # Проверяем, что пароль не None
            if EMAIL_CONFIG['password'] is None:
                logger.error("Пароль не установлен в переменных окружения")
                return False
            self.imap = imaplib.IMAP4_SSL(EMAIL_CONFIG['host'], timeout=60)
            self.imap.login(EMAIL_CONFIG['user'], EMAIL_CONFIG['password'])
            logger.info(f"Успешное подключение к почтовому серверу {EMAIL_CONFIG['host']}")
            return True
        except Exception as e:
            logger.error(f"Ошибка подключения к почтовому серверу: {e}")
            return False
    
    def disconnect(self) -> None:
        """Отключение от почтового сервера"""
        if self.imap:
            try:
                self.imap.logout()
                logger.info("Отключение от почтового сервера выполнено")
            except Exception as e:
                logger.error(f"Ошибка при отключении от почтового сервера: {e}")
            finally:
                self.imap = None
    
    def find_emails_by_date(self, date_str: str) -> List[str]:
        """
        Поиск писем за указанную дату
        
        Args:
            date_str: Дата в формате IMAP, например '10-Apr-2025'
            
        Returns:
            Список ID писем
        """
        if not self.imap:
            logger.error("Нет подключения к почтовому серверу")
            return []
        
        try:
            # Переходим в папку INBOX
            status, _ = self.imap.select('INBOX')
            if status != 'OK':
                logger.error(f"Ошибка при выборе папки: {status}")
                return []
            
            # Ищем письма с указанной датой
            status, data = self.imap.search(None, f'(ON {date_str})')
            if status != 'OK' or not data or not data[0]:
                logger.info(f"Новых писем за {date_str} не обнаружено")
                return []
            
            email_ids = data[0].split()
            logger.info(f"Найдено {len(email_ids)} писем за {date_str}")
            
            return [email_id.decode() for email_id in email_ids]
        
        except Exception as e:
            logger.error(f"Ошибка при поиске писем: {e}")
            return []
    
    def fetch_email(self, email_id: str) -> Optional[Dict[str, Any]]:
        """
        Получает письмо по ID
        
        Args:
            email_id: ID письма
            
        Returns:
            Словарь с данными письма или None в случае ошибки
        """
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                if not self.imap:
                    logger.error("Нет подключения к почтовому серверу")
                    return None
                
                # Убедимся, что папка INBOX выбрана
                try:
                    status, _ = self.imap.select('INBOX')
                    if status != 'OK':
                        logger.error(f"Ошибка при выборе папки: {status}")
                        return None
                except Exception as select_error:
                    logger.error(f"Ошибка при выборе папки INBOX: {select_error}")
                    return None
                
                # Получаем письмо целиком
                res, msg_data = self.imap.fetch(email_id, '(RFC822)')
                if res != 'OK' or not msg_data or not msg_data[0]:
                    logger.error(f"Ошибка получения письма {email_id}")
                    return None
                
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                
                # ---------- Декодирование заголовка Subject ----------
                raw_subject = msg.get('Subject') or ''
                subject_parts = decode_header(raw_subject)
                if subject_parts:
                    subj, enc = subject_parts[0]
                    if isinstance(subj, bytes):
                        subject = subj.decode(enc or 'utf-8', errors='ignore')
                    else:
                        subject = subj
                else:
                    subject = ''
                
                # ---------- Декодирование заголовка From ----------
                raw_from = msg.get('From') or ''
                sender_name, sender_addr = email.utils.parseaddr(raw_from)
                # Декодируем display name если закодирован
                if sender_name:
                    parts = decode_header(sender_name)
                    decoded_parts = []
                    for data, enc in parts:
                        if isinstance(data, bytes):
                            decoded_parts.append(data.decode(enc or 'utf-8', errors='ignore'))
                        else:
                            decoded_parts.append(data)
                    sender_name = ' '.join(decoded_parts)
                sender = f"{sender_name} <{sender_addr}>" if sender_addr else sender_name
                
                # ---------- Парсим дату письма из заголовка Date ----------
                date_str = msg.get('Date') or ''
                try:
                    received_date = email.utils.parsedate_to_datetime(date_str)
                except Exception:
                    received_date = datetime.now()
                
                # ---------- Извлечение тела письма (body) ----------
                body = ''
                if msg.is_multipart():
                    # Идём по частям письма
                    for part in msg.walk():
                        ctype = part.get_content_type()
                        cdispo = str(part.get("Content-Disposition"))
                        # Ищем простую текстовую часть, игнорируем вложения
                        if ctype == "text/plain" and "attachment" not in cdispo:
                            payload = part.get_payload(decode=True) or b''
                            charset = part.get_content_charset() or 'utf-8'
                            body += payload.decode(charset, errors='ignore')
                else:
                    # Если письмо не содержит вложений
                    payload = msg.get_payload(decode=True) or b''
                    charset = msg.get_content_charset() or 'utf-8'
                    body = payload.decode(charset, errors='ignore')
                
                # Возвращаем данные письма
                return {
                    "id": email_id,
                    "msg": msg,  # Оригинальный объект письма для скачивания вложений
                    "subject": subject,
                    "body": body,
                    "sender": sender,
                    "sender_addr": sender_addr,  # Чистый email адрес (для outgoing check)
                    "received_date": received_date,
                    "attachments": []  # Пока пустой список, заполним позже
                }
            
            except Exception as e:
                logger.error(f"Ошибка при получении письма {email_id}: {e}")
                
                # Если это не последняя попытка, пробуем переподключиться
                if attempt < max_retries - 1:
                    logger.info(f"Попытка {attempt + 1} не удалась, переподключение...")
                    try:
                        # Закрываем старое соединение
                        self.disconnect()
                        time.sleep(retry_delay)  # Небольшая пауза
                        # Пробуем подключиться заново
                        if self.connect():
                            logger.info("Успешное переподключение к серверу")
                            continue
                    except Exception as reconnect_error:
                        logger.error(f"Ошибка при попытке переподключения: {reconnect_error}")
                
                return None
    
    def download_attachments(self, email_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Скачивает вложения письма и извлекает из них текст
        
        Args:
            email_data: Данные письма
            
        Returns:
            Список с информацией о вложениях
        """
        if not email_data or 'msg' not in email_data:
            logger.error("Некорректные данные письма для скачивания вложений")
            return []
        
        msg = email_data['msg']
        email_id = email_data['id']
        subject = email_data['subject']
        
        # Создаем безопасное имя папки из темы письма
        safe_subject = "".join(c for c in subject if c.isalnum() or c in " ._-").strip()
        if not safe_subject:
            safe_subject = f"email_{email_id}"

        # Ограничиваем длину имени папки (Windows MAX_PATH = 260, оставляем запас для файлов)
        MAX_FOLDER_NAME_LENGTH = 100
        if len(safe_subject) > MAX_FOLDER_NAME_LENGTH:
            # Обрезаем и добавляем хэш для уникальности
            subject_hash = hashlib.md5(subject.encode('utf-8', errors='ignore')).hexdigest()[:8]
            safe_subject = safe_subject[:MAX_FOLDER_NAME_LENGTH - 10] + f"_{subject_hash}"
            logger.info(f"Имя папки обрезано до: {safe_subject}")

        # Создаем папку для вложений этого письма
        folder_path = os.path.join(ATTACHMENTS_FOLDER, safe_subject)
        try:
            os.makedirs(folder_path, exist_ok=True)
        except OSError as e:
            logger.error(f"Ошибка создания папки {folder_path}: {e}")
            # Fallback: используем только ID письма
            safe_subject = f"email_{email_id}"
            folder_path = os.path.join(ATTACHMENTS_FOLDER, safe_subject)
            try:
                os.makedirs(folder_path, exist_ok=True)
            except OSError as e2:
                logger.error(f"Критическая ошибка создания папки: {e2}")
                return []
        
        attachments_saved = []
        
        # Перебираем части письма
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            
            # Проверяем, является ли часть вложением
            # Обратите внимание: некоторые письма могут содержать вложения без явного Content-Disposition
            is_attachment = False
            if part.get('Content-Disposition') is not None:
                is_attachment = 'attachment' in part.get('Content-Disposition')
            # Также проверяем наличие имени файла в Content-Type
            elif part.get_filename():
                is_attachment = True
            # Пропускаем текстовые части, которые не являются вложениями
            elif part.get_content_type() == 'text/plain' or part.get_content_type() == 'text/html':
                continue
            
            if not is_attachment:
                continue
            
            filename = part.get_filename()
            if not filename:
                # Если имя не указано, генерируем его
                ext = mimetypes.guess_extension(part.get_content_type())
                if not ext:
                    ext = '.bin'
                filename = f'unknown_{len(attachments_saved)}{ext}'
            
            # Декодируем имя файла, если необходимо
            try:
                filename_parts = decode_header(filename)
                if filename_parts:
                    filename_bytes, charset = filename_parts[0]
                    if isinstance(filename_bytes, bytes):
                        filename = filename_bytes.decode(charset or 'utf-8', errors='ignore')
                    else:
                        filename = str(filename_bytes)  # Если это уже строка

                # Санитизация имени файла
                # 1. Отделяем расширение (последняя точка + буквы/цифры)
                if '.' in filename:
                    name_part, ext_part = filename.rsplit('.', 1)
                    # Проверяем что расширение валидное (только буквы/цифры, до 10 символов)
                    if ext_part and len(ext_part) <= 10 and ext_part.replace('_', '').replace('-', '').isalnum():
                        ext_part = '.' + ext_part
                    else:
                        # Это не настоящее расширение, включаем обратно в имя
                        name_part = filename
                        ext_part = ''
                else:
                    name_part = filename
                    ext_part = ''

                # 2. Заменяем все точки в имени на подчёркивания
                name_part = name_part.replace('.', '_')

                # 3. Оставляем только безопасные символы (буквы, цифры, пробел, _, -)
                name_part = "".join(c for c in name_part if c.isalnum() or c in " _-").strip()

                # 4. Ограничиваем длину имени (без расширения)
                MAX_FILENAME_LENGTH = 100
                if len(name_part) > MAX_FILENAME_LENGTH:
                    file_hash = hashlib.md5(filename.encode('utf-8', errors='ignore')).hexdigest()[:8]
                    name_part = name_part[:MAX_FILENAME_LENGTH - 10] + f"_{file_hash}"

                # 5. Собираем обратно
                filename = name_part + ext_part

                if not filename or filename == ext_part:
                    # Если после фильтрации имя файла пустое, создаем новое
                    ext = mimetypes.guess_extension(part.get_content_type())
                    if not ext:
                        ext = '.bin'
                    filename = f'unknown_{len(attachments_saved)}{ext}'
            except Exception as e:
                logger.error(f"Ошибка при декодировании имени файла: {e}")
                ext = mimetypes.guess_extension(part.get_content_type())
                if not ext:
                    ext = '.bin'
                filename = f'error_decode_{len(attachments_saved)}{ext}'
            
            # Путь для сохранения вложения
            filepath = os.path.join(folder_path, filename)
            
            # Проверка на дублирование имен файлов
            counter = 1
            while os.path.exists(filepath):
                name, ext = os.path.splitext(filename)
                new_filename = f"{name}_{counter}{ext}"
                filepath = os.path.join(folder_path, new_filename)
                counter += 1
                
            # Сохраняем вложение с проверкой на дубликаты
            try:
                payload = part.get_payload(decode=True)
                if payload is not None:
                    # Вычисляем хеш содержимого для проверки дубликатов
                    file_hash = hashlib.md5(payload).hexdigest()

                    # Проверяем, есть ли уже файл с таким содержимым в папке
                    duplicate_found = False
                    existing_filepath = None
                    if os.path.exists(folder_path):
                        for existing_file in os.listdir(folder_path):
                            existing_path = os.path.join(folder_path, existing_file)
                            if os.path.isfile(existing_path) and os.path.getsize(existing_path) == len(payload):
                                with open(existing_path, 'rb') as f:
                                    if hashlib.md5(f.read()).hexdigest() == file_hash:
                                        logger.info(f"Пропускаем дубликат: {filename} (совпадает с {existing_file})")
                                        filepath = existing_path
                                        duplicate_found = True
                                        break

                    # Сохраняем только если это не дубликат
                    if not duplicate_found:
                        with open(filepath, 'wb') as f:
                            f.write(payload)
                        logger.info(f"Сохранено вложение: {filename}, размер: {len(payload)} байт")
                else:
                    logger.warning(f"Вложение {filename} имеет пустой payload, пропускаем")
                    continue
            except Exception as e:
                logger.error(f"Ошибка при сохранении вложения {filename}: {e}")
                continue
            
            # Получаем тип содержимого
            content_type = part.get_content_type() or 'application/octet-stream'
            
            # Извлекаем текст из вложения
            try:
                extracted_text = self.attachment_processor.process_attachment(filepath)
            except Exception as e:
                logger.error(f"Ошибка при извлечении текста из вложения {filename}: {e}")
                extracted_text = f"[Ошибка извлечения текста: {str(e)}]"
            
            # Сохраняем информацию о вложении
            attachment_info = {
                'filename': filename,
                'filepath': filepath,
                'content_type': content_type,
                'extracted_text': extracted_text,
                'size': os.path.getsize(filepath)
            }
            
            attachments_saved.append(attachment_info)
            logger.info(f"Сохранено вложение: {filename}, размер: {os.path.getsize(filepath)} байт, тип: {content_type}")

        # Дедупликация: если имя файла 1:1 совпадает — оставляем последний
        seen = {}
        for att in attachments_saved:
            name = att['filename']
            if name in seen:
                logger.info(f"Дубликат вложения '{name}': заменяем предыдущий на последний")
            seen[name] = att

        if len(seen) < len(attachments_saved):
            logger.info(f"Дедупликация вложений: {len(attachments_saved)} -> {len(seen)}")
            attachments_saved = list(seen.values())

        return attachments_saved
    
    def process_email(self, email_id: str, skip_bitrix: bool = False) -> Dict[str, Any]:
        """
        Обрабатывает письмо: загружает данные, скачивает вложения и классифицирует

        Args:
            email_id: ID письма
            skip_bitrix: Если True, не отправлять в Bitrix (используется при разделении на множественные рекламации)

        Returns:
            Результат обработки письма
        """
        try:
            # Загружаем данные письма
            email_data = self.fetch_email(email_id)
            if not email_data:
                logger.error(f"Не удалось загрузить письмо {email_id}")
                return {"success": False, "error": "Ошибка загрузки письма"}
            
            # Скачиваем вложения
            attachments = self.download_attachments(email_data)
            email_data['attachments'] = attachments
            
            logger.info(f"Начинаем классификацию письма {email_id}")

            # Классифицируем (категория + blacklist, НЕ is_reclamation!)
            classification = self.classifier.classify_reclamation(email_data, attachments)
            logger.info(f"Результат классификатора: {classification}")

            # BLACKLIST CHECK — быстрый выход для бухгалтерии/счетов
            if classification.get('blacklisted'):
                blacklist_keyword = classification.get('blacklist_keyword', 'unknown')
                reasoning = f"Blacklist: тема содержит '{blacklist_keyword}'. Бухгалтерский документ."
                logger.info(f"[BLACKLIST] Email {email_id}: {reasoning}")

                return {
                    'email_id': email_id,
                    'subject': email_data.get('subject', ''),
                    'is_reclamation': False,
                    'reasoning': reasoning,
                    'decision_path': 'blacklist',
                    'category': classification.get('category'),
                    'classification': classification,
                    'llama_analysis': None
                }

            # LLaMA ANALYSIS — единственный источник правды для is_reclamation
            llama_result = {'is_reclamation': None}
            reasoning = ""
            decision_path = "llama"

            if self.check_llama_availability():
                logger.info(f"Запрос анализа в LLaMA для письма {email_id}")
                try:
                    llama_result = self.llama_analyzer.analyze_email(email_data, attachments)
                    logger.info(f"Результат LLaMA: {llama_result}")
                except Exception as e:
                    logger.error(f"Ошибка при анализе LLaMA: {e}")
                    llama_result = {"is_reclamation": None, "error": str(e)}
            else:
                logger.warning("LLaMA API недоступен")
                llama_result = {"is_reclamation": None, "error": "LLaMA API недоступен"}

            # ПРИНЯТИЕ РЕШЕНИЯ — LLaMA = source of truth
            llama_is_reclamation = llama_result.get('is_reclamation')

            if llama_is_reclamation is None or 'error' in llama_result:
                # LLaMA недоступен или ошибка — лучше пропустить, чем ложное срабатывание
                is_reclamation = False
                reasoning = f"LLaMA unavailable: {llama_result.get('error', 'is_reclamation=None')}"
                decision_path = "llama_error"
                logger.warning(f"[DECISION] {reasoning} -> is_reclamation=False")
            else:
                # LLaMA решил
                is_reclamation = bool(llama_is_reclamation)
                llama_reasoning = llama_result.get('reasoning', llama_result.get('reason', ''))
                if is_reclamation:
                    reasoning = f"LLaMA: {llama_reasoning[:200]}" if llama_reasoning else "LLaMA: определено как рекламация"
                else:
                    reasoning = f"LLaMA: {llama_reasoning[:200]}" if llama_reasoning else "LLaMA: не рекламация"
                logger.info(f"[DECISION] {reasoning}")

            # Обновляем classification
            classification['is_reclamation'] = is_reclamation
            if is_reclamation and not classification.get('category'):
                classification['category'] = "Рекламации Тестовая"
            
            # Извлекаем детали, если это рекламация
            details_data = {}
            if is_reclamation:
                logger.info(f"Письмо {email_id} определено как рекламация, извлекаем детали")
                category_value = classification.get('category')
                category = category_value.replace('Рекламации ', '') if category_value else 'Тестовая'
                
                if self.check_llama_availability():
                    try:
                        details_data = self.llama_analyzer.extract_details(email_data, attachments, category)
                        logger.info(f"Извлечены детали рекламации: {list(details_data.keys())}")
                    except Exception as e:
                        logger.error(f"Ошибка при извлечении деталей: {e}")
                        details_data = {}
                else:
                    logger.warning("LLaMA API недоступен, пропускаем извлечение деталей")
                    details_data = {}
            
            # Формируем объединенный результат
            result = {
                "success": True,
                "email_id": email_id,
                "subject": email_data['subject'],
                "sender": email_data['sender'],  # ─── добавили
                "received_date": email_data['received_date'], # ─── и эту дату
                "attachments": attachments, # ─── чтобы битрикс получил пути к файлам
                "is_reclamation": is_reclamation,
                "category": classification.get('category'),
                "subcategories": classification.get('subcategories', []),
                "recipients": classification.get('recipients', []),
                "copy_to": classification.get('copy_to', []),
                "attachments_count": len(attachments),
                "llama_analysis": llama_result,
                "details": details_data
            }

            
            # Если это рекламация, пересылаем её
            if is_reclamation:
                logger.info(f"Рекламация обнаружена для {email_id}")
                # TODO: ВРЕМЕННО ОТКЛЮЧЕНО - исправляем повторную обработку
                # forward_success = self.forward_reclamation(email_data, result)
                # result["forwarded"] = forward_success
                # if forward_success:
                #     logger.info(f"Рекламация {email_id} успешно переслана")
                # else:
                #     logger.warning(f"Не удалось переслать рекламацию {email_id}")
                result["forwarded"] = False  # Временно отключено
                logger.info(f"Пересылка временно отключена для {email_id}")

                # Отправляем в Bitrix только если skip_bitrix=False
                # (skip_bitrix=True используется при разделении на множественные рекламации)
                if not skip_bitrix and self.bitrix_connector:
                    try:
                        element_id = self.bitrix_connector.process_reclamation(result)
                        if element_id:
                            logger.info(f"Рекламация отправлена в Битрикс24: ID={element_id}")
                            result["bitrix24_id"] = element_id
                    except Exception as e:
                        logger.error(f"Ошибка при отправке в Битрикс24: {e}")
                elif skip_bitrix:
                    logger.info(f"Отправка в Bitrix отложена (skip_bitrix=True) для {email_id}")

            else:
                logger.info(f"Письмо {email_id} не является рекламацией")
            
            logger.info(f"Обработано письмо: {email_id}, результат: {result['is_reclamation']}, категория: {result['category']}")
            return result

        except Exception as e:
            logger.error(f"Ошибка при обработке письма {email_id}: {e}")
            import traceback
            logger.error(f"Трассировка ошибки: {traceback.format_exc()}")
            return {"success": False, "error": str(e)}

    def process_email_parallel(self, email_id: str, skip_bitrix: bool = False) -> Dict[str, Any]:
        """
        v2.0: Обрабатывает письмо с параллельным OCR и параллельным анализом документов.

        Архитектура:
        1. Fetch email + download attachments
        2. Classifier (keyword-based) - быстрая проверка
        3. Параллельный OCR всех вложений (ThreadPoolExecutor, 12 workers)
        4. Параллельный LLaMA анализ каждого документа (4 workers)
        5. Intelligent merge результатов
        6. Отправка в Bitrix

        Args:
            email_id: ID письма
            skip_bitrix: Если True, не отправлять в Bitrix

        Returns:
            Результат обработки письма
        """
        import time
        start_time = time.time()

        try:
            # === PHASE 0: INTAKE ===
            logger.info(f"[PARALLEL] Начало обработки письма {email_id}")
            log_entries = []  # Цепочка обработки

            # Загружаем данные письма
            email_data = self.fetch_email(email_id)
            if not email_data:
                logger.error(f"[PARALLEL] Не удалось загрузить письмо {email_id}")
                return {"success": False, "error": "Ошибка загрузки письма"}

            # Скачиваем вложения (ещё не параллельно - это I/O операции)
            attachments = self.download_attachments(email_data)
            email_data['attachments'] = attachments

            elapsed = time.time() - start_time
            log_entries.append(f"[{elapsed:.1f}s] Загрузка: {len(attachments)} вложений скачано")

            # Классификация (категория + blacklist, НЕ is_reclamation!)
            classification = self.classifier.classify_reclamation(email_data, attachments)
            classifier_score = classification.get('score', 0)

            elapsed = time.time() - start_time
            log_entries.append(f"[{elapsed:.1f}s] Классификация: score={classifier_score}, blacklisted={classification.get('blacklisted')}")

            logger.info(f"[PARALLEL] Классификатор: score={classifier_score}, blacklisted={classification.get('blacklisted')}")

            # OUTGOING CHECK — письма от нас самих (ответы сотрудников на рекламации) пропускаем
            # Используем sender_addr (чистый email) вместо sender (display name)
            sender_addr_lower = (email_data.get('sender_addr', '') or '').lower()
            our_email = EMAIL_CONFIG.get('user', '').lower()
            # OUR_EMAIL_DOMAINS — домены компании через запятую (автоопределяется из EMAIL_USER)
            _domains_env = os.getenv('OUR_EMAIL_DOMAINS', '')
            our_domains = [d.strip() for d in _domains_env.split(',') if d.strip()]
            if our_email and '@' in our_email:
                _auto_domain = '@' + our_email.split('@')[1]
                if _auto_domain not in our_domains:
                    our_domains.append(_auto_domain)
            is_from_our_domain = any(sender_addr_lower.endswith(d) for d in our_domains)
            # ALLOWED_SENDERS — адреса сотрудников, чьи письма анализируются LLaMA (через запятую)
            _allowed_env = os.getenv('ALLOWED_SENDERS', '')
            allowed_senders = [s.strip() for s in _allowed_env.split(',') if s.strip()]
            is_outgoing = (sender_addr_lower == our_email or
                           (is_from_our_domain and sender_addr_lower not in allowed_senders))
            if is_outgoing:
                reasoning = f"Исходящее письмо от {email_data.get('sender', '')} — ответ, не рекламация."
                logger.info(f"[PARALLEL] [OUTGOING] Email {email_id}: {reasoning}")
                log_entries.append(f"[{time.time() - start_time:.1f}s] Исходящее: {sender_addr_lower} -> пропуск")
                try:
                    import database as db_mod
                    db_mod.save_email(
                        email_id=str(email_id), subject=email_data.get('subject', ''),
                        sender=email_data.get('sender', ''), received_date=email_data.get('received_date', ''),
                        is_reclamation=False, is_blacklisted=False,
                        category='Исходящее',
                        processing_time=time.time() - start_time,
                        run_date=email_data.get('received_date', ''),
                        body_text=email_data.get('body', ''),
                        processing_log='\n'.join(log_entries)
                    )
                except Exception:
                    pass
                return {
                    "success": True, "email_id": email_id,
                    "subject": email_data['subject'], "sender": email_data['sender'],
                    "received_date": email_data['received_date'],
                    "attachments": attachments, "is_reclamation": False,
                    "reasoning": reasoning, "decision_path": "outgoing",
                    "category": "Исходящее", "subcategories": [],
                    "recipients": [], "copy_to": [],
                    "attachments_count": len(attachments),
                    "llama_analysis": None, "details": {},
                    "processing_time": time.time() - start_time
                }

            # BLACKLIST CHECK — быстрый выход для бухгалтерии/счетов
            if classification.get('blacklisted'):
                blacklist_keyword = classification.get('blacklist_keyword', 'unknown')
                reasoning = f"Blacklist: тема содержит '{blacklist_keyword}'. Бухгалтерский документ."
                logger.info(f"[PARALLEL] [BLACKLIST] Email {email_id}: {reasoning}")

                log_entries.append(f"[{time.time() - start_time:.1f}s] Blacklist: '{blacklist_keyword}' -> пропуск")

                # Записываем blacklisted письмо в БД
                try:
                    import database as db_mod
                    att_meta = [{'filename': a.get('filename', ''), 'size': a.get('size', 0),
                                 'content_type': a.get('content_type', '')} for a in attachments]
                    db_mod.save_email(
                        email_id=str(email_id), subject=email_data.get('subject', ''),
                        sender=email_data.get('sender', ''), received_date=email_data.get('received_date', ''),
                        is_reclamation=False, is_blacklisted=True,
                        category=classification.get('category', ''),
                        processing_time=time.time() - start_time,
                        run_date=email_data.get('received_date', ''),
                        body_text=email_data.get('body', ''),
                        attachments_json=json.dumps(att_meta, ensure_ascii=False),
                        processing_log='\n'.join(log_entries)
                    )
                except Exception:
                    pass

                return {
                    "success": True,
                    "email_id": email_id,
                    "subject": email_data['subject'],
                    "sender": email_data['sender'],
                    "received_date": email_data['received_date'],
                    "attachments": attachments,
                    "is_reclamation": False,
                    "reasoning": reasoning,
                    "decision_path": "blacklist",
                    "category": classification.get('category'),
                    "subcategories": classification.get('subcategories', []),
                    "recipients": [],
                    "copy_to": [],
                    "attachments_count": len(attachments),
                    "llama_analysis": None,
                    "details": {},
                    "processing_time": time.time() - start_time
                }

            # NOTE: Раньше здесь был "if score < 50: skip LLaMA"
            # Убрано в рефакторинге 2026-02-13: LLaMA решает ВСЕГДА (если не blacklist)
            # Score теперь используется только для определения категории

            # === PHASE 1: PARALLEL OCR ===
            ocr_start = time.time()
            logger.info(f"[PARALLEL] Запуск параллельного OCR для {len(attachments)} вложений")

            # Подготавливаем данные для OCR
            ocr_input = []
            for att in attachments:
                ocr_input.append({
                    'path': att.get('filepath', ''),
                    'filename': att.get('filename', ''),
                    'content_type': att.get('content_type', '')
                })

            # Параллельный OCR
            ocr_results = process_attachments_parallel(ocr_input)

            # Объединяем OCR результаты с вложениями
            attachments = merge_ocr_results_to_attachments(attachments, ocr_results)
            email_data['attachments'] = attachments

            ocr_time = time.time() - ocr_start
            ocr_ok = sum(1 for a in attachments if a.get('extracted_text', '').strip())
            log_entries.append(f"[{time.time() - start_time:.1f}s] OCR: {len(attachments)} файлов, {ocr_ok} с текстом ({ocr_time:.1f}s)")
            logger.info(f"[PARALLEL] OCR завершён за {ocr_time:.1f}s")

            # === PHASE 2: PARALLEL LLaMA ANALYSIS ===
            llama_start = time.time()

            # Подготавливаем документы для LLaMA
            documents = []
            for att in attachments:
                # Используем OCR текст если есть, иначе extracted_text
                text = att.get('extracted_text', '')
                ocr_text = att.get('extracted_text', '')  # OCR уже добавил текст сюда

                if text and len(text.strip()) > 10:
                    documents.append({
                        'filename': att.get('filename', 'unknown'),
                        'text': text,
                        'content_type': att.get('content_type', 'unknown')
                    })

            # Также добавляем тело письма как "документ"
            email_body = email_data.get('body', '')
            if email_body and len(email_body.strip()) > 50:
                documents.append({
                    'filename': '_email_body.txt',
                    'text': email_body,
                    'content_type': 'text/plain'
                })

            logger.info(f"[PARALLEL] Запуск параллельного LLaMA для {len(documents)} документов")

            # Контекст письма для промптов
            email_context = {
                'subject': email_data.get('subject', ''),
                'sender': email_data.get('sender', ''),
                'date': email_data.get('received_date', '')
            }

            # Проверяем доступность LLaMA
            if not self.check_llama_availability():
                logger.warning("[PARALLEL] LLaMA недоступен, используем только классификатор")
                analyses = []
            else:
                # Параллельный анализ документов
                analyses = self.llama_analyzer.analyze_documents_parallel(documents, email_context)

            llama_time = time.time() - llama_start
            recl_docs = sum(1 for a in analyses if a.is_reclamation_related)
            log_entries.append(f"[{time.time() - start_time:.1f}s] LLaMA: {len(analyses)} документов, {recl_docs} рекламационных ({llama_time:.1f}s)")
            logger.info(f"[PARALLEL] LLaMA завершён за {llama_time:.1f}s ({len(analyses)} документов)")

            # === PHASE 3: INTELLIGENT MERGE ===
            merged = self.llama_analyzer.merge_document_analyses(analyses, classification)

            # Определяем итоговый результат
            is_reclamation = merged.get('is_reclamation', False)

            # LLaMA = source of truth (НЕТ override от классификатора!)
            # is_reclamation уже определён в merge

            # Рекламация ВСЕГДА должна иметь хотя бы один продукт
            if is_reclamation and not merged.get('products'):
                logger.warning(f"[PARALLEL] 0 продуктов -> override is_reclamation=False")
                is_reclamation = False
                merged['is_reclamation'] = False

            # Определяем категорию
            category = merged.get('category', classification.get('category', 'Неизвестно'))
            if category == 'Неизвестно' and classification.get('category'):
                category = classification.get('category')

            n_prod = len(merged.get('products', []))
            conf = merged.get('confidence', 0)
            log_entries.append(f"[{time.time() - start_time:.1f}s] Merge: is_recl={is_reclamation}, {n_prod} продуктов, категория={category}, confidence={conf}")

            # Формируем результат
            result = {
                "success": True,
                "email_id": email_id,
                "subject": email_data['subject'],
                "sender": email_data['sender'],
                "body": email_data.get('body', ''),  # Сырой текст письма для Bitrix
                "received_date": email_data['received_date'],
                "attachments": attachments,
                "is_reclamation": is_reclamation,
                "category": category,
                "subcategories": classification.get('subcategories', []),
                "recipients": classification.get('recipients', []),
                "copy_to": classification.get('copy_to', []),
                "attachments_count": len(attachments),
                "llama_analysis": merged,
                "details": merged,  # Для совместимости
                "processing_time": time.time() - start_time,
                "parallel_stats": {
                    "ocr_time": ocr_time,
                    "llama_time": llama_time,
                    "documents_analyzed": len(analyses),
                    "products_found": len(merged.get('products', []))
                }
            }

            # === PHASE 4: BITRIX ===
            # B25: 1 email = 1 элемент Bitrix (product-поля уже конкатенированы в merge)
            if is_reclamation and not skip_bitrix and self.bitrix_connector:
                try:
                    element_id = self.bitrix_connector.process_reclamation(result)
                    if element_id:
                        result["bitrix24_id"] = element_id
                        products_count = len(merged.get('products', []))
                        logger.info(f"[PARALLEL] Bitrix ID={element_id} ({products_count} продуктов в 1 записи)")
                except Exception as e:
                    logger.error(f"[PARALLEL] Ошибка Bitrix: {e}")
                    result["bitrix_error"] = str(e)
                    log_entries.append(f"[{time.time() - start_time:.1f}s] Bitrix: ошибка - {e}")

            total_time = time.time() - start_time
            result["processing_time"] = total_time

            # Финальная запись в лог
            if not is_reclamation:
                log_entries.append(f"[{total_time:.1f}s] Итог: не рекламация")
            elif result.get('bitrix24_id'):
                bid = result.get('bitrix24_id')
                log_entries.append(f"[{total_time:.1f}s] Bitrix: запись {bid} создана")

            processing_log = '\n'.join(log_entries)

            # === PHASE 5: SAVE TO DB ===
            try:
                import database as db
                run_date = email_data.get('received_date', '')
                # Метаданные вложений (без extracted_text — он большой)
                att_meta = [{'filename': a.get('filename', ''), 'size': a.get('size', 0),
                             'content_type': a.get('content_type', ''),
                             'filepath': a.get('filepath', '')} for a in attachments]
                db.save_email(
                    email_id=email_id,
                    subject=email_data.get('subject', ''),
                    sender=email_data.get('sender', ''),
                    received_date=run_date,
                    is_reclamation=is_reclamation,
                    is_blacklisted=False,
                    category=category,
                    processing_time=total_time,
                    run_date=run_date,
                    body_text=email_data.get('body', ''),
                    attachments_json=json.dumps(att_meta, ensure_ascii=False),
                    llama_result_json=json.dumps(merged, ensure_ascii=False) if merged else None,
                    cloud_links=result.get('cloud_links', ''),
                    processing_log=processing_log
                )

                if is_reclamation:
                    # B25: Сохраняем рекламацию(и) в БД — 1 bitrix_id для всех продуктов
                    products = merged.get('products', [])
                    bitrix_id = result.get('bitrix24_id')

                    if products:
                        for product in products:
                            db.save_reclamation(
                                email_id=str(email_id),
                                bitrix_id=bitrix_id,
                                product_name=product.get('name', 'n/a'),
                                serial_number=product.get('serial_number', 'n/a'),
                                category=category,
                                severity=merged.get('severity', 'n/a'),
                                customer_name=merged.get('customer_name', 'n/a'),
                                issue_description=product.get('issue', 'n/a'),
                                dealer_name=merged.get('dealer_name'),
                                contact_person=merged.get('contact_person'),
                                act_number=merged.get('act_number'),
                                products_json=json.dumps(products, ensure_ascii=False)
                            )
                    else:
                        db.save_reclamation(
                            email_id=str(email_id),
                            bitrix_id=bitrix_id,
                            product_name=merged.get('product_name', 'n/a'),
                            serial_number=merged.get('serial_number', 'n/a'),
                            category=category,
                            severity=merged.get('severity', 'n/a'),
                            customer_name=merged.get('customer_name', 'n/a'),
                            issue_description=merged.get('issue_description', 'n/a'),
                            dealer_name=merged.get('dealer_name'),
                            contact_person=merged.get('contact_person'),
                            act_number=merged.get('act_number')
                        )
            except Exception as e:
                logger.warning(f"[PARALLEL] Ошибка записи в БД (не критично): {e}")

            logger.info(f"[PARALLEL] Завершено за {total_time:.1f}s: is_recl={is_reclamation}, products={len(merged.get('products', []))}")
            return result

        except Exception as e:
            logger.error(f"[PARALLEL] Ошибка при обработке письма {email_id}: {e}")
            import traceback
            logger.error(f"[PARALLEL] Трассировка: {traceback.format_exc()}")
            return {"success": False, "error": str(e), "processing_time": time.time() - start_time}

    def format_email_body(email_data, result, details):
        """
        Форматирует тело письма с рекламацией для пересылки
        
        Args:
            email_data: Данные исходного письма
            result: Результат классификации и анализа
            details: Детали рекламации
            
        Returns:
            Отформатированный текст сообщения
        """
        # Получаем данные из LLaMA анализа
        llama_analysis = result.get('llama_analysis', {})
        product_name = llama_analysis.get('product_name', 'Не определен')
        issue_description = llama_analysis.get('issue_description', 'Не указано')
        severity = llama_analysis.get('severity', 'Не определена')
        customer_name = llama_analysis.get('customer_name', 'Не указан')
        
        # Форматируем подробное описание рекламации
        details_text = ""
        for key, value in details.items():
            if value and value != 'n/a':
                # Преобразуем snake_case в читаемый текст
                readable_key = key.replace('_', ' ').capitalize()
                details_text += f"{readable_key}: {value}\n"
        
        # Форматируем тело письма с полным анализом
        body = f"""
            АВТОМАТИЧЕСКИ ОБРАБОТАННАЯ РЕКЛАМАЦИЯ
            ===========================================

            ОСНОВНАЯ ИНФОРМАЦИЯ:
            -------------------
            Оригинальное письмо от: {email_data['sender']}
            Получено: {email_data['received_date']}
            Категория рекламации: {result.get('category', 'Не определена')}

            АНАЛИЗ РЕКЛАМАЦИИ:
            -----------------
            Продукт: {product_name}
            Описание проблемы: {issue_description}
            Серьезность: {severity}
            Клиент: {customer_name}

            ДЕТАЛИ РЕКЛАМАЦИИ:
            -----------------
            {details_text}

            ОРИГИНАЛЬНЫЙ ТЕКСТ ПИСЬМА:
            ------------------------
            {email_data['body']}

            ===========================================
            Количество вложений: {len(email_data['attachments'])}
        """
        
        return body
    

    def process_multiple_reclamations(self, email_id: str, email_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Обрабатывает письмо с возможностью множественных рекламаций
        
        Args:
            email_id: ID письма
            email_data: Данные письма
            
        Returns:
            Список результатов обработки (может содержать несколько рекламаций)
        """
        try:
            logger.info(f"Начинаем обработку возможных множественных рекламаций для {email_id}")

            # ВАЖНО: Сначала вызываем с skip_bitrix=True, чтобы не создавать дубль
            # Если будет один продукт - отправим в Bitrix ниже
            # Если несколько продуктов - отправим каждый отдельно
            result = self.process_email(email_id, skip_bitrix=True)

            # Если это не рекламация, просто возвращаем результат
            if not result.get('is_reclamation', False):
                logger.info(f"Письмо {email_id} не является рекламацией, пропускаем проверку множественных рекламаций")
                return [result]

            # Получаем вложения
            attachments = email_data.get('attachments', [])

            # Создаем экземпляр MultiReclamationProcessor
            multi_processor = MultiReclamationProcessor()

            # Проверяем, есть ли несколько продуктов в рекламации
            llama_result = result.get('llama_analysis', {})

            # Если llama_analysis содержит ошибку, пропускаем проверку на несколько продуктов
            # и отправляем один результат в Bitrix
            if 'error' in llama_result:
                logger.warning(f"Пропуск проверки множественных рекламаций из-за ошибки LLaMA: {llama_result.get('error')}")
                # Отправляем в Bitrix как одну рекламацию
                if self.bitrix_connector:
                    try:
                        element_id = self.bitrix_connector.process_reclamation(result)
                        if element_id:
                            logger.info(f"Рекламация отправлена в Битрикс24: ID={element_id}")
                            result["bitrix24_id"] = element_id
                    except Exception as e:
                        logger.error(f"Ошибка при отправке в Битрикс24: {e}")
                return [result]

            logger.info(f"Проверяем наличие нескольких продуктов в рекламации {email_id}")
            products = multi_processor.detect_multiple_products(email_data, attachments, llama_result)

            # Если найдено несколько продуктов, разделяем рекламацию
            if products and len(products) > 1:
                logger.info(f"Письмо {email_id} содержит несколько продуктов: {products}")
                split_results = multi_processor.split_reclamation_by_products(email_data, attachments, result, products)
                
                logger.info(f"Рекламация разделена на {len(split_results)} отдельных рекламаций по продуктам")
                
                # ВАЖНО: Пересылаем каждую разделенную рекламацию отдельно
                for idx, split_result in enumerate(split_results):
                    logger.info(f"Обработка разделенной рекламации {idx+1}/{len(split_results)} для продукта {split_result.get('product', 'неизвестный')}")
                    # TODO: ВРЕМЕННО ОТКЛЮЧЕНО - исправляем повторную обработку
                    # forward_success = self.forward_reclamation(email_data, split_result)
                    # split_result["forwarded"] = forward_success
                    forward_success = False  # Временно отключено
                    split_result["forwarded"] = forward_success
                    logger.info(f"Пересылка временно отключена для продукта {split_result.get('product', 'неизвестный')}")

                    # Добавляем ваш блок здесь
                    if self.bitrix_connector:
                        try:
                            element_id = self.bitrix_connector.process_reclamation(split_result)
                            if element_id:
                                logger.info(f"Рекламация отправлена в Битрикс24: ID={element_id}")
                                split_result["bitrix24_id"] = element_id
                        except Exception as e:
                            logger.error(f"Ошибка при отправке в Битрикс24: {e}")

                    # Логирование статуса пересылки (временно отключено)
                    if forward_success:
                        logger.info(f"Разделенная рекламация для продукта {split_result.get('product', 'неизвестный')} успешно переслана")
                    else:
                        logger.debug(f"Пересылка отключена для продукта {split_result.get('product', 'неизвестный')}")
                
                return split_results

            # Если один продукт (или продукты не определены) - отправляем как одну рекламацию
            logger.info(f"Письмо {email_id} содержит одну рекламацию, отправляем в Bitrix")
            if self.bitrix_connector:
                try:
                    element_id = self.bitrix_connector.process_reclamation(result)
                    if element_id:
                        logger.info(f"Рекламация отправлена в Битрикс24: ID={element_id}")
                        result["bitrix24_id"] = element_id
                except Exception as e:
                    logger.error(f"Ошибка при отправке в Битрикс24: {e}")
            return [result]
            
        except Exception as e:
            logger.error(f"Ошибка при обработке множественных рекламаций: {e}")
            import traceback
            logger.error(f"Трассировка ошибки: {traceback.format_exc()}")
            
            # В случае ошибки возвращаем базовый результат
            return [{
                "success": False,
                "email_id": email_id,
                "error": f"Ошибка обработки множественных рекламаций: {str(e)}",
                "is_reclamation": False
            }]

    # Изменения для метода forward_reclamation

    def forward_reclamation(self, email_data: Dict[str, Any], result: Dict[str, Any]) -> bool:
        """
        Пересылает рекламацию на тестовые адреса с подробной информацией
        
        Args:
            email_data: Данные письма
            result: Результат обработки
            
        Returns:
            Успешность пересылки
        """
        try:
            # ДОБАВЛЕНО: Отладочная информация перед пересылкой
            logger.info(f"Начинаем пересылку рекламации: {result.get('category', 'неизвестная категория')}")
            
            # Проверка пароля SMTP
            if SMTP_CONFIG['password'] is None:
                logger.error("SMTP пароль не установлен в переменных окружения")
                return False
            
            # Используем тестовые адреса из .env
            recipients = [os.getenv('DEFAULT_RECIPIENT', TEST_RECIPIENTS['default'])]
            copy_to = [os.getenv('COPY_RECIPIENT', TEST_RECIPIENTS['copy'])]
            
            # ДОБАВЛЕНО: Проверка адресов получателей
            if not recipients[0] or recipients[0] == 'None':
                logger.error("Не указан основной получатель в .env или тестовых данных")
                recipients = [r for r in [os.getenv('DEFAULT_RECIPIENT', '')] if r]
                
            logger.info(f"Пересылка рекламации на {recipients}, копия {copy_to}")
            
            # Создаем новое письмо
            msg = MIMEMultipart()
            msg['From'] = SMTP_CONFIG['user']
            msg['To'] = ', '.join(recipients)
            msg['Cc'] = ', '.join(copy_to)
            
            # Формируем тему с категорией рекламации и информацией об отправителе
            category = result.get('category', 'Неопределенная категория')
            subcategories = result.get('subcategories', [])
            
            # Добавляем подкатегории в тему, если они есть
            subcategory_text = f" ({', '.join(subcategories)})" if subcategories else ""
            
            # Добавляем информацию об отправителе в тему
            original_sender = email_data.get('sender', 'Неизвестный отправитель')
            
            # Если это часть множественной рекламации, добавляем информацию о продукте
            if result.get('is_part_of_multiple', False):
                product_info = f" - Продукт: {result.get('product', 'Неизвестный')}"
            else:
                product_info = ""
            
            msg['Subject'] = f"Рекламация: {email_data['subject']} - {category}{subcategory_text}{product_info} - от: {original_sender}"
            
            # Получаем данные из LLaMA анализа
            llama_analysis = result.get('llama_analysis', {})
            product_name = llama_analysis.get('product_name', 'Не определен')
            issue_description = llama_analysis.get('issue_description', 'Не указано')
            severity = llama_analysis.get('severity', 'Не определена')
            customer_name = llama_analysis.get('customer_name', 'Не указан')
            
            # Получаем детали
            details = result.get('details', {})
            
            # Форматируем подробное описание рекламации
            details_text = ""
            for key, value in details.items():
                if value and value != 'n/a':
                    # Преобразуем snake_case в читаемый текст
                    readable_key = key.replace('_', ' ').capitalize()
                    
                    # Преобразуем словари в читаемый формат
                    if isinstance(value, dict):
                        formatted_value = "\n  ".join([f"{k}: {v}" for k, v in value.items()])
                        details_text += f"{readable_key}:\n  {formatted_value}\n"
                    else:
                        details_text += f"{readable_key}: {value}\n"
            
            # Добавляем проверку, чтобы recipients и copy_to всегда были списками:
            recipients = result.get('recipients', ['Не определены'])
            if not isinstance(recipients, list):
                recipients = ['Не определены']

            copy_to = result.get('copy_to', ['Не определены'])
            if not isinstance(copy_to, list):
                copy_to = ['Не определены']

            # Формируем тело письма с полным анализом и информацией о получателях
            body = f"""
    АВТОМАТИЧЕСКИ ОБРАБОТАННАЯ РЕКЛАМАЦИЯ
    ===========================================

    ОСНОВНАЯ ИНФОРМАЦИЯ:
    -------------------
    Оригинальное письмо от: {email_data['sender']}
    Получено: {email_data['received_date']}
    Категория рекламации: {category}{subcategory_text}

    АНАЛИЗ РЕКЛАМАЦИИ:
    -----------------
    Продукт: {product_name}
    Описание проблемы: {issue_description}
    Серьезность: {severity}
    Клиент: {customer_name}

    ДЕТАЛИ РЕКЛАМАЦИИ:
    -----------------
    {details_text}

    ИНФОРМАЦИЯ О ПОЛУЧАТЕЛЯХ:
    -----------------------
    Согласно распределению рекламаций, данное письмо должно быть отправлено:
    Получатели: {', '.join(recipients)}
    Копия: {', '.join(copy_to)}

    ОРИГИНАЛЬНЫЙ ТЕКСТ ПИСЬМА:
    ------------------------
    {email_data['body']}

    ===========================================
    Количество вложений: {len(email_data['attachments'])}
    """
            
            msg.attach(MIMEText(body, 'plain'))
            
            # Прикрепляем вложения с правильной обработкой имен файлов
            logger.info(f"Прикрепление {len(email_data['attachments'])} вложений")
            for attachment in email_data['attachments']:
                try:
                    filepath = attachment.get('filepath')
                    filename = attachment.get('filename')
                    
                    if not filepath or not os.path.exists(filepath):
                        logger.warning(f"Вложение не найдено: {filepath}")
                        continue
                    
                    with open(filepath, 'rb') as file:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(file.read())
                        encoders.encode_base64(part)
                        
                        # Правильное формирование заголовка Content-Disposition с именем файла
                        # Обработка кириллических имен файлов согласно RFC2231
                        from email.header import Header
                        filename_header = Header(filename).encode()
                        part.add_header(
                            'Content-Disposition',
                            'attachment',
                            filename=filename
                        )
                        
                        # Добавляем тип содержимого
                        content_type = attachment.get('content_type', 'application/octet-stream')
                        part.add_header('Content-Type', content_type)
                        
                        msg.attach(part)
                        logger.info(f"Вложение добавлено: {filename}, тип: {content_type}")
                except Exception as e:
                    logger.error(f"Ошибка при прикреплении вложения {attachment.get('filename', 'неизвестно')}: {e}")
            
            # ДОБАВЛЕНО: Проверяем настройки SMTP перед отправкой
            logger.info(f"Проверка настроек SMTP: Хост={SMTP_CONFIG['host']}, Порт={SMTP_CONFIG['port']}, Пользователь={SMTP_CONFIG['user']}")
            
            # Отправляем письмо
            logger.info("Подключение к SMTP-серверу...")
            try:
                # ИЗМЕНЕНО: Добавляем таймауты и проверку SSL/TLS
                with smtplib.SMTP_SSL(SMTP_CONFIG['host'], SMTP_CONFIG['port'], timeout=30) as server:
                    logger.info(f"Логин на SMTP как {SMTP_CONFIG['user']}")
                    server.login(SMTP_CONFIG['user'], SMTP_CONFIG['password'])
                    logger.info("Логин успешен, отправка сообщения...")
                    all_recipients = recipients + copy_to
                    server.send_message(msg)
                    logger.info("Сообщение отправлено успешно")
                
                logger.info(f"Рекламация успешно переслана на {recipients} и {copy_to}")
                return True
            except Exception as smtp_error:
                logger.error(f"Ошибка SMTP при отправке сообщения: {smtp_error}")
                import traceback
                logger.error(f"Трассировка SMTP: {traceback.format_exc()}")
                
                # ДОБАВЛЕНО: Альтернативный метод отправки при ошибке
                try:
                    logger.info("Пробуем альтернативный метод отправки...")
                    with smtplib.SMTP(SMTP_CONFIG['host'], 587, timeout=30) as server:
                        server.starttls()
                        server.login(SMTP_CONFIG['user'], SMTP_CONFIG['password'])
                        server.send_message(msg)
                        logger.info("Сообщение отправлено успешно (альтернативный метод)")
                        return True
                except Exception as alt_error:
                    logger.error(f"Альтернативный метод отправки также не удался: {alt_error}")
                    return False
        
        except Exception as e:
            logger.error(f"Ошибка при пересылке рекламации: {e}")
            import traceback
            logger.error(f"Трассировка: {traceback.format_exc()}")
            return False

    
    def run(self, date_str: str = None) -> Dict[str, Any]:
        """
        Запускает поиск и обработку рекламаций
        
        Args:
            date_str: Дата в формате IMAP (если None, определяется автоматически)
            
        Returns:
            Результаты обработки
        """
        if date_str is None:
            # Определяем дату автоматически на основе настроек
            date_str = get_date_for_imap()
            logger.info(f"Дата для поиска писем: {date_str}")
        
        results = {
            "date": date_str,
            "total_emails": 0,
            "processed": 0,
            "reclamations_found": 0,
            "forwarded": 0,
            "errors": 0,
            "details": []
        }

        # Сохраняем данные писем для пересылки
        self.email_cache = {}

        # Записываем начало запуска в БД
        run_id = None
        try:
            import database as db_run
            run_id = db_run.start_run(date_str)
        except Exception:
            pass

        run_start_time = time.time()

        try:
            # Подключаемся к почтовому серверу
            if not self.connect():
                return {
                    "success": False, 
                    "error": "Не удалось подключиться к почтовому серверу",
                    "date": date_str,
                    "total_emails": 0,
                    "processed": 0,
                    "reclamations_found": 0,
                    "forwarded": 0,
                    "errors": 1,
                    "details": []
                }
            
            # Ищем письма за указанную дату
            email_ids = self.find_emails_by_date(date_str)
            results["total_emails"] = len(email_ids)
            
            if not email_ids:
                logger.info(f"Писем за {date_str} не найдено")
                return results
            
            # Закрываем соединение после поиска писем
            self.disconnect()
            logger.info(f"Найдено {len(email_ids)} писем, будем обрабатывать по одному")
            
            # Очищаем старые записи кэша (старше 30 дней)
            self._cleanup_old_cache_entries(days=30)

            # Счётчик пропущенных (уже обработанных) писем
            skipped_count = 0

            # Обрабатываем каждое письмо отдельно с новым подключением
            for i, email_id in enumerate(email_ids):
                try:
                    # ПРОВЕРКА: Пропускаем уже обработанные письма
                    if self._is_email_processed(email_id):
                        logger.info(f"Письмо {email_id} уже обработано ранее, пропускаем")
                        skipped_count += 1
                        continue

                    # Пауза перед обработкой следующего письма
                    if i > 0:
                        time.sleep(5)  # 5 секунд между письмами

                    logger.info(f"Обработка письма #{i+1}/{len(email_ids)} (ID: {email_id})")

                    # Подключаемся заново для каждого письма
                    if not self.connect():
                        logger.error(f"Не удалось подключиться для письма {email_id}")
                        results["errors"] += 1
                        continue
                        
                    # Загружаем письмо
                    logger.info(f"Загрузка письма {email_id}")
                    email_data = self.fetch_email(email_id)
                    if not email_data:
                        logger.error(f"Не удалось загрузить письмо {email_id}")
                        results["errors"] += 1
                        self.disconnect()  # Обязательно отключаемся
                        continue
                    
                    # Скачиваем вложения
                    logger.info(f"Скачивание вложений для письма {email_id}")
                    try:
                        attachments = self.download_attachments(email_data)
                        email_data['attachments'] = attachments
                    except Exception as e:
                        logger.error(f"Ошибка при скачивании вложений: {e}")
                        results["errors"] += 1
                        self.disconnect()  # Обязательно отключаемся
                        continue
                    
                    # Сохраняем в кеш
                    self.email_cache[email_id] = email_data
                    
                    # Обрабатываем письмо
                    logger.info(f"Анализ и классификация письма {email_id}")
                    try:
                        # v2.0: Выбор режима обработки
                        if PARALLEL_MODE:
                            # Новая архитектура: параллельный OCR + per-document LLaMA
                            logger.info(f"[PARALLEL_MODE] Используем параллельную обработку для {email_id}")
                            result = self.process_email_parallel(email_id)
                            reclamation_results = [result]
                        elif not self.check_llama_availability():
                            logger.warning(f"LLaMA API недоступен, обрабатываем письмо {email_id} без AI-анализа")
                            # Классифицируем без LLaMA
                            classifier_result = self.classifier.classify_reclamation(email_data, email_data['attachments'])
                            reclamation_results = [{
                                "success": True,
                                "email_id": email_id,
                                "subject": email_data['subject'],
                                "is_reclamation": classifier_result.get('is_reclamation', False),
                                "category": classifier_result.get('category'),
                                "recipients": classifier_result.get('recipients', []),
                                "copy_to": classifier_result.get('copy_to', []),
                                "attachments_count": len(email_data['attachments']),
                                "llama_analysis": {"is_reclamation": classifier_result.get('is_reclamation', False), "error": "LLaMA API недоступен"},
                                "details": {}
                            }]
                        else:
                            # Старая архитектура
                            reclamation_results = self.process_multiple_reclamations(email_id, email_data)
                        
                        if not reclamation_results:
                            logger.warning(f"Пустой результат обработки для письма {email_id}")
                            reclamation_results = [{
                                "success": False,
                                "email_id": email_id,
                                "error": "Пустой результат обработки",
                                "is_reclamation": False
                            }]
                        
                        results["processed"] += 1
                        
                        # Обрабатываем результаты
                        reclamations_detected = 0
                        reclamations_forwarded = 0
                        
                        for reclamation_result in reclamation_results:
                            if reclamation_result.get("is_reclamation", False):
                                reclamations_detected += 1
                                if reclamation_result.get("forwarded", False):
                                    reclamations_forwarded += 1
                            
                            # Добавляем результат в детали
                            results["details"].append(reclamation_result)
                        
                        results["reclamations_found"] += reclamations_detected
                        results["forwarded"] += reclamations_forwarded
                        
                        logger.info(f"Обработано письмо: {email_id}, найдено рекламаций: {reclamations_detected}, переслано: {reclamations_forwarded}")

                        # СОХРАНЯЕМ email_id как обработанный
                        first_result = reclamation_results[0] if reclamation_results else {}
                        self._mark_email_processed(email_id, {
                            "subject": first_result.get("subject", ""),
                            "is_reclamation": reclamations_detected > 0,
                            "category": first_result.get("category", "")
                        })
                        # ВАЖНО: Сохраняем кэш СРАЗУ после обработки каждого письма
                        self._save_processed_cache()

                    except Exception as e:
                        logger.error(f"Ошибка при обработке рекламаций: {e}")
                        import traceback
                        logger.error(f"Трассировка ошибки: {traceback.format_exc()}")
                        results["errors"] += 1
                    
                    # Обязательно отключаемся после каждого письма
                    self.disconnect()
                    
                except Exception as e:
                    logger.error(f"Ошибка при обработке письма {email_id}: {e}")
                    import traceback
                    logger.error(f"Трассировка: {traceback.format_exc()}")
                    results["errors"] += 1
                    results["details"].append({
                        "email_id": email_id,
                        "success": False,
                        "error": str(e)
                    })
                    
                    # В случае ошибки также отключаемся
                    try:
                        self.disconnect()
                    except:
                        pass
            
            # Сохраняем кэш обработанных писем
            self._save_processed_cache()

            # Добавляем статистику пропущенных в результаты
            results["skipped"] = skipped_count

            logger.info(f"Итоги обработки: всего {results['total_emails']} писем, пропущено {skipped_count} (уже обработаны), обработано {results['processed']}, найдено {results['reclamations_found']} рекламаций, переслано {results['forwarded']}")

            # Записываем завершение запуска в БД
            if run_id:
                try:
                    import database as db_run
                    db_run.finish_run(
                        run_id,
                        total_emails=results['total_emails'],
                        reclamations_found=results['reclamations_found'],
                        blacklisted=0,
                        errors=results['errors'],
                        skipped=skipped_count,
                        duration=time.time() - run_start_time
                    )
                except Exception:
                    pass

            return results

        except Exception as e:
            logger.error(f"Ошибка при выполнении обработки: {e}")
            import traceback
            logger.error(f"Трассировка: {traceback.format_exc()}")
            # Записываем ошибку запуска в БД
            if run_id:
                try:
                    import database as db_run
                    db_run.fail_run(run_id, str(e))
                except Exception:
                    pass
            return {"success": False, "error": str(e)}


def main():
    """Основная функция для запуска обработчика"""
    logger.info("===== Запуск улучшенной системы обработки рекламаций =====")
    
    # Проверка подключения к почтовому серверу перед запуском обработки
    def test_email_connection():
        try:
            logger.info("Тестирование подключения к почтовому серверу...")
            test_processor = EmailProcessor()
            conn_result = test_processor.connect()
            if conn_result:
                logger.info("Тест подключения к почтовому серверу успешен!")
                test_processor.disconnect()
                return True
            else:
                logger.error("Тест подключения к почтовому серверу не пройден!")
                return False
        except Exception as e:
            logger.error(f"Ошибка при тестировании подключения: {e}")
            return False
    
    # Проверка подключения к SMTP перед запуском обработки
    def test_smtp_connection():
        try:
            logger.info("Тестирование подключения к SMTP серверу...")
            if SMTP_CONFIG['password'] is None:
                logger.error("SMTP пароль не установлен в переменных окружения")
                return False
                
            import smtplib
            try:
                with smtplib.SMTP_SSL(SMTP_CONFIG['host'], SMTP_CONFIG['port'], timeout=10) as server:
                    server.login(SMTP_CONFIG['user'], SMTP_CONFIG['password'])
                    logger.info("Тест подключения к SMTP серверу успешен!")
                    return True
            except Exception as e:
                logger.error(f"Ошибка подключения к SMTP (SSL): {e}")
                try:
                    with smtplib.SMTP(SMTP_CONFIG['host'], 587, timeout=10) as server:
                        server.starttls()
                        server.login(SMTP_CONFIG['user'], SMTP_CONFIG['password'])
                        logger.info("Тест подключения к SMTP серверу через STARTTLS успешен!")
                        return True
                except Exception as e2:
                    logger.error(f"Ошибка подключения к SMTP (STARTTLS): {e2}")
                    return False
        except Exception as e:
            logger.error(f"Ошибка при тестировании SMTP: {e}")
            return False

    # Новая функция для непрерывного мониторинга писем с определенным интервалом
    def continuous_monitoring(interval_minutes=5):
        """
        Непрерывный мониторинг новых писем с заданным интервалом
        
        Args:
            interval_minutes: Интервал проверки в минутах
        """
        import time
        
        logger.info(f"Запуск непрерывного мониторинга новых писем. Интервал проверки: {interval_minutes} минут")
        
        processor = EmailProcessor()
        
        # Бесконечный цикл для постоянного мониторинга
        while True:
            try:
                logger.info(f"===== Начало проверки новых писем =====")
                # Используем текущую дату
                date_str = None  # Автоматически возьмет текущую дату из get_date_for_imap()
                
                # Проверяем подключение перед каждой итерацией
                email_conn_ok = test_email_connection()
                if not email_conn_ok:
                    logger.warning("Подключение к почтовому серверу не удалось, пропускаем итерацию...")
                    time.sleep(60)  # Ожидаем 1 минуту перед следующей попыткой
                    continue
                
                # Запускаем обработку
                results = processor.run(date_str)
                
                # Выводим результаты
                logger.info(f"Результаты обработки:")
                logger.info(f"Всего писем: {results['total_emails']}")
                logger.info(f"Обработано: {results['processed']}")
                logger.info(f"Найдено рекламаций: {results['reclamations_found']}")
                logger.info(f"Переслано: {results['forwarded']}")
                logger.info(f"Ошибок: {results['errors']}")
                
                # Сохраняем результаты в JSON
                import json
                from pathlib import Path
                results_file = Path('processing_results.json')
                with open(results_file, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
                
                logger.info(f"Результаты сохранены в {results_file}")
                
                # Если есть коннектор Битрикс, также обрабатываем результаты
                if hasattr(processor, 'bitrix_connector') and processor.bitrix_connector:
                    try:
                        processor.bitrix_connector.process_latest_results()
                    except Exception as bitrix_error:
                        logger.error(f"Ошибка при отправке в Битрикс24: {bitrix_error}")
                
                # Ожидаем до следующей проверки
                logger.info(f"===== Проверка завершена. Следующая проверка через {interval_minutes} минут =====")
                time.sleep(interval_minutes * 60)
                
            except KeyboardInterrupt:
                logger.info("Мониторинг остановлен пользователем")
                break
            except Exception as e:
                logger.error(f"Ошибка при выполнении итерации мониторинга: {e}")
                import traceback
                logger.error(f"Трассировка: {traceback.format_exc()}")
                # Ожидаем немного меньше перед следующей попыткой после ошибки
                logger.info(f"Повторная попытка через 2 минуты...")
                time.sleep(120)
    
    try:
        # Вывести настройки для отладки
        logger.info("Настройки:")
        logger.info(f"IMAP: {EMAIL_CONFIG['host']}, пользователь: {EMAIL_CONFIG['user']}")
        logger.info(f"SMTP: {SMTP_CONFIG['host']}:{SMTP_CONFIG['port']}, пользователь: {SMTP_CONFIG['user']}")
        logger.info(f"Режим работы: {os.getenv('TEST_MODE', 'email')}")
        logger.info(f"Тестовая дата: {os.getenv('TEST_DATE', 'не указана')}")
        logger.info(f"Адреса пересылки: {TEST_RECIPIENTS}")
        
        # Тестирование подключений
        email_conn_ok = test_email_connection()
        smtp_conn_ok = test_smtp_connection()
        
        if not email_conn_ok:
            logger.warning("Подключение к почтовому серверу не удалось, но продолжаем работу...")
            
        if not smtp_conn_ok:
            logger.warning("Подключение к SMTP серверу не удалось, пересылка рекламаций может не работать!")
        
        # Запускаем обработку
        test_mode = os.getenv('TEST_MODE', 'email').lower()
        
        if test_mode == 'demo':
            # В демо-режиме запускаем интеграционный модуль
            from integration_module import run_demo_test
            logger.info("Запуск в демо-режиме")
            run_demo_test()
        elif test_mode == 'monitor':
            # Новый режим - непрерывный мониторинг
            # Берем интервал проверки из переменной окружения или используем значение по умолчанию
            interval = int(os.getenv('MONITOR_INTERVAL', '5'))
            continuous_monitoring(interval)
        else:
            # В обычном режиме однократно обрабатываем письма
            date_str = os.getenv('TEST_DATE', None)
            logger.info(f"Запуск однократной обработки писем за дату: {date_str if date_str else 'текущая дата'}")
            
            # Создаем процессор
            processor = EmailProcessor()
            results = processor.run(date_str)
            
            # Выводим результаты
            logger.info(f"Результаты обработки:")
            logger.info(f"Всего писем: {results['total_emails']}")
            logger.info(f"Обработано: {results['processed']}")
            logger.info(f"Найдено рекламаций: {results['reclamations_found']}")
            logger.info(f"Переслано: {results['forwarded']}")
            logger.info(f"Ошибок: {results['errors']}")
            
            # Сохраняем результаты в JSON
            import json
            from pathlib import Path
            results_file = Path('processing_results.json')
            with open(results_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2, default=str)
            
            logger.info(f"Результаты сохранены в {results_file}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        import traceback
        logger.error(f"Трассировка: {traceback.format_exc()}")
    
    logger.info("===== Завершение работы системы =====")

if __name__ == "__main__":
    main()