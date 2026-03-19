"""
Модуль для интеграции с Битрикс24 API и заполнения универсального списка рекламаций

Этот модуль предоставляет функции для:
1. Подключения к Битрикс24 через REST API
2. Создания и обновления элементов универсального списка
3. Отправки данных о рекламациях в Битрикс24

Настройка:
1. Вам нужно создать вебхук в Битрикс24
   - Перейдите в Профиль пользователя > Пароли приложений > Создать пароль для вебхуков
   - Выберите доступ к универсальным списка (lists)
   - Скопируйте URL вебхука
2. Укажите URL вебхука при создании экземпляра Bitrix24Integration или в переменной среды BITRIX24_WEBHOOK
"""
import os
import re
import json
import time
import base64
import logging
import requests
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import urlparse

# Настройка логирования
logger = logging.getLogger("Bitrix24Integration")

class Bitrix24Integration:
    """Класс для работы с API Битрикс24"""
    
    def __init__(self, webhook_url=None):
        # Загружаем данные для подключения
        self.webhook_url = webhook_url or os.getenv('BITRIX24_WEBHOOK')
        if not self.webhook_url:
            raise ValueError("Необходимо указать webhook URL для Битрикс24")
        parsed = urlparse(self.webhook_url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError("BITRIX24_WEBHOOK должен начинаться с http:// или https://")
        if parsed.hostname in ('169.254.169.254', '::1') or parsed.hostname == 'metadata.google.internal':
            raise ValueError("BITRIX24_WEBHOOK указывает на запрещённый адрес")
        
        # Информация о списке рекламаций
        # Список перенесён в бизнес-процессы (bitrix_processes), ID=100
        self.iblock_type_id = "bitrix_processes"
        self.iblock_code = None  # Код инфоблока списка (будет установлен позже)
        self.iblock_id = 100  # ID инфоблока списка
        
        # Кэш для полей списка
        self.fields_cache = {}
        self.fields_mapping = {}  # Соответствие наших полей и полей Битрикс24
    
    def _make_request(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Выполняет запрос к REST API Битрикс24
        
        Args:
            method: Метод API, например 'lists.get'
            params: Параметры для метода
            
        Returns:
            Результат запроса
        """
        if params is None:
            params = {}
        
        # Формируем URL запроса
        url = f"{self.webhook_url}/{method}"
        
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Вызов метода {method} с параметрами: {json.dumps(params, ensure_ascii=False)[:100]}...")
                response = requests.post(url, json=params, timeout=30)
                
                # Проверяем статус ответа
                if response.status_code == 200:
                    result = response.json()
                    
                    # Проверяем наличие ошибок в ответе Битрикс24
                    if 'error' in result:
                        error_code = result.get('error', '')
                        error_desc = result.get('error_description', 'Unknown')
                        error_msg = f"Ошибка Битрикс24 [{error_code}]: {error_desc}"
                        logger.error(error_msg)
                        raise Exception(error_msg)
                    
                    return result
                else:
                    logger.error(f"Ошибка HTTP: {response.status_code} - {response.text}")
                    raise Exception(f"Ошибка HTTP: {response.status_code}")
            
            except Exception as e:
                logger.error(f"Ошибка при вызове {method}: {e}")
                
                if attempt < max_retries - 1:
                    logger.info(f"Повторная попытка через {retry_delay} сек...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку при каждой повторной попытке
                else:
                    raise
    
    def find_reclamation_list(self) -> bool:
        """
        Находит готовый список для рекламаций в Битрикс24
        """
        try:
            # Используем известный ID списка напрямую
            # Список перенесён в бизнес-процессы (bitrix_processes)
            self.iblock_id = 100
            self.iblock_type_id = 'bitrix_processes'
            logger.info(f"Используется список с ID={self.iblock_id} (тип: {self.iblock_type_id})")
            return True
        except Exception as e:
            logger.error(f"Ошибка при поиске списка рекламаций: {e}")
            return False
    
    def create_reclamation_list(self) -> bool:
        """
        Создает новый универсальный список "Рекламации", если он не существует
        
        Returns:
            True, если список создан успешно, иначе False
        """
        try:
            # Сначала проверим, существует ли уже список
            if self.find_reclamation_list():
                logger.info("Список 'Рекламации' уже существует")
                return True
            
            # Создаем новый список
            params = {
                'IBLOCK_TYPE_ID': self.iblock_type_id,
                'FIELDS': {
                    'NAME': 'Рекламации',
                    'DESCRIPTION': 'Список для хранения информации о рекламациях',
                    'SORT': '500',
                    'BIZPROC': 'Y'  # Включаем поддержку бизнес-процессов
                }
            }
            
            result = self._make_request('lists.add', params)
            
            if result.get('result'):
                self.iblock_id = result['result']
                
                # Получаем код созданного списка
                list_info = self._make_request('lists.get', {
                    'IBLOCK_TYPE_ID': self.iblock_type_id,
                    'IBLOCK_ID': self.iblock_id
                })
                
                if list_info.get('result'):
                    self.iblock_code = list_info['result'][0]['CODE']
                    logger.info(f"Создан новый список 'Рекламации': ID={self.iblock_id}, CODE={self.iblock_code}")
                    return True
            
            logger.error("Не удалось создать список 'Рекламации'")
            return False
            
        except Exception as e:
            logger.error(f"Ошибка при создании списка рекламаций: {e}")
            return False
    
    def get_list_fields(self) -> List[Dict[str, Any]]:
        # ...
        try:
            result = self._make_request('lists.field.get', {
                'IBLOCK_TYPE_ID': self.iblock_type_id,
                'IBLOCK_ID': self.iblock_id
            })
            
            fields = result.get('result', [])
            
            # Обрабатываем разные форматы ответа API
            if isinstance(fields, dict):
                fields_list = []
                for field_id, field_data in fields.items():
                    if isinstance(field_data, dict):
                        field_data['FIELD_ID'] = field_id
                        fields_list.append(field_data)
                fields = fields_list
            
            # Кэшируем информацию о полях
            self.fields_cache = {field.get('FIELD_ID', ''): field for field in fields if isinstance(field, dict)}
            
            logger.info(f"Получены поля списка 'Рекламации': {len(fields)} полей")
            return fields
            
        except Exception as e:
            logger.error(f"Ошибка при получении полей списка: {e}")
            return []
    
    def create_field(self, field_name: str, field_type: str, is_required: bool = False, 
                     is_multiple: bool = False, sort: int = 500, list_values: List[str] = None,
                     default_value: str = None) -> Optional[str]:
        """
        Создает новое поле в списке
        
        Args:
            field_name: Название поля
            field_type: Тип поля (S-строка, N-число, L-список, F-файл, G-привязка к разделам)
            is_required: Обязательное ли поле
            is_multiple: Множественное ли поле
            sort: Порядок сортировки
            list_values: Значения для списка (если тип L)
            default_value: Значение по умолчанию
            
        Returns:
            ID созданного поля или None в случае ошибки
        """
        if not self.iblock_id:
            if not self.find_reclamation_list():
                logger.error("Список рекламаций не найден")
                return None
        
        try:
            # Генерируем код поля
            field_code = self._generate_field_code(field_name)
            
            # Подготавливаем параметры
            params = {
                'IBLOCK_TYPE_ID': self.iblock_type_id,
                'IBLOCK_ID': self.iblock_id,
                'FIELDS': {
                    'NAME': field_name,
                    'IS_REQUIRED': 'Y' if is_required else 'N',
                    'MULTIPLE': 'Y' if is_multiple else 'N',
                    'TYPE': field_type,
                    'SORT': str(sort),
                    'CODE': field_code
                }
            }
            
            # Добавляем значения списка, если нужно
            if field_type == 'L' and list_values:
                list_items = {}
                for i, value in enumerate(list_values):
                    list_items[str(i)] = {
                        'SORT': str((i + 1) * 10),
                        'VALUE': value
                    }
                params['FIELDS']['LIST'] = list_items
                
                # Устанавливаем значение по умолчанию для списка
                if default_value and default_value in list_values:
                    default_index = list_values.index(default_value)
                    params['FIELDS']['LIST_DEF'] = {
                        '0': str(default_index)
                    }
            elif default_value:
                params['FIELDS']['DEFAULT_VALUE'] = default_value
            
            # Создаем поле
            result = self._make_request('lists.field.add', params)
            
            if result.get('result'):
                field_id = result['result']
                logger.info(f"Создано новое поле '{field_name}' (ID: {field_id}, CODE: {field_code})")
                
                # Обновляем кэш полей
                self.fields_mapping[field_name] = f"PROPERTY_{field_id.split('_')[-1]}"
                return field_id
            
            logger.error(f"Не удалось создать поле '{field_name}'")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при создании поля '{field_name}': {e}")
            return None
    
    def _generate_field_code(self, field_name: str) -> str:
        """
        Генерирует код поля из его названия
        
        Args:
            field_name: Название поля
            
        Returns:
            Код поля
        """
        # Транслитерация русских букв
        transliteration = {
            'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
            'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
            'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
            'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
            'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
        }
        
        # Преобразуем название в код
        code = ''.join([transliteration.get(c.lower(), c) for c in field_name if c.isalnum() or c.isspace()])
        code = code.replace(' ', '_').upper()
        
        # Если код получился длиннее 50 символов, обрезаем
        if len(code) > 50:
            code = code[:50]
        
        return code
    
    def setup_reclamation_fields(self) -> bool:
        """
        Настраивает все необходимые поля для списка рекламаций
        
        Returns:
            True, если все поля созданы успешно, иначе False
        """
        if not self.iblock_id:
            if not self.find_reclamation_list():
                if not self.create_reclamation_list():
                    logger.error("Не удалось найти или создать список рекламаций")
                    return False
        
        try:
            # Получаем текущие поля списка
            existing_fields = self.get_list_fields()
            existing_field_names = [field.get('NAME', '') for field in existing_fields]
            
            # Определяем поля, которые нужно создать
            fields_to_create = [
                # Основные поля
                {'name': 'Тема письма', 'type': 'S', 'required': True},
                {'name': 'Отправитель', 'type': 'S', 'required': True},
                {'name': 'Дата получения', 'type': 'S', 'required': True},  # Используем строку для даты, т.к. работаем с разными форматами
                {'name': 'Статус рекламации', 'type': 'L', 'required': True, 'list_values': ['Получена', 'В обработке', 'Закрыта'], 'default': 'Получена'},
                {'name': 'Категория', 'type': 'L', 'required': False, 'list_values': ['Наземка', 'Метро', 'Спецтехника', 'ЖДТ']},
                {'name': 'Подкатегории', 'type': 'S', 'required': False},
                {'name': 'Сотрудники в ответе', 'type': 'S', 'required': False, 'multiple': True},
                {'name': 'Сотрудники в копии', 'type': 'S', 'required': False, 'multiple': True},
                
                # Данные о продукте
                {'name': 'Название продукта', 'type': 'S', 'required': False},
                {'name': 'Код продукта', 'type': 'S', 'required': False},
                {'name': 'Модель', 'type': 'S', 'required': False},
                {'name': 'Серийный номер', 'type': 'S', 'required': False},
                {'name': 'Дата производства', 'type': 'S', 'required': False},
                {'name': 'Дата ввода в эксплуатацию', 'type': 'S', 'required': False},
                
                # Данные о проблеме
                {'name': 'Описание проблемы', 'type': 'S', 'required': False},
                {'name': 'Дата возникновения', 'type': 'S', 'required': False},
                {'name': 'Серьезность', 'type': 'L', 'required': False, 'list_values': ['Низкая', 'Средняя', 'Высокая'], 'default': 'Средняя'},
                
                # Данные о клиенте
                {'name': 'Название организации', 'type': 'S', 'required': False},
                {'name': 'Контактное лицо', 'type': 'S', 'required': False},
                {'name': 'Телефон', 'type': 'S', 'required': False},
                {'name': 'Адрес', 'type': 'S', 'required': False},
                {'name': 'Номер договора/счета', 'type': 'S', 'required': False},
                
                # Дополнительно
                {'name': 'Ссылка на вложения', 'type': 'S', 'required': False},
                {'name': 'Количество вложений', 'type': 'N', 'required': False},
                {'name': 'Результат обработки', 'type': 'S', 'required': False},
                {'name': 'История обработки', 'type': 'S', 'required': False},
                # Английские поля удалены - используем русские аналоги из bitrix_field_mapping.json
            ]
            
            # Создаем недостающие поля
            created_count = 0
            for field_info in fields_to_create:
                if field_info['name'] not in existing_field_names:
                    field_id = self.create_field(
                        field_name=field_info['name'],
                        field_type=field_info['type'],
                        is_required=field_info.get('required', False),
                        is_multiple=field_info.get('multiple', False),
                        list_values=field_info.get('list_values'),
                        default_value=field_info.get('default')
                    )
                    
                    if field_id:
                        created_count += 1
            
            # Обновляем наш маппинг полей
            self.update_fields_mapping()
            
            logger.info(f"Настройка полей завершена: создано {created_count} новых полей")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при настройке полей списка: {e}")
            return False
    
    def update_fields_mapping(self) -> None:
        """Обновляет соответствие между нашими полями и полями Битрикс24"""
        # Получаем информацию о всех полях
        fields = self.get_list_fields()
        
        # Создаем мапинг имен полей к их кодам в Битрикс24
        self.fields_mapping = {}
        for field in fields:
            if field.get('TYPE') == 'PROPERTY':
                property_id = field.get('FIELD_ID', '').split('_')[-1]
                self.fields_mapping[field.get('NAME')] = f"PROPERTY_{property_id}"
            elif field.get('TYPE') == 'FIELD':
                self.fields_mapping[field.get('NAME')] = field.get('FIELD_ID')
    
    def add_reclamation(self, reclamation_data: Dict[str, Any]) -> Optional[int]:
        """
        Добавляет новую рекламацию в список
        
        Args:
            reclamation_data: Данные о рекламации
            
        Returns:
            ID созданного элемента или None в случае ошибки
        """
        if not self.iblock_id:
            if not self.find_reclamation_list():
                logger.error("Список рекламаций не найден")
                return None
        
        # Если маппинг полей пуст, обновляем его
        if not self.fields_mapping:
            self.update_fields_mapping()
        
        try:
            # Подготавливаем данные для добавления
            element_fields = {
                'IBLOCK_TYPE_ID': self.iblock_type_id,
                'IBLOCK_ID': self.iblock_id,
                'ELEMENT_CODE': f"RECL_{int(time.time())}",  # Уникальный код элемента
                'FIELDS': {
                    'NAME': reclamation_data.get('subject', 'Новая рекламация')  # Название элемента
                }
            }
            
            # Добавляем все поля рекламации
            for field_name, field_value in reclamation_data.items():
                bitrix_field_id = self.fields_mapping.get(field_name)
                
                # Если есть соответствие поля в Битрикс24
                if bitrix_field_id:
                    # Для множественных полей преобразуем значение в список
                    if isinstance(field_value, list):
                        element_fields['FIELDS'][bitrix_field_id] = field_value
                    else:
                        element_fields['FIELDS'][bitrix_field_id] = str(field_value)
            
            # Добавляем элемент
            result = self._make_request('lists.element.add', element_fields)
            
            if result.get('result'):
                element_id = result['result']
                logger.info(f"Создан новый элемент рекламации: ID={element_id}")
                return element_id
            
            logger.error("Не удалось создать элемент рекламации")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при добавлении рекламации: {e}")
            return None
    
    def update_reclamation(self, element_id: int, reclamation_data: Dict[str, Any]) -> bool:
        """
        Обновляет данные существующей рекламации
        
        Args:
            element_id: ID элемента
            reclamation_data: Новые данные о рекламации
            
        Returns:
            True, если обновление успешно, иначе False
        """
        if not self.iblock_id:
            if not self.find_reclamation_list():
                logger.error("Список рекламаций не найден")
                return False
        
        # Если маппинг полей пуст, обновляем его
        if not self.fields_mapping:
            self.update_fields_mapping()
        
        try:
            # Подготавливаем данные для обновления
            element_fields = {
                'IBLOCK_TYPE_ID': self.iblock_type_id,
                'IBLOCK_ID': self.iblock_id,
                'ELEMENT_ID': element_id,
                'FIELDS': {}
            }
            
            # Добавляем все поля рекламации
            for field_name, field_value in reclamation_data.items():
                bitrix_field_id = self.fields_mapping.get(field_name)
                
                # Если есть соответствие поля в Битрикс24
                if bitrix_field_id:
                    # Для множественных полей преобразуем значение в список
                    if isinstance(field_value, list):
                        element_fields['FIELDS'][bitrix_field_id] = field_value
                    else:
                        element_fields['FIELDS'][bitrix_field_id] = str(field_value)
            
            # Обновляем элемент
            result = self._make_request('lists.element.update', element_fields)
            
            if result.get('result'):
                logger.info(f"Обновлен элемент рекламации: ID={element_id}")
                return True
            
            logger.error(f"Не удалось обновить элемент рекламации: ID={element_id}")
            return False
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении рекламации: {e}")
            return False
    
    def find_reclamation_by_subject(self, subject: str) -> Optional[int]:
        """
        Ищет рекламацию по теме письма
        
        Args:
            subject: Тема письма
            
        Returns:
            ID элемента или None, если не найден
        """
        if not self.iblock_id:
            if not self.find_reclamation_list():
                logger.error("Список рекламаций не найден")
                return None
        
        try:
            # Получаем все элементы списка
            result = self._make_request('lists.element.get', {
                'IBLOCK_TYPE_ID': self.iblock_type_id,
                'IBLOCK_ID': self.iblock_id
            })
            
            elements = result.get('result', [])
            
            # Ищем элемент с нужной темой
            for element in elements:
                if element.get('NAME') == subject:
                    logger.info(f"Найдена рекламация по теме '{subject}': ID={element.get('ID')}")
                    return element.get('ID')
            
            logger.info(f"Рекламация с темой '{subject}' не найдена")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при поиске рекламации: {e}")
            return None
    
    def process_reclamation(self, reclamation_data: Dict[str, Any]) -> Optional[int]:
        """
        Обрабатывает данные рекламации: добавляет новую или обновляет существующую
        
        Args:
            reclamation_data: Данные о рекламации
            
        Returns:
            ID элемента списка или None в случае ошибки
        """
        # Проверяем существование списка
        if not self.iblock_id:
            if not self.find_reclamation_list():
                if not self.create_reclamation_list():
                    logger.error("Не удалось найти или создать список рекламаций")
                    return None
        
        # Настраиваем поля списка (если нужно)
        if not self.fields_mapping:
            self.setup_reclamation_fields()
        
        try:
            # Ищем существующую рекламацию по теме
            element_id = None
            if 'subject' in reclamation_data:
                element_id = self.find_reclamation_by_subject(reclamation_data['subject'])
            
            # Если рекламация найдена, обновляем ее
            if element_id:
                success = self.update_reclamation(element_id, reclamation_data)
                return element_id if success else None
            
            # Иначе создаем новую
            return self.add_reclamation(reclamation_data)
            
        except Exception as e:
            logger.error(f"Ошибка при обработке рекламации: {e}")
            return None

    # ========== Методы для работы с Bitrix24 Disk ==========

    COMMON_STORAGE_ID = 11  # "Общий диск"
    RECLAMATION_FOLDER_NAME = "РЕКЛАМАЦИИ_АВТО_ИИ"

    def get_or_create_reclamation_folder(self) -> Optional[int]:
        """
        Найти или создать главную папку для рекламаций на Общем диске.

        Returns:
            ID папки или None в случае ошибки
        """
        try:
            # 1. Получить содержимое общего диска
            result = self._make_request('disk.storage.getchildren', {
                'id': self.COMMON_STORAGE_ID
            })

            children = result.get('result', [])

            # 2. Найти папку РЕКЛАМАЦИИ_АВТО_ИИ
            for item in children:
                if item.get('NAME') == self.RECLAMATION_FOLDER_NAME and item.get('TYPE') == 'folder':
                    logger.info(f"Найдена папка {self.RECLAMATION_FOLDER_NAME}, ID={item['ID']}")
                    return int(item['ID'])

            # 3. Создать если не найдена
            logger.info(f"Создаём папку {self.RECLAMATION_FOLDER_NAME} на Общем диске")
            result = self._make_request('disk.storage.addfolder', {
                'id': self.COMMON_STORAGE_ID,
                'data': {'NAME': self.RECLAMATION_FOLDER_NAME}
            })

            folder_id = int(result['result']['ID'])
            logger.info(f"Папка {self.RECLAMATION_FOLDER_NAME} создана, ID={folder_id}")
            return folder_id

        except Exception as e:
            logger.error(f"Ошибка при создании папки рекламаций: {e}")
            return None

    def create_reclamation_subfolder(self, email_id: str, subject: str, date: str = None) -> Optional[int]:
        """
        Создать подпапку для конкретной рекламации.
        Формат: 2026-02-05_41123_претензия_354

        Args:
            email_id: ID письма
            subject: Тема письма
            date: Дата (если не указана, используется текущая)

        Returns:
            ID созданной папки или None
        """
        try:
            parent_id = self.get_or_create_reclamation_folder()
            if not parent_id:
                return None

            # Очистить тему от спецсимволов
            safe_subject = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', subject)[:50].strip()
            if not safe_subject:
                safe_subject = "без_темы"

            # Формируем имя папки
            if not date:
                date = datetime.now().strftime('%Y-%m-%d')
            folder_name = f"{date}_{email_id}_{safe_subject}"

            try:
                result = self._make_request('disk.folder.addsubfolder', {
                    'id': parent_id,
                    'data': {'NAME': folder_name}
                })
                folder_id = int(result['result']['ID'])
                logger.info(f"Создана подпапка для рекламации: {folder_name}, ID={folder_id}")
                return folder_id

            except Exception as e:
                # Папка может уже существовать
                err_str = str(e).lower()
                if ('disk_obj_22000' in err_str or
                    'already exists' in err_str or
                    'уже есть' in err_str or
                    'уже существует' in err_str):
                    logger.info(f"Папка {folder_name} уже существует, ищем её")
                    # Найти существующую
                    children_result = self._make_request('disk.folder.getchildren', {'id': parent_id})
                    for item in children_result.get('result', []):
                        if item.get('NAME') == folder_name and item.get('TYPE') == 'folder':
                            logger.info(f"Найдена существующая папка: {folder_name}, ID={item['ID']}")
                            return int(item['ID'])
                raise

        except Exception as e:
            logger.error(f"Ошибка при создании подпапки для рекламации: {e}")
            return None

    def upload_file_to_disk(self, folder_id: int, filepath: str) -> Optional[Dict[str, Any]]:
        """
        Загрузить файл в указанную папку на диске.

        Args:
            folder_id: ID папки
            filepath: Путь к локальному файлу

        Returns:
            Данные о загруженном файле или None
        """
        try:
            if not os.path.exists(filepath):
                logger.error(f"Файл не найден: {filepath}")
                return None

            filename = os.path.basename(filepath)
            file_size = os.path.getsize(filepath)

            logger.info(f"Загружаем файл {filename} ({file_size} байт) в папку ID={folder_id}")

            # Читаем файл и кодируем в base64
            with open(filepath, 'rb') as f:
                content = base64.b64encode(f.read()).decode('utf-8')

            result = self._make_request('disk.folder.uploadfile', {
                'id': folder_id,
                'data': {'NAME': filename},
                'fileContent': [filename, content],
                'generateUniqueName': True
            })

            file_data = result.get('result', {})
            if file_data.get('ID'):
                logger.info(f"Файл загружен: {filename}, ID={file_data['ID']}")
            return file_data

        except Exception as e:
            logger.error(f"Ошибка при загрузке файла {filepath}: {e}")
            return None

    def get_file_external_link(self, file_id: int) -> Optional[str]:
        """
        Получить публичную ссылку на файл.

        Args:
            file_id: ID файла на диске

        Returns:
            Публичная ссылка или None
        """
        try:
            result = self._make_request('disk.file.getExternalLink', {'id': file_id})
            link = result.get('result', '')
            if link:
                logger.info(f"Получена ссылка на файл ID={file_id}: {link}")
            return link

        except Exception as e:
            logger.error(f"Ошибка при получении ссылки на файл ID={file_id}: {e}")
            return None