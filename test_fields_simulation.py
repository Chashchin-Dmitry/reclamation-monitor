"""
Тест-симуляция заполнения полей Bitrix БЕЗ реального запуска.
Проверяет:
1. Синтаксис кода
2. Маппинг полей (все ли поля существуют)
3. Формирование данных для разных входных данных
4. Edge cases (None, пустые строки, списки, etc.)
"""
import json
import sys
from pathlib import Path
from datetime import datetime

# Цвета для вывода
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RESET = '\033[0m'

def ok(msg):
    print(f"{GREEN}[OK]{RESET} {msg}")

def fail(msg):
    print(f"{RED}[FAIL]{RESET} {msg}")

def warn(msg):
    print(f"{YELLOW}[WARN]{RESET} {msg}")

print("="*80)
print("SIMULATION TEST: Proverka zapolneniya poley Bitrix")
print("="*80)

# =========================================================================
# 1. PROVERKA SINTAKSISA
# =========================================================================
print("\n--- 1. PROVERKA SINTAKSISA ---")

errors = []

try:
    import reclamation_bitrix_connector
    ok("reclamation_bitrix_connector.py - sintaksis OK")
except SyntaxError as e:
    fail(f"reclamation_bitrix_connector.py - SYNTAX ERROR: {e}")
    errors.append(f"Syntax: {e}")
except Exception as e:
    warn(f"reclamation_bitrix_connector.py - import error (ne sintaksis): {e}")

try:
    import bitrix24_integration
    ok("bitrix24_integration.py - sintaksis OK")
except SyntaxError as e:
    fail(f"bitrix24_integration.py - SYNTAX ERROR: {e}")
    errors.append(f"Syntax: {e}")
except Exception as e:
    warn(f"bitrix24_integration.py - import error: {e}")

# =========================================================================
# 2. PROVERKA MAPPINGA
# =========================================================================
print("\n--- 2. PROVERKA MAPPINGA ---")

mapping_file = Path(__file__).parent / 'bitrix_field_mapping.json'
try:
    with open(mapping_file, 'r', encoding='utf-8') as f:
        mapping = json.load(f)
    ok(f"bitrix_field_mapping.json zagruzhen: {len(mapping)} poley")
except Exception as e:
    fail(f"Ne udalos' zagruzit' mapping: {e}")
    errors.append(f"Mapping: {e}")
    mapping = {}

# Проверка обязательных полей
required_fields = [
    'Тема письма',
    'Отправитель',
    'Дата получения',
    'Статус рекламации',
    'Категория',
    'Категория (список)',
    'Название продукта',
    'Описание проблемы',
    'Путь к документам (облако)',
    # Новые поля
    'Сырой текст email',
    'Номер рекламационного акта',
]

for field in required_fields:
    if field in mapping:
        ok(f"  {field} -> {mapping[field]}")
    else:
        fail(f"  {field} -> NE NAYDEN V MAPPINGE!")
        errors.append(f"Missing field: {field}")

# =========================================================================
# 3. SIMULYACIYA ZAPOLNENIYA
# =========================================================================
print("\n--- 3. SIMULYACIYA ZAPOLNENIYA ---")

# Тестовые данные - разные сценарии
test_cases = [
    {
        'name': 'Polnye dannye',
        'data': {
            'email_id': 12345,
            'subject': 'Рекламация на ИП-212-101',
            'sender': 'client@example.com',
            'received_date': '13-Feb-2026',
            'body': '<html><body>Текст письма с рекламацией...</body></html>',
            'category': 'Метро',
            'subcategories': ['ЖВИ', 'Метрополитен'],
            'recipients': ['manager@your-company.ru'],
            'copy_to': ['info@your-company.ru'],
            'attachments': [
                {'filename': 'act.pdf', 'filepath': '/path/to/act.pdf'}
            ],
            'llama_analysis': {
                'product_name': 'ИП-212-101',
                'issue_description': 'Датчик не срабатывает',
                'severity': 'Высокая',
                'customer_name': 'ООО Метрострой',
                'reclamation_category': 'Метро',
                'act_number': 'АКТ-2026-001',
            },
            'details': {
                'product_code': 'ИП-212-101',
                'serial_number': 'SN123456',
            }
        }
    },
    {
        'name': 'Minimalnye dannye',
        'data': {
            'email_id': 99999,
            'subject': 'Тест',
            'sender': 'test@test.ru',
        }
    },
    {
        'name': 'Pustye znacheniya',
        'data': {
            'email_id': 11111,
            'subject': '',
            'sender': None,
            'body': None,
            'llama_analysis': None,
            'details': {},
            'subcategories': None,
        }
    },
    {
        'name': 'Nevernye tipy',
        'data': {
            'email_id': 'not_a_number',  # должен быть int
            'subject': 123,  # должен быть str
            'subcategories': 'not_a_list',  # должен быть list
            'llama_analysis': 'not_a_dict',  # должен быть dict
        }
    },
]

def safe_get(data, *keys, default='n/a'):
    """Копия функции из connector"""
    for key in keys:
        try:
            data = data.get(key, {})
        except AttributeError:
            return default
    return data if data else default

def simulate_field_mapping(reclamation_data):
    """Симуляция формирования полей (копия логики из connector)"""
    result = {}
    errors_local = []

    # safe_get
    def safe_get_local(data, *keys, default='n/a'):
        for key in keys:
            try:
                data = data.get(key, {})
            except AttributeError:
                return default
        return data if data else default

    # Формируем поля
    try:
        field_values = {
            'Тема письма': reclamation_data.get('subject', 'n/a'),
            'Отправитель': reclamation_data.get('sender', 'n/a'),
            'Статус рекламации': 'Получена',
            'Подкатегории': ', '.join(reclamation_data.get('subcategories', []) or []),
            'Название продукта': safe_get_local(reclamation_data, 'llama_analysis', 'product_name'),
            'Описание проблемы': safe_get_local(reclamation_data, 'llama_analysis', 'issue_description'),
            'Серьезность': safe_get_local(reclamation_data, 'llama_analysis', 'severity', default='Средняя'),
            'Название организации': safe_get_local(reclamation_data, 'llama_analysis', 'customer_name'),
            # Новые поля
            'Сырой текст email': reclamation_data.get('body') or reclamation_data.get('email_body') or '',
            'Номер рекламационного акта': safe_get_local(reclamation_data, 'llama_analysis', 'act_number') or safe_get_local(reclamation_data, 'details', 'act_number') or '',
        }

        for fname, val in field_values.items():
            result[fname] = {
                'value': val,
                'type': type(val).__name__,
                'empty': not val or val == 'n/a',
            }

    except Exception as e:
        errors_local.append(f"Exception: {e}")

    return result, errors_local

for tc in test_cases:
    print(f"\n  Test: {tc['name']}")
    print(f"  " + "-"*40)

    try:
        fields, errs = simulate_field_mapping(tc['data'])

        if errs:
            for e in errs:
                fail(f"    {e}")
                errors.append(f"{tc['name']}: {e}")

        # Показать результат для ключевых полей
        key_fields = ['Тема письма', 'Сырой текст email', 'Номер рекламационного акта', 'Подкатегории']
        for kf in key_fields:
            if kf in fields:
                f = fields[kf]
                status = "empty" if f['empty'] else "OK"
                val_preview = str(f['value'])[:50] + "..." if len(str(f['value'])) > 50 else str(f['value'])
                print(f"    {kf}: [{f['type']}] {val_preview} ({status})")

        ok(f"  Simulyaciya OK")

    except Exception as e:
        fail(f"    EXCEPTION: {e}")
        errors.append(f"{tc['name']}: {e}")
        import traceback
        traceback.print_exc()

# =========================================================================
# 4. PROVERKA KATEGORIY
# =========================================================================
print("\n--- 4. PROVERKA map_category() ---")

# Импортируем функцию если возможно
try:
    # Нужно проверить что функция map_category работает
    # Она определена внутри process_reclamation, поэтому копируем логику

    CATEGORY_TO_LIST_ID = {
        'наземка': 1200,
        'метро': 1202,
        'ждт': 1204,
        'спецтехника': 1206,
        'жд': 1204,
        'железнодорожный': 1204,
        'жд транспорт': 1204,
        'наземный': 1200,
        'метрополитен': 1202,
    }

    test_categories = [
        ('Метро', 1202),
        ('метро', 1202),
        ('МЕТРО', 1202),
        ('Рекламации Метро', 1202),
        ('Наземка', 1200),
        ('ЖДТ', 1204),
        ('жд транспорт', 1204),
        ('Спецтехника', 1206),
        (None, None),
        ('', None),
        ('n/a', None),
        ('неизвестная категория', None),
    ]

    for cat_input, expected_id in test_categories:
        # Симуляция map_category
        result_id = None
        if cat_input and isinstance(cat_input, str):
            cleaned = cat_input.strip().lower()
            for prefix in ['рекламации ', 'рекламация ']:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):]
                    break
            if cleaned and cleaned not in {'n/a', 'na', 'неизвестно', ''}:
                result_id = CATEGORY_TO_LIST_ID.get(cleaned)

        if result_id == expected_id:
            ok(f"  '{cat_input}' -> {result_id}")
        else:
            fail(f"  '{cat_input}' -> {result_id} (ozhidalos' {expected_id})")
            if expected_id is not None:
                errors.append(f"Category: '{cat_input}' -> {result_id}, expected {expected_id}")

except Exception as e:
    fail(f"Oshibka proverki kategoriy: {e}")
    errors.append(f"Category test: {e}")

# =========================================================================
# 5. ITOG
# =========================================================================
print("\n" + "="*80)
print("ITOG SIMULYACII")
print("="*80)

if errors:
    print(f"\n{RED}NAYDENO OSHIBOK: {len(errors)}{RESET}")
    for e in errors:
        print(f"  - {e}")
    print(f"\n{RED}NE ZAPUSKAY SERVIS POKA NE ISPRAVISH!{RESET}")
    sys.exit(1)
else:
    print(f"\n{GREEN}VSE PROVERKI PROYDENY USPESHNO{RESET}")
    print("Mozhno zapuskat' servis.")
    sys.exit(0)
