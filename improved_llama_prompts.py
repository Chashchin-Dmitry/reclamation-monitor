"""
Улучшенные промпты для LLaMA API
"""

# Улучшенный промпт для определения рекламации
RECLAMATION_DETECTION_PROMPT = """
Ты - специалист по анализу технических рекламаций. 
Проанализируй следующее письмо, его вложения и определи, является ли оно рекламацией:

ТЕМА: {subject}

СОДЕРЖАНИЕ ПИСЬМА: {body}

ВЛОЖЕНИЯ:
{attachments_info}

ТЕКСТ ИЗ ВЛОЖЕНИЙ:
{attachments_text}

Рекламация - это ЛЮБОЕ обращение клиента об обнаруженных проблемах, дефектах, неисправностях продукции ЭПОТОС.
Формы: претензия, жалоба, уведомление о несоответствии, акт обследования, фото с описанием проблемы, даже просто email о поломке.
ГЛАВНОЕ: клиент указывает на проблему с продуктом → это рекламация.

ЭПОТОС производит: системы пожаротушения, модули (МПП Тунгус и др.), извещатели (ИП-212, ДИП-34А и др.),
системы для транспорта, метро, ЖД и спецтехники.

КРИТИЧЕСКИЕ ПРАВИЛА (проверь ПЕРВЫМИ):
1. Если в теме/содержании/вложениях есть слова "рекламация", "претензия", "несоответствие", "дефект", "уведомление об обнаружении", "неисправность", "брак" → is_reclamation = TRUE (100%)
2. Если вложения содержат уведомления о несоответствии, акты обследования, рекламационные акты → is_reclamation = TRUE (100%)
3. При сомнении → выбирай TRUE. Лучше ложное срабатывание, чем пропуск рекламации.

Если это рекламация, укажи "is_reclamation": true и извлеки следующую информацию:
- product_name: название продукта, на который поступила рекламация (если нет - использовать "n/a")
- issue_description: краткое описание проблемы (если нет - использовать "n/a")
- severity: степень серьезности проблемы (низкая/средняя/высокая, если нет - использовать "средняя")
- customer_name: имя клиента или организации (если нет - использовать "n/a")
- reclamation_category: категория рекламации (Наземка, Метро, Спецтехника, ЖДТ, если нет - использовать "n/a")

Если это не рекламация, укажи "is_reclamation": false и кратко объясни причину.

Дополнительные индикаторы рекламации:
1. Упоминание неисправностей, поломок или неудовлетворительной работы устройств
2. Упоминание предприятий из транспортной сферы (РЖД, метрополитен, автобусные парки, ТМХ)
3. Наличие фотографий изделий, уведомлений о несоответствии, актов обследования/расследования
4. Запросы на ремонт, обслуживание или техническую поддержку

ВАЖНО: Пожалуйста, всегда возвращай JSON-ответ. Если какая-либо информация отсутствует, используй "n/a".
"""


# Улучшенный промпт для извлечения деталей из рекламации
DETAILS_EXTRACTION_PROMPT = """
Ты - специалист по извлечению подробной информации из рекламаций.
На основе следующего содержимого письма и его вложений, извлеки все доступные данные о рекламации:

ТЕМА: {subject}

СОДЕРЖАНИЕ ПИСЬМА: {body}

ВЛОЖЕНИЯ:
{attachments_info}

ТЕКСТ ИЗ ВЛОЖЕНИЙ:
{attachments_text}

ОПРЕДЕЛЕННАЯ КАТЕГОРИЯ: {reclamation_category}

Извлеки следующие поля (если информация отсутствует, используй "n/a"):
- product_code: код продукта или артикул (если нет - "n/a")
- model_number: номер модели или модификация (если нет - "n/a")
- serial_number: серийный номер (если нет - "n/a")
- purchase_date: дата покупки (если нет - "n/a")
- issue_date: дата возникновения проблемы (если нет - "n/a")
- customer_id: ID клиента или название организации (если нет - "n/a")
- store_location: место покупки или установки (если нет - "n/a")
- warranty_status: статус гарантии (если нет - "n/a")
- dealer_name: название дилера или поставщика (если нет - "n/a")
- contact_person: контактное лицо (если нет - "n/a")
- phone_number: телефон контактного лица (если нет - "n/a")
- return_address: адрес для возврата товара (если нет - "n/a")
- tracking_number: номер отслеживания (если нет - "n/a")
- invoice_number: номер счёта или договора (если нет - "n/a")
- act_number: номер рекламационного акта (если нет - "n/a")

Дополнительно, поскольку категория рекламации: {reclamation_category}, обрати особое внимание на:
- для категории "Наземка": тип транспорта, маршрут, гаражный номер, модель транспортного средства
- для категории "Метро": линия метро, номер вагона, депо
- для категории "Спецтехника": тип спецтехники, марка, модель
- для категории "ЖДТ": номер состава, участок ЖД, тип подвижного состава

Важно: сохраняй точное форматирование дат, номеров и кодов как в исходном тексте.
Всегда включай все перечисленные поля в ответ, даже если значение - "n/a".

Ответ предоставь в формате JSON.
/no_think"""


# Функция для формирования промпта с данными
def format_reclamation_detection_prompt(email_data, attachments):
    """
    Форматирует промпт для определения рекламации
    
    Args:
        email_data: Данные письма
        attachments: Информация о вложениях
        
    Returns:
        Отформатированный промпт
    """
    subject = email_data.get('subject', 'Нет темы')
    body = email_data.get('body', 'Нет содержимого')
    
    # Формируем информацию о вложениях
    attachments_info = ""
    attachments_text = ""
    
    for i, att in enumerate(attachments, 1):
        filename = att.get('filename', 'unknown')
        content_type = att.get('content_type', 'unknown')
        attachments_info += f"{i}. {filename} ({content_type})\n"
        
        extracted_text = att.get('extracted_text', '')
        if extracted_text:
            attachments_text += f"--- Содержимое вложения {i}: {filename} ---\n"
            # Ограничиваем длину текста для промпта
            if len(extracted_text) > 5000:
                attachments_text += extracted_text[:5000] + "...\n[Текст вложения слишком длинный, показаны первые 5000 символов]\n"
            else:
                attachments_text += extracted_text + "\n"
    
    if not attachments_info:
        attachments_info = "Вложения отсутствуют"
    
    if not attachments_text:
        attachments_text = "Текст из вложений не извлечен"
    
    # Форматируем промпт
    prompt = RECLAMATION_DETECTION_PROMPT.format(
        subject=subject,
        body=body,
        attachments_info=attachments_info,
        attachments_text=attachments_text
    )
    
    return prompt


def format_details_extraction_prompt(email_data, attachments, reclamation_category):
    """
    Форматирует промпт для извлечения деталей из рекламации
    
    Args:
        email_data: Данные письма
        attachments: Информация о вложениях
        reclamation_category: Категория рекламации
        
    Returns:
        Отформатированный промпт
    """
    subject = email_data.get('subject', 'Нет темы')
    body = email_data.get('body', 'Нет содержимого')
    
    # Формируем информацию о вложениях
    attachments_info = ""
    attachments_text = ""
    
    for i, att in enumerate(attachments, 1):
        filename = att.get('filename', 'unknown')
        content_type = att.get('content_type', 'unknown')
        attachments_info += f"{i}. {filename} ({content_type})\n"
        
        extracted_text = att.get('extracted_text', '')
        if extracted_text:
            attachments_text += f"--- Содержимое вложения {i}: {filename} ---\n"
            # Ограничиваем длину текста для промпта
            if len(extracted_text) > 5000:
                attachments_text += extracted_text[:5000] + "...\n[Текст вложения слишком длинный, показаны первые 5000 символов]\n"
            else:
                attachments_text += extracted_text + "\n"
    
    if not attachments_info:
        attachments_info = "Вложения отсутствуют"
    
    if not attachments_text:
        attachments_text = "Текст из вложений не извлечен"
    
    # Форматируем промпт
    prompt = DETAILS_EXTRACTION_PROMPT.format(
        subject=subject,
        body=body,
        attachments_info=attachments_info,
        attachments_text=attachments_text,
        reclamation_category=reclamation_category or "Неопределенная категория"
    )
    
    return prompt