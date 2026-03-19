#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тестовый скрипт для проверки подключения к Битрикс24
и получения информации о списке рекламаций
"""

import os
import logging
import json
import time
from typing import Dict, Any, List
from dotenv import load_dotenv
from bitrix24_integration import Bitrix24Integration

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BitrixTest")

# Загружаем переменные окружения из .env
load_dotenv()

def test_bitrix_connection():
    """Тестирует подключение к Битрикс24 и проверяет список рекламаций"""
    try:
        # Получаем webhook URL из .env
        webhook_url = os.getenv('BITRIX24_WEBHOOK')
        if not webhook_url:
            logger.error("Webhook URL не найден в .env файле")
            return
            
        logger.info(f"Используем webhook: {webhook_url}")
        
        # Создаем экземпляр Bitrix24Integration
        bitrix = Bitrix24Integration(webhook_url)
        
        # Ищем список "Работа с Рекламациями" в бизнес-процессах
        # Список был перемещён из универсальных списков в бизнес-процессы
        lists_result = bitrix._make_request('lists.get', {'IBLOCK_TYPE_ID': 'bitrix_processes'})
        
        target_list = None
        lists = lists_result.get('result', [])
        
        for lst in lists:
            logger.info(f"Найден список: ID={lst.get('ID')}, NAME={lst.get('NAME')}")
            # Ищем список с ID=100 (Работа с Рекламациями)
            if lst.get('ID') == '100' or lst.get('ID') == 100:
                target_list = lst
                break
        
        if not target_list:
            logger.error("Список с ID=100 не найден в бизнес-процессах")
            return
            
        logger.info(f"Список найден: ID={target_list.get('ID')}, CODE={target_list.get('CODE')}")
        
        # Сохраняем ID списка
        list_id = target_list.get('ID')
        bitrix.iblock_id = list_id
        
        # Получаем поля списка - исправленная версия
        fields_result = bitrix._make_request('lists.field.get', {
            'IBLOCK_TYPE_ID': 'bitrix_processes',
            'IBLOCK_ID': list_id
        })
        
        # Выводим сырые данные для отладки
        logger.debug(f"Сырой ответ API: {json.dumps(fields_result, ensure_ascii=False, default=str)}")
        
        # Получаем поля
        fields = fields_result.get('result', {})
        
        # Проверяем формат полей
        logger.info(f"Найдено полей в списке: {len(fields) if isinstance(fields, dict) else 'не определено'}")
        logger.info(f"Тип полей: {type(fields)}")
        
        # Выводим данные о полях
        field_mapping = {}
        
        if isinstance(fields, dict):
            logger.info("Обрабатываем поля как словарь")
            # Выводим ключи для отладки
            logger.info(f"Ключи словаря полей: {list(fields.keys())}")
            
            # Обходим словарь полей
            for field_id, field_data in fields.items():
                if isinstance(field_data, dict):
                    field_name = field_data.get('NAME', '')
                    field_type = field_data.get('TYPE', '')
                    
                    logger.info(f"Поле: ID={field_id}, NAME={field_name}, TYPE={field_type}")
                    
                    # Формируем маппинг полей для Битрикс24
                    if field_type == 'PROPERTY':
                        property_id = field_id.split('_')[-1] if '_' in field_id else field_id
                        if property_id and field_name:
                            field_mapping[field_name] = f"PROPERTY_{property_id}"
                    else:
                        if field_id and field_name:
                            field_mapping[field_name] = field_id
                else:
                    logger.info(f"Поле {field_id} имеет тип {type(field_data)}: {field_data}")
        elif isinstance(fields, list):
            logger.info("Обрабатываем поля как список")
            for field in fields:
                if isinstance(field, dict):
                    field_id = field.get('FIELD_ID', '')
                    field_name = field.get('NAME', '')
                    field_type = field.get('TYPE', '')
                    
                    logger.info(f"Поле: ID={field_id}, NAME={field_name}, TYPE={field_type}")
                    
                    # Формируем маппинг полей для Битрикс24
                    if field_type == 'PROPERTY':
                        property_id = field_id.split('_')[-1] if field_id else None
                        if property_id and field_name:
                            field_mapping[field_name] = f"PROPERTY_{property_id}"
                    else:
                        if field_id and field_name:
                            field_mapping[field_name] = field_id
                else:
                    logger.info(f"Элемент не является словарем: {type(field)}")
        else:
            logger.info(f"Поля имеют неожиданный тип: {type(fields)}")
            # Попробуем вывести содержимое для отладки
            logger.info(f"Содержимое полей: {fields}")
        
        # Сохраняем маппинг полей в JSON файл для использования в основном коде
        with open('bitrix_field_mapping.json', 'w', encoding='utf-8') as f:
            json.dump(field_mapping, f, ensure_ascii=False, indent=2)
            
        logger.info(f"Маппинг полей сохранен в файл 'bitrix_field_mapping.json'")
        
        # Добавляем задержку перед следующим запросом
        time.sleep(1)
        
        # Создаем параметры для добавления тестового элемента
        test_element = {
            'IBLOCK_TYPE_ID': 'bitrix_processes',
            'IBLOCK_ID': list_id,
            'ELEMENT_CODE': f"TEST_RECL_{int(time.time())}",
            'FIELDS': {
                'NAME': 'Тестовая рекламация',
                'PROPERTY_1000': str(int(time.time()))  # id рекламации - уникальный номер
            }
        }
        
        # Добавляем другие известные поля, если они есть в маппинге
        if 'Тема письма' in field_mapping:
            test_element['FIELDS'][field_mapping['Тема письма']] = 'Тестовая тема рекламации'
        
        if 'Отправитель' in field_mapping:
            test_element['FIELDS'][field_mapping['Отправитель']] = 'test@example.com'
        
        if 'Дата получения' in field_mapping:
            test_element['FIELDS'][field_mapping['Дата получения']] = '28.04.2025'
        
        if 'Статус рекламации' in field_mapping:
            test_element['FIELDS'][field_mapping['Статус рекламации']] = 'Получена'
        
        if 'Категория' in field_mapping:
            test_element['FIELDS'][field_mapping['Категория']] = 'Наземка'
            
        # Выводим параметры для отладки
        logger.debug(f"Параметры добавления элемента: {json.dumps(test_element, ensure_ascii=False)}")
        
        # Добавляем тестовый элемент
        add_result = bitrix._make_request('lists.element.add', test_element)
        
        if add_result and 'result' in add_result:
            element_id = add_result['result']
            logger.info(f"Тестовая рекламация успешно добавлена, ID={element_id}")
        else:
            logger.error(f"Не удалось добавить тестовую рекламацию: {add_result}")
            
    except Exception as e:
        logger.error(f"Ошибка при тестировании подключения к Битрикс24: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    test_bitrix_connection()