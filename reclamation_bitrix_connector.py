"""
Модуль для интеграции системы обработки рекламаций с Битрикс24

Этот модуль связывает вашу систему обработки рекламаций с Битрикс24,
автоматически добавляя новые рекламации в универсальный список.
"""
import os
import time
import logging
import json
import base64
import traceback
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from pathlib import Path
from datetime import datetime
import random

# Импортируем интеграцию с Битрикс24
from bitrix24_integration import Bitrix24Integration

# Настройка логирования
logger = logging.getLogger("ReclamationBitrixConnector")

# Абсолютный путь к директории проекта (для маппинга и кэша)
_PROJECT_DIR = Path(__file__).parent

class ReclamationBitrixConnector:
    """Класс для соединения системы рекламаций с Битрикс24"""

    def __init__(self, webhook_url=None, results_file=None):
        """
        Инициализирует коннектор

        Args:
            webhook_url: URL вебхука Битрикс24 (если None, берется из переменной окружения)
            results_file: Путь к файлу с результатами обработки рекламаций (если None, используется стандартный)
        """
        # Настройка Битрикс24
        self.bitrix = Bitrix24Integration(webhook_url)

        # Путь к файлу с результатами
        self.results_file = results_file or Path('processing_results.json')

        # Абсолютные пути к файлам данных
        self.field_mapping_file = _PROJECT_DIR / 'bitrix_field_mapping.json'

        # Автосинхронизация полей при старте
        self.sync_field_mapping()

    def sync_field_mapping(self):
        """
        Синхронизирует маппинг полей с Bitrix24 API.
        Запрашивает все поля списка 100 и обновляет bitrix_field_mapping.json.
        """
        try:
            logger.info("[SYNC] Начинаю синхронизацию полей с Bitrix24...")

            res = self.bitrix._make_request('lists.field.get', {
                'IBLOCK_TYPE_ID': 'bitrix_processes',
                'IBLOCK_ID': 100
            })

            fields = res.get('result', {})
            if not fields:
                logger.warning("[SYNC] Bitrix вернул пустой список полей, пропускаю синхронизацию")
                return

            # Загружаем текущий маппинг
            current_mapping = {}
            if self.field_mapping_file.exists():
                try:
                    with open(self.field_mapping_file, 'r', encoding='utf-8') as f:
                        current_mapping = json.load(f)
                except (json.JSONDecodeError, IOError):
                    logger.warning("[SYNC] Не удалось прочитать текущий маппинг, создаю новый")

            # Собираем все поля из Bitrix API
            api_fields = {}
            if isinstance(fields, dict):
                for field_id, field_data in fields.items():
                    if isinstance(field_data, dict):
                        name = field_data.get('NAME', '')
                        if name:
                            api_fields[name] = field_id

            # Находим новые поля (есть в API, нет в маппинге)
            current_ids = set(current_mapping.values())
            new_fields = {}
            for name, fid in api_fields.items():
                if fid not in current_ids:
                    new_fields[name] = fid

            if new_fields:
                logger.info(f"[SYNC] Найдено {len(new_fields)} новых полей:")
                for name, fid in new_fields.items():
                    logger.info(f"  + {name}: {fid}")
                    current_mapping[name] = fid

                # Сохраняем обновлённый маппинг
                with open(self.field_mapping_file, 'w', encoding='utf-8') as f:
                    json.dump(current_mapping, f, ensure_ascii=False, indent=2)
                logger.info(f"[SYNC] Маппинг обновлён: {len(current_mapping)} полей")
            else:
                logger.info(f"[SYNC] Маппинг актуален ({len(current_mapping)} полей)")

            # Сохраняем ID поля "Файлы рекламации (не облако)" для быстрого доступа
            self.file_field_id = None
            for name, fid in current_mapping.items():
                if 'файлы рекламации' in name.lower() and 'облако' not in name.lower():
                    self.file_field_id = fid
                    logger.info(f"[SYNC] Поле файлов рекламации: {fid} ({name})")
                    break
            if not self.file_field_id:
                # Пробуем поиск в API fields
                for name, fid in api_fields.items():
                    if 'файлы рекламации' in name.lower():
                        self.file_field_id = fid
                        logger.info(f"[SYNC] Поле файлов из API: {fid} ({name})")
                        break

        except Exception as e:
            logger.warning(f"[SYNC] Ошибка синхронизации полей (не критично): {e}")
            self.file_field_id = None


    def process_reclamation(self, reclamation_data: Dict[str, Any]) -> Optional[int]:
        try:
            # Проверяем наличие данных
            if not reclamation_data or not isinstance(reclamation_data, dict):
                logger.error("Некорректные данные рекламации")
                return None

            # B28: Дедупликация через SQLite (единый источник правды)
            email_id = reclamation_data.get('email_id')
            subject = reclamation_data.get('subject', '')

            try:
                import database as db
                existing = db.get_reclamations(limit=1)
                # Проверяем есть ли уже рекламация с этим email_id и bitrix_id
                conn = db.get_connection()
                cursor = conn.execute(
                    "SELECT bitrix_id FROM reclamations WHERE email_id = ? AND bitrix_id IS NOT NULL LIMIT 1",
                    (str(email_id),)
                )
                row = cursor.fetchone()
                if row:
                    logger.info(f"Рекламация для email {email_id} уже в Bitrix (ID={row[0]}), пропускаем")
                    return None
            except Exception as e:
                logger.warning(f"Не удалось проверить дедупликацию через БД: {e}")

            # Жестко указываем ID списка (перенесён в бизнес-процессы)
            IBLOCK_ID = 100
            IBLOCK_TYPE_ID = 'bitrix_processes'

            # Генерация уникального кода элемента
            timestamp = int(time.time())
            random_suffix = ''.join(str(random.randint(0, 9)) for _ in range(6))
            element_code = f"RECL_{reclamation_data.get('email_id', '')}_{timestamp}_{random_suffix}"

            # Подготовка базовых данных с уникальным именем
            original_name = reclamation_data.get('subject', 'Новая рекламация')[:200]
            unique_name = f"{original_name} [{timestamp}_{random_suffix}]"

            bitrix_data = {
                'IBLOCK_TYPE_ID': IBLOCK_TYPE_ID,
                'IBLOCK_ID': IBLOCK_ID,
                'ELEMENT_CODE': element_code,
                'FIELDS': {
                    'NAME': unique_name,
                    'PROPERTY_1000': str(reclamation_data.get('email_id', timestamp))  # id рекламации = email_id (уникальный)
                }
            }

            # Загрузка маппинга полей (абсолютный путь)
            try:
                with open(self.field_mapping_file, 'r', encoding='utf-8') as f:
                    bitrix_field_mapping = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                logger.error(f"Ошибка при загрузке маппинга полей: {self.field_mapping_file}")
                bitrix_field_mapping = {}

            # Преобразование email-списков
            if reclamation_data.get('recipients'):
                fid = bitrix_field_mapping.get('Сотрудники в ответе(письма)', 'PROPERTY_1018')
                bitrix_data['FIELDS'][fid] = ', '.join(str(e) for e in reclamation_data['recipients'] if e)
            if reclamation_data.get('copy_to'):
                fid = bitrix_field_mapping.get('Сотрудники в копии(письма)', 'PROPERTY_1020')
                bitrix_data['FIELDS'][fid] = ', '.join(str(e) for e in reclamation_data['copy_to'] if e)

            # Вспомогательная функция
            def safe_get(data, *keys, default='n/a'):
                for key in keys:
                    try:
                        data = data.get(key, {})
                    except AttributeError:
                        return default
                return data if data else default

            # =====================================================
            # МАППИНГ КАТЕГОРИЙ
            # =====================================================
            # Входные данные: str | None
            #   - "Рекламации Метро", "Метро", "метро", "", None
            # Выходные данные:
            #   - PROPERTY_1014 (текст): str ("Метро", "Наземка", ...)
            #   - PROPERTY_1012 (список): int (1200, 1202, 1204, 1206)
            # =====================================================

            def map_category(raw_category) -> dict:
                """
                Маппит категорию на значения Bitrix.

                Args:
                    raw_category: Любой тип (str, None, list, int, ...)

                Returns:
                    {'text': str | None, 'list_id': int | None, 'error': str | None}

                Сценарии:
                    "Метро"           -> {text: "Метро", list_id: 1202, error: None}
                    "МЕТРО"           -> {text: "Метро", list_id: 1202, error: None}
                    "Рекламации Метро"-> {text: "Метро", list_id: 1202, error: None}
                    "жд транспорт"    -> {text: "ЖДТ", list_id: 1204, error: None}
                    "n/a"             -> {text: None, list_id: None, error: "n/a value"}
                    None              -> {text: None, list_id: None, error: "is None"}
                    ["Метро"]         -> извлекает первый элемент
                    123               -> {text: None, list_id: None, error: "not str"}
                """
                # Маппинг: ключ (lowercase) -> ID значения списка в Bitrix
                CATEGORY_TO_LIST_ID = {
                    # Основные
                    'наземка': 1200,
                    'метро': 1202,
                    'ждт': 1204,
                    'спецтехника': 1206,
                    # Алиасы
                    'жд': 1204,
                    'железнодорожный': 1204,
                    'железнодорожный транспорт': 1204,
                    'жд транспорт': 1204,
                    'наземный': 1200,
                    'наземный транспорт': 1200,
                    'специальная техника': 1206,
                    'спец': 1206,
                    'метрополитен': 1202,
                }

                # Каноничные названия для текстового поля
                CATEGORY_CANONICAL = {
                    'наземка': 'Наземка',
                    'метро': 'Метро',
                    'ждт': 'ЖДТ',
                    'спецтехника': 'Спецтехника',
                    'жд': 'ЖДТ',
                    'железнодорожный': 'ЖДТ',
                    'железнодорожный транспорт': 'ЖДТ',
                    'жд транспорт': 'ЖДТ',
                    'наземный': 'Наземка',
                    'наземный транспорт': 'Наземка',
                    'специальная техника': 'Спецтехника',
                    'спец': 'Спецтехника',
                    'метрополитен': 'Метро',
                }

                # Невалидные значения (LLaMA часто возвращает)
                INVALID_VALUES = {'n/a', 'na', 'н/д', 'неизвестно', 'unknown', 'none', 'null', '-', ''}

                result = {'text': None, 'list_id': None, 'error': None}

                try:
                    # 1. None check
                    if raw_category is None:
                        result['error'] = 'is None'
                        return result

                    # 2. List -> извлекаем первый элемент
                    if isinstance(raw_category, (list, tuple)):
                        if len(raw_category) > 0:
                            raw_category = raw_category[0]
                            logger.debug(f"Категория была списком, взят первый элемент: {raw_category}")
                        else:
                            result['error'] = 'empty list'
                            return result

                    # 3. Не строка -> ошибка
                    if not isinstance(raw_category, str):
                        result['error'] = f'not str: {type(raw_category).__name__}'
                        return result

                    # 4. Нормализация
                    cleaned = raw_category.strip()

                    # 5. Проверка на невалидные значения
                    if cleaned.lower() in INVALID_VALUES:
                        result['error'] = f'invalid value: {cleaned}'
                        return result

                    # 6. Убираем префиксы (с учётом регистра)
                    lower = cleaned.lower()
                    for prefix in ['рекламации ', 'рекламация ', 'категория ', 'category ']:
                        if lower.startswith(prefix):
                            cleaned = cleaned[len(prefix):].strip()
                            lower = cleaned.lower()
                            break

                    # 7. Пустая строка после очистки
                    if not cleaned:
                        result['error'] = 'empty after cleanup'
                        return result

                    # 8. Поиск в маппинге
                    key = lower
                    if key in CATEGORY_TO_LIST_ID:
                        result['text'] = CATEGORY_CANONICAL[key]
                        result['list_id'] = CATEGORY_TO_LIST_ID[key]
                    else:
                        # Попробуем partial match
                        found = False
                        for map_key in CATEGORY_TO_LIST_ID:
                            if map_key in key or key in map_key:
                                result['text'] = CATEGORY_CANONICAL[map_key]
                                result['list_id'] = CATEGORY_TO_LIST_ID[map_key]
                                found = True
                                logger.debug(f"Категория '{cleaned}' -> partial match -> '{map_key}'")
                                break

                        if not found:
                            # Категория не найдена
                            result['text'] = cleaned  # Сохраняем как текст
                            result['error'] = f'unknown: {cleaned}'
                            logger.warning(f"Неизвестная категория: '{cleaned}'")

                    return result

                except Exception as e:
                    result['error'] = f'exception: {str(e)}'
                    logger.error(f"Ошибка маппинга категории '{raw_category}': {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    return result

            # =====================================================
            # ПРИОРИТЕТ ИСТОЧНИКОВ КАТЕГОРИИ:
            # 1. LLaMA reclamation_category (понимает контекст)
            # 2. Классификатор category (keywords fallback)
            # =====================================================

            # Пробуем LLaMA сначала
            llama_cat = None
            llama_analysis = reclamation_data.get('llama_analysis', {})
            if isinstance(llama_analysis, dict):
                llama_cat = llama_analysis.get('reclamation_category')

            cat_result = map_category(llama_cat)

            # Если LLaMA не дал валидную категорию -> fallback на классификатор
            if not cat_result['list_id']:
                classifier_cat = reclamation_data.get('category')
                cat_result_fallback = map_category(classifier_cat)

                if cat_result_fallback['list_id']:
                    logger.info(f"Категория: LLaMA='{llama_cat}' не валидна, fallback на классификатор='{classifier_cat}'")
                    cat_result = cat_result_fallback
                else:
                    logger.warning(f"Категория не определена: LLaMA='{llama_cat}', классификатор='{classifier_cat}'")

            if cat_result['text']:
                # Текстовое поле (PROPERTY_1014)
                cat_fid = bitrix_field_mapping.get('Категория', 'PROPERTY_1014')
                bitrix_data['FIELDS'][cat_fid] = cat_result['text']

            if cat_result['list_id']:
                # Поле-список (PROPERTY_1012) - int!
                bitrix_data['FIELDS']['PROPERTY_1012'] = cat_result['list_id']
                logger.info(f"Категория: {cat_result['text']} -> PROPERTY_1012={cat_result['list_id']}")
            elif cat_result['error']:
                logger.warning(f"Категория не установлена в список: {cat_result['error']}")

            # Парсинг даты получения
            def parse_date(date_str):
                """Преобразует дату в единый формат YYYY-MM-DD"""
                if not date_str or date_str == 'n/a':
                    return 'n/a'
                date_str = str(date_str)
                import re
                # Формат: "12-Feb-2026" или "12 Feb 2026"
                match = re.search(r'(\d{1,2})[-\s](\w{3})[-\s](\d{4})', date_str)
                if match:
                    day, month, year = match.groups()
                    months = {'Jan':'01','Feb':'02','Mar':'03','Apr':'04','May':'05','Jun':'06',
                              'Jul':'07','Aug':'08','Sep':'09','Oct':'10','Nov':'11','Dec':'12'}
                    if month in months:
                        return f"{year}-{months[month]}-{day.zfill(2)}"
                # Формат: "2026-02-12" - уже правильный
                match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
                if match:
                    return match.group(0)
                # Формат: "12.02.2026" - конвертируем
                match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', date_str)
                if match:
                    day, month, year = match.groups()
                    return f"{year}-{month}-{day}"
                # Вернуть как есть, обрезав до 10 символов
                return date_str[:10] if len(date_str) > 10 else date_str

            received_date = reclamation_data.get('received_date') or reclamation_data.get('date') or 'n/a'

            # Формируем остальные поля через маппинг
            field_mapping = {
                'Тема письма': reclamation_data.get('subject', 'n/a'),
                'Отправитель': reclamation_data.get('sender', 'n/a'),
                'Дата получения': parse_date(received_date),
                'Статус рекламации': 'Получена',
                'Подкатегории': ', '.join(reclamation_data.get('subcategories', []) or []),
                'Название продукта': safe_get(reclamation_data, 'llama_analysis', 'product_name'),
                'Описание проблемы': safe_get(reclamation_data, 'llama_analysis', 'issue_description'),
                'Серьезность': safe_get(reclamation_data, 'llama_analysis', 'severity', default='Средняя'),
                'Название организации': safe_get(reclamation_data, 'llama_analysis', 'customer_name'),
                'Код продукта': safe_get(reclamation_data, 'details', 'product_code'),
                'Модель': safe_get(reclamation_data, 'details', 'model_number'),
                'Серийный номер': safe_get(reclamation_data, 'details', 'serial_number'),
                'Дата производства': safe_get(reclamation_data, 'details', 'purchase_date'),
                'Дата ввода в эксплуатацию': safe_get(reclamation_data, 'details', 'issue_date'),
                'Дата возникновения': safe_get(reclamation_data, 'details', 'issue_date'),
                'Контактное лицо': safe_get(reclamation_data, 'details', 'contact_person'),
                'Телефон': safe_get(reclamation_data, 'details', 'phone_number'),
                'Адрес': safe_get(reclamation_data, 'details', 'store_location'),
                'Номер договора/счета': safe_get(reclamation_data, 'details', 'invoice_number'),
                'Путь к документам (облако)': reclamation_data.get('_precomputed_cloud_links') or self._upload_attachments_to_cloud(reclamation_data),
                'Дилер': safe_get(reclamation_data, 'details', 'dealer_name'),
                'Адрес возврата': safe_get(reclamation_data, 'details', 'return_address'),
                'Номер отслеживания': safe_get(reclamation_data, 'details', 'tracking_number'),
                'История обработки': f"Автоматически добавлено {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                # Новые поля (2026-02-13)
                'Сырой текст email': reclamation_data.get('body') or reclamation_data.get('email_body') or '',
                'Номер рекламационного акта': safe_get(reclamation_data, 'llama_analysis', 'act_number') or safe_get(reclamation_data, 'details', 'act_number') or '',
            }

            for fname, val in field_mapping.items():
                if val and fname in bitrix_field_mapping:
                    try:
                        bitrix_data['FIELDS'][bitrix_field_mapping[fname]] = str(val)
                    except Exception as e:
                        logger.warning(f"Поле {fname} не записано: {e}")

            # Загрузка файлов в поле "Файлы рекламации (не облако)"
            if self.file_field_id and reclamation_data.get('attachments'):
                try:
                    file_data_list = self._prepare_file_attachments(reclamation_data['attachments'])
                    if file_data_list:
                        # Формат для Bitrix Lists: {"n0": [name, b64], "n1": [name, b64]}
                        formatted = {}
                        for i, fd in enumerate(file_data_list):
                            formatted[f"n{i}"] = fd  # fd = [filename, base64content]
                        bitrix_data['FIELDS'][self.file_field_id] = formatted
                        logger.info(f"Подготовлено {len(file_data_list)} файлов для {self.file_field_id}")
                except Exception as e:
                    logger.warning(f"Ошибка подготовки файлов: {e}")

            # Пытаемся создать элемент с retry
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    logger.info(f"Попытка {attempt+1}/{max_attempts} создания элемента")
                    res = self.bitrix._make_request('lists.element.add', bitrix_data)
                    if res.get('result'):
                        logger.info(f"Рекламация добавлена, ID={res['result']}")
                        return res['result']
                    else:
                        logger.error(f"Неожиданный ответ Bitrix: {res}")
                        break
                except Exception as e:
                    error_msg = str(e)
                    if 'ERROR_ELEMENT_ALREADY_EXISTS' in error_msg:
                        # Перегенерировать ELEMENT_CODE
                        timestamp = int(time.time())
                        random_suffix = ''.join(str(random.randint(0, 9)) for _ in range(8))
                        bitrix_data['ELEMENT_CODE'] = f"RECL_NEW_{timestamp}_{random_suffix}"
                        logger.warning(f"Элемент уже существует, retry с новым кодом (попытка {attempt+1})")
                        time.sleep(1)
                    else:
                        logger.error(f"Ошибка Bitrix при попытке {attempt+1}: {error_msg}")
                        if attempt < max_attempts - 1:
                            time.sleep(2)
                        else:
                            return None

            logger.error("Не удалось создать элемент после нескольких попыток")
            return None

        except Exception as global_error:
            logger.error(f"Глобальная ошибка: {global_error}")
            logger.error(traceback.format_exc())
            return None

    
    @staticmethod
    def _sanitize_filename_keep_ext(filename: str, content_type: str = '') -> str:
        """Санитизирует имя файла, гарантируя сохранение расширения."""
        import mimetypes
        import re as _re

        _, ext = os.path.splitext(filename)
        # Если расширения нет или оно слишком длинное — определяем по MIME
        if not ext or len(ext) > 10:
            ext = mimetypes.guess_extension(content_type) or '.bin'
            name_part = filename
        else:
            name_part = filename[:len(filename) - len(ext)]

        # Санитизируем имя но СОХРАНЯЕМ расширение
        name_part = _re.sub(r'[^\w\s._-]', '_', name_part, flags=_re.UNICODE).strip()[:80]
        if not name_part:
            name_part = 'file'
        return name_part + ext

    def _prepare_file_attachments(self, attachments: list) -> list:
        """
        Подготавливает вложения для загрузки в поле типа File в Bitrix24.

        Args:
            attachments: Список вложений [{'filepath': str, 'filename': str}, ...]

        Returns:
            Список dict с fileData для Bitrix API
        """
        files = []
        max_file_size = 30 * 1024 * 1024  # 30MB лимит Bitrix

        for att in attachments:
            filepath = att.get('filepath', '')
            if not filepath or not os.path.exists(filepath):
                continue
            file_size = os.path.getsize(filepath)
            if file_size > max_file_size:
                logger.warning(f"Файл {filepath} слишком большой ({file_size / 1024 / 1024:.1f}MB > 30MB), пропускаем")
                continue
            if file_size == 0:
                continue
            try:
                with open(filepath, 'rb') as f:
                    content = base64.b64encode(f.read()).decode('ascii')
                filename = att.get('filename') or os.path.basename(filepath)
                filename = self._sanitize_filename_keep_ext(filename, att.get('content_type', ''))
                files.append([filename, content])
                logger.info(f"Подготовлен файл для Bitrix: {filename} ({file_size / 1024:.0f}KB)")
            except Exception as e:
                logger.error(f"Ошибка чтения файла {filepath}: {e}")

        return files


    def _upload_attachments_to_cloud(self, reclamation_data: Dict[str, Any]) -> str:
        """
        Загрузить вложения в облако Bitrix24 и вернуть ссылки.

        Args:
            reclamation_data: Данные о рекламации с вложениями

        Returns:
            Строка со ссылками на файлы (разделённые переносом строки)
        """
        attachments = reclamation_data.get('attachments', [])
        if not attachments:
            return ''

        try:
            # Получаем данные для создания папки
            email_id = str(reclamation_data.get('email_id', 'unknown'))
            subject = reclamation_data.get('subject', 'Без темы')
            date = datetime.now().strftime('%Y-%m-%d')

            # Создать подпапку для рекламации
            folder_id = self.bitrix.create_reclamation_subfolder(email_id, subject, date)
            if not folder_id:
                logger.warning("Не удалось создать папку в облаке, используем локальные пути")
                return ', '.join(att.get('filepath', '') for att in attachments if att.get('filepath'))

            # Загрузить файлы и собрать ссылки (HTML-формат для кликабельности)
            html_links = []
            for att in attachments:
                filepath = att.get('filepath')
                filename = att.get('filename', os.path.basename(filepath) if filepath else 'файл')
                # Гарантируем расширение
                filename = self._sanitize_filename_keep_ext(filename, att.get('content_type', ''))
                if filepath and os.path.exists(filepath):
                    try:
                        # Загружаем файл
                        file_data = self.bitrix.upload_file_to_disk(folder_id, filepath)
                        if file_data and file_data.get('ID'):
                            # Получаем публичную ссылку
                            link = self.bitrix.get_file_external_link(file_data['ID'])
                            if link:
                                # Форматируем как HTML-ссылку
                                html_links.append(f'<a href="{link}" target="_blank">{filename}</a>')
                                logger.info(f"Файл {filename} загружен в облако: {link}")
                            else:
                                # Если не удалось получить ссылку, используем DETAIL_URL
                                detail_url = file_data.get('DETAIL_URL', '')
                                if detail_url:
                                    html_links.append(f'<a href="{detail_url}" target="_blank">{filename}</a>')
                                    logger.info(f"Файл {filename} загружен, DETAIL_URL: {detail_url}")
                    except Exception as e:
                        logger.error(f"Ошибка загрузки файла {filepath}: {e}")
                else:
                    if filepath:
                        logger.warning(f"Файл не найден: {filepath}")

            if html_links:
                # Возвращаем HTML со списком ссылок
                return '<br>'.join(html_links)
            else:
                # Fallback на локальные пути если ничего не загрузилось
                return ', '.join(att.get('filepath', '') for att in attachments if att.get('filepath'))

        except Exception as e:
            logger.error(f"Ошибка загрузки вложений в облако: {e}")
            # Fallback на локальные пути
            return ', '.join(att.get('filepath', '') for att in attachments if att.get('filepath'))

    
    def process_latest_results(self) -> int:
        """
        Обрабатывает последние результаты работы системы обработки рекламаций
        
        Returns:
            Количество обработанных рекламаций
        """
        # Проверяем существование файла с результатами
        if not self.results_file.exists():
            logger.warning(f"Файл с результатами обработки не найден: {self.results_file}")
            return 0
        
        try:
            # Загружаем результаты
            with open(self.results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)
            
            if not results or 'details' not in results:
                logger.warning("Файл с результатами не содержит данных о рекламациях")
                return 0
            
            # Убеждаемся, что список рекламаций существует в Битрикс24
            if not self.bitrix.find_reclamation_list():
                if not self.bitrix.create_reclamation_list():
                    logger.error("Не удалось найти или создать список рекламаций в Битрикс24")
                    return 0
            
            # Настраиваем поля списка
            self.bitrix.setup_reclamation_fields()
            
            # Обрабатываем каждую рекламацию
            processed_count = 0
            reclamation_details = results.get('details', [])
            
            for reclamation in reclamation_details:
                # Проверяем, является ли запись рекламацией
                if not reclamation.get('is_reclamation', False):
                    continue
                
                # Проверяем, обрабатывали ли мы уже эту рекламацию (через БД)
                reclamation_id = reclamation.get('email_id')
                try:
                    import database as db
                    conn = db.get_connection()
                    cursor = conn.execute(
                        "SELECT bitrix_id FROM reclamations WHERE email_id = ? AND bitrix_id IS NOT NULL LIMIT 1",
                        (str(reclamation_id),)
                    )
                    if cursor.fetchone():
                        logger.info(f"Рекламация {reclamation_id} уже была обработана ранее")
                        continue
                except Exception:
                    pass
                
                # Преобразуем данные рекламации в формат для Битрикс24
                bitrix_data = self._convert_reclamation_to_bitrix_format(reclamation)
                
                # Отправляем данные в Битрикс24
                element_id = self.bitrix.process_reclamation(bitrix_data)
                
                if element_id:
                    logger.info(f"Рекламация успешно добавлена/обновлена в Битрикс24: {reclamation_id}")
                    processed_count += 1
                else:
                    logger.error(f"Не удалось добавить рекламацию в Битрикс24: {reclamation_id}")
            
            logger.info(f"Обработано {processed_count} новых рекламаций")
            return processed_count
            
        except Exception as e:
            logger.error(f"Ошибка при обработке результатов: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return 0
    
    def _convert_reclamation_to_bitrix_format(self, reclamation_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Преобразует данные рекламации в формат для Битрикс24
        
        Args:
            reclamation_data: Данные о рекламации из системы обработки
            
        Returns:
            Данные в формате для Битрикс24
        """
        # Загружаем маппинг полей из файла
        field_mapping = {}
        mapping_file = Path('bitrix_field_mapping.json')
        if mapping_file.exists():
            try:
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    field_mapping = json.load(f)
                logger.info(f"Загружен маппинг полей из {mapping_file}")
            except Exception as e:
                logger.error(f"Ошибка при загрузке маппинга полей: {e}")
                
        # Если файла нет, получаем маппинг полей через API
        if not field_mapping:
            try:
                fields_result = self.bitrix._make_request('lists.field.get', {
                    'IBLOCK_TYPE_ID': self.bitrix.iblock_type_id,
                    'IBLOCK_ID': self.bitrix.iblock_id
                })
                
                fields = fields_result.get('result', {})
                
                # Обрабатываем поля как словарь или как список
                if isinstance(fields, dict):
                    for field_id, field_data in fields.items():
                        if isinstance(field_data, dict):
                            field_name = field_data.get('NAME', '')
                            field_type = field_data.get('TYPE', '')
                            if field_type == 'PROPERTY':
                                property_id = field_id.split('_')[-1] if '_' in field_id else field_id
                                if property_id and field_name:
                                    field_mapping[field_name] = f"PROPERTY_{property_id}"
                            else:
                                if field_id and field_name:
                                    field_mapping[field_name] = field_id
                elif isinstance(fields, list):
                    for field in fields:
                        if isinstance(field, dict):
                            field_id = field.get('FIELD_ID', '')
                            field_name = field.get('NAME', '')
                            field_type = field.get('TYPE', '')
                            
                            if field_type == 'PROPERTY':
                                property_id = field_id.split('_')[-1] if field_id else None
                                if property_id and field_name:
                                    field_mapping[field_name] = f"PROPERTY_{property_id}"
                            else:
                                if field_id and field_name:
                                    field_mapping[field_name] = field_id
                
                logger.info(f"Получен маппинг полей через API: {len(field_mapping)} из 28 полей")
                if len(field_mapping) < 28:
                    logger.warning(f"Обнаружено неполное количество полей: {len(field_mapping)} из 28")
                    logger.debug(f"Найденные поля: {list(field_mapping.keys())}")
            except Exception as e:
                logger.error(f"Ошибка при получении полей списка: {e}")
                # В случае ошибки используем пустой маппинг
                field_mapping = {}
        
        # Формируем базовый набор данных для Битрикс24
        bitrix_data = {}
        
        # Маппинг ключей из данных рекламации на поля Битрикс24
        field_keys = {
            'subject': 'Тема письма',
            'sender': 'Отправитель',
            'received_date': 'Дата получения',
            'category': 'Категория',
            'attachments_count': 'Количество вложений'
        }
        
        # Добавляем базовые поля
        for key, field_name in field_keys.items():
            if key in reclamation_data and field_name in field_mapping:
                value = reclamation_data.get(key)
                # Обрабатываем специальные случаи
                if key == 'category' and value and isinstance(value, str):
                    # Вырезаем префикс "Рекламации " из категории, если он есть
                    if value.startswith('Рекламации '):
                        value = value[11:]
                # Преобразуем дату в строку, если это объект datetime
                if key == 'received_date' and hasattr(value, 'isoformat'):
                    value = value.isoformat()
                
                bitrix_data[field_mapping[field_name]] = str(value)
        
        # Устанавливаем название элемента (обязательное поле)
        bitrix_data['NAME'] = reclamation_data.get('subject', 'Новая рекламация')
        
        # Устанавливаем статус рекламации
        if 'Статус рекламации' in field_mapping:
            bitrix_data[field_mapping['Статус рекламации']] = 'Получена'
        
        # Добавляем подкатегории, если они есть
        subcategories = reclamation_data.get('subcategories', [])
        if subcategories and 'Подкатегории' in field_mapping:
            bitrix_data[field_mapping['Подкатегории']] = ', '.join(subcategories)
        
        # Добавляем информацию о сотрудниках в ответе и копии
        recipients = reclamation_data.get('recipients', [])
        if recipients and 'Сотрудники в ответе' in field_mapping:
            bitrix_data[field_mapping['Сотрудники в ответе']] = ', '.join(recipients)
        
        copy_to = reclamation_data.get('copy_to', [])
        if copy_to and 'Сотрудники в копии' in field_mapping:
            bitrix_data[field_mapping['Сотрудники в копии']] = ', '.join(copy_to)
        
        # Добавляем информацию о продукте из результатов анализа LLaMA
        llama_analysis = reclamation_data.get('llama_analysis', {})
        if llama_analysis:
            field_mapping_llama = {
                'product_name': 'Название продукта',
                'issue_description': 'Описание проблемы',
                'severity': 'Серьезность',
                'customer_name': 'Название организации'
            }
            
            for key, field_name in field_mapping_llama.items():
                if key in llama_analysis and field_name in field_mapping:
                    value = llama_analysis.get(key)
                    if value and value != 'n/a':
                        bitrix_data[field_mapping[field_name]] = str(value)
        
        # Добавляем детальную информацию о рекламации, если она есть
        details = reclamation_data.get('details', {})
        if details:
            field_mapping_details = {
                'product_code': 'Код продукта',
                'model_number': 'Модель',
                'serial_number': 'Серийный номер',
                'purchase_date': 'Дата производства',
                'issue_date': 'Дата возникновения',
                'customer_id': 'Название организации',
                'store_location': 'Адрес',
                'warranty_status': 'Статус гарантии',
                'dealer_name': 'Дилер',
                'contact_person': 'Контактное лицо',
                'phone_number': 'Телефон',
                'return_address': 'Адрес возврата',
                'tracking_number': 'Номер отслеживания',
                'invoice_number': 'Номер договора/счета'
            }
            
            for key, field_name in field_mapping_details.items():
                if key in details and field_name in field_mapping:
                    value = details.get(key)
                    if value and value != 'n/a':
                        bitrix_data[field_mapping[field_name]] = str(value)
        
        # Добавляем пути к вложениям, если они есть
        if 'attachments' in reclamation_data and 'Путь к документам(вложения)' in field_mapping:
            attachments = reclamation_data.get('attachments', [])
            if attachments:
                attachment_paths = [att.get('filepath', '') for att in attachments if 'filepath' in att]
                if attachment_paths:
                    bitrix_data[field_mapping['Путь к документам(вложения)']] = '\n'.join(attachment_paths)

        # Дополнительно: история обработки
        if 'История обработки' in field_mapping:
            bitrix_data[field_mapping['История обработки']] = f"Автоматически добавлено в Битрикс24 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        
        logger.info(f"Подготовлены данные для Битрикс24: {len(bitrix_data)} полей")
        return bitrix_data
    
    def setup_monitoring(self, interval_minutes: int = 60) -> None:
        """
        Настраивает постоянный мониторинг результатов обработки рекламаций
        
        Args:
            interval_minutes: Интервал проверки в минутах
        """
        logger.info(f"Запуск мониторинга результатов с интервалом {interval_minutes} минут")
        
        while True:
            try:
                # Обрабатываем последние результаты
                processed_count = self.process_latest_results()
                logger.info(f"Обработано {processed_count} новых рекламаций")
                
                # Ждем до следующей проверки
                logger.info(f"Следующая проверка через {interval_minutes} минут")
                time.sleep(interval_minutes * 60)
            
            except KeyboardInterrupt:
                logger.info("Мониторинг остановлен пользователем")
                break
            
            except Exception as e:
                logger.error(f"Ошибка во время мониторинга: {e}")
                # Ждем немного и продолжаем
                time.sleep(60)


def main():
    """Основная функция для запуска интеграции с Битрикс24"""
    import argparse
    
    # Настройка логирования для main
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("bitrix_connector.log"),
            logging.StreamHandler()
        ]
    )
    
    # Парсинг аргументов командной строки
    parser = argparse.ArgumentParser(description='Интеграция системы обработки рекламаций с Битрикс24')
    parser.add_argument('--webhook', help='URL вебхука Битрикс24')
    parser.add_argument('--results', help='Путь к файлу с результатами обработки рекламаций')
    parser.add_argument('--interval', type=int, default=60, help='Интервал проверки в минутах')
    parser.add_argument('--oneshot', action='store_true', help='Запустить однократную обработку')
    
    args = parser.parse_args()
    
    try:
        # Создаем коннектор
        connector = ReclamationBitrixConnector(
            webhook_url=args.webhook,
            results_file=Path(args.results) if args.results else None
        )
        
        if args.oneshot:
            # Запускаем однократную обработку
            logger.info("Запуск однократной обработки результатов")
            processed_count = connector.process_latest_results()
            logger.info(f"Обработано {processed_count} новых рекламаций")
        else:
            # Запускаем постоянный мониторинг
            connector.setup_monitoring(interval_minutes=args.interval)
    
    except Exception as e:
        logger.error(f"Критическая ошибка при выполнении: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    main()