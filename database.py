"""
SQLite база данных для системы рекламаций ЭПОТОС.

Таблицы:
- emails: все обработанные письма
- reclamations: рекламации (is_reclamation=True)
- processing_runs: логи запусков обработки
- tasks: задачи на обработку (из UI)
- settings: настройки системы (промпты, blacklist, маппинг)
"""
import sqlite3
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from contextlib import contextmanager

logger = logging.getLogger("ReclamationDB")

DB_PATH = Path(__file__).parent / 'reclamations.db'

# Thread-local storage для connections
_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Получить connection для текущего потока."""
    if not hasattr(_local, 'connection') or _local.connection is None:
        _local.connection = sqlite3.connect(str(DB_PATH), timeout=10)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA foreign_keys=ON")
    return _local.connection


@contextmanager
def get_db():
    """Context manager для транзакций."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """Создать таблицы если не существуют."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT NOT NULL,
                subject TEXT,
                sender TEXT,
                received_date TEXT,
                is_reclamation INTEGER DEFAULT 0,
                is_blacklisted INTEGER DEFAULT 0,
                category TEXT,
                processing_time REAL,
                processed_at TEXT DEFAULT (datetime('now', 'localtime')),
                error TEXT,
                run_date TEXT,
                UNIQUE(email_id, run_date)
            );

            CREATE TABLE IF NOT EXISTS reclamations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_id TEXT NOT NULL,
                bitrix_id INTEGER,
                product_name TEXT,
                serial_number TEXT,
                category TEXT,
                severity TEXT,
                customer_name TEXT,
                issue_description TEXT,
                dealer_name TEXT,
                contact_person TEXT,
                act_number TEXT,
                products_json TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS processing_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                total_emails INTEGER DEFAULT 0,
                reclamations_found INTEGER DEFAULT 0,
                blacklisted INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0,
                duration REAL,
                started_at TEXT DEFAULT (datetime('now', 'localtime')),
                finished_at TEXT,
                status TEXT DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_date TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                started_at TEXT,
                finished_at TEXT,
                total_emails INTEGER,
                reclamations_found INTEGER,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_emails_run_date ON emails(run_date);
            CREATE INDEX IF NOT EXISTS idx_emails_email_id ON emails(email_id);
            CREATE INDEX IF NOT EXISTS idx_reclamations_email ON reclamations(email_id);
            CREATE INDEX IF NOT EXISTS idx_reclamations_category ON reclamations(category);
            CREATE INDEX IF NOT EXISTS idx_runs_date ON processing_runs(run_date);
        """)

        # Миграция: добавляем новые колонки в emails (ALTER TABLE — безопасно для существующих данных)
        _migrate_emails_columns(conn)

    logger.info(f"[DB] База инициализирована: {DB_PATH}")


def _migrate_emails_columns(conn):
    """Добавляет новые колонки в emails если их нет."""
    new_columns = [
        ("body_text", "TEXT"),
        ("attachments_json", "TEXT"),
        ("llama_result_json", "TEXT"),
        ("cloud_links", "TEXT"),
        ("processing_log", "TEXT"),
    ]
    # Получаем текущие колонки
    existing = {row[1] for row in conn.execute("PRAGMA table_info(emails)").fetchall()}
    for col_name, col_type in new_columns:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE emails ADD COLUMN {col_name} {col_type}")
            logger.info(f"[DB] Миграция: добавлена колонка emails.{col_name}")


# ========== EMAILS ==========

def save_email(email_id: str, subject: str, sender: str, received_date: str,
               is_reclamation: bool, is_blacklisted: bool, category: str,
               processing_time: float, run_date: str, error: str = None,
               body_text: str = None, attachments_json: str = None,
               llama_result_json: str = None, cloud_links: str = None,
               processing_log: str = None):
    """Сохранить обработанное письмо."""
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO emails
                (email_id, subject, sender, received_date, is_reclamation,
                 is_blacklisted, category, processing_time, run_date, error,
                 body_text, attachments_json, llama_result_json, cloud_links, processing_log)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(email_id), subject, sender, received_date,
                  1 if is_reclamation else 0,
                  1 if is_blacklisted else 0,
                  category, processing_time, run_date, error,
                  body_text, attachments_json, llama_result_json,
                  cloud_links, processing_log))
    except Exception as e:
        logger.error(f"[DB] Ошибка сохранения email {email_id}: {e}")


def get_emails(run_date: str = None, limit: int = 100, offset: int = 0) -> List[Dict]:
    """Получить список обработанных писем."""
    conn = get_connection()
    if run_date:
        rows = conn.execute(
            "SELECT * FROM emails WHERE run_date=? ORDER BY processed_at DESC LIMIT ? OFFSET ?",
            (run_date, limit, offset)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM emails ORDER BY processed_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
    return [dict(r) for r in rows]


def get_email_detail(email_id: str) -> Optional[Dict]:
    """Получить полную карточку письма по email_id."""
    conn = get_connection()
    eid = str(email_id).strip()

    row = conn.execute(
        "SELECT * FROM emails WHERE email_id=? ORDER BY processed_at DESC LIMIT 1",
        (eid,)
    ).fetchone()

    if row:
        email_data = dict(row)
    else:
        # Может быть только в reclamations (старые данные до миграции)
        recl_rows = conn.execute(
            "SELECT * FROM reclamations WHERE email_id=? ORDER BY id", (eid,)
        ).fetchall()
        if not recl_rows:
            return None
        first = dict(recl_rows[0])
        email_data = {
            'email_id': eid,
            'subject': f"(рекламация #{first.get('bitrix_id', '?')})",
            'sender': first.get('customer_name', ''),
            'received_date': first.get('created_at', ''),
            'is_reclamation': 1,
            'is_blacklisted': 0,
            'category': first.get('category', ''),
            'processing_time': None,
            'body_text': None, 'attachments_json': None,
            'llama_result_json': None, 'cloud_links': None,
            'processing_log': None,
            'reclamations': [dict(r) for r in recl_rows],
        }
        return email_data

    # Подтягиваем рекламации для этого email
    reclamations = conn.execute(
        "SELECT * FROM reclamations WHERE email_id=? ORDER BY id",
        (eid,)
    ).fetchall()
    email_data['reclamations'] = [dict(r) for r in reclamations]

    return email_data


def search_emails(query: str = None, limit: int = 50, offset: int = 0,
                  is_reclamation: Optional[bool] = None) -> Dict[str, Any]:
    """Поиск по всем письмам (subject, sender, body_text LIKE)."""
    conn = get_connection()
    where_parts = ["1=1"]
    params = []

    if query:
        where_parts.append("(subject LIKE ? OR sender LIKE ? OR body_text LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])

    if is_reclamation is not None:
        where_parts.append("is_reclamation=?")
        params.append(1 if is_reclamation else 0)

    where_clause = " AND ".join(where_parts)

    total = conn.execute(
        f"SELECT COUNT(*) FROM emails WHERE {where_clause}", params
    ).fetchone()[0]

    rows = conn.execute(
        f"SELECT * FROM emails WHERE {where_clause} ORDER BY processed_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()

    return {"items": [dict(r) for r in rows], "total": total}


# ========== SETTINGS ==========

def get_setting(key: str) -> Optional[str]:
    """Получить значение настройки по ключу."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_setting(key: str, value: str):
    """Сохранить настройку (INSERT OR REPLACE)."""
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now', 'localtime'))
        """, (key, value))


def get_all_settings() -> Dict[str, str]:
    """Получить все настройки."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {row[0]: row[1] for row in rows}


# ========== RECLAMATIONS ==========

def save_reclamation(email_id: str, bitrix_id: int, product_name: str,
                     serial_number: str, category: str, severity: str,
                     customer_name: str, issue_description: str,
                     dealer_name: str = None, contact_person: str = None,
                     act_number: str = None, products_json: str = None):
    """Сохранить рекламацию."""
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO reclamations
                (email_id, bitrix_id, product_name, serial_number, category,
                 severity, customer_name, issue_description, dealer_name,
                 contact_person, act_number, products_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(email_id), bitrix_id, product_name, serial_number,
                  category, severity, customer_name, issue_description,
                  dealer_name, contact_person, act_number, products_json))
    except Exception as e:
        logger.error(f"[DB] Ошибка сохранения рекламации {email_id}: {e}")


def get_reclamations(category: str = None, limit: int = 100, offset: int = 0,
                     date_from: str = None, date_to: str = None) -> List[Dict]:
    """Получить список рекламаций с фильтрами."""
    conn = get_connection()
    query = "SELECT * FROM reclamations WHERE 1=1"
    params = []

    if category:
        query += " AND (category=? OR category LIKE ?)"
        params.extend([category, f'%{category}%'])
    if date_from:
        query += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        query += " AND created_at <= ?"
        params.append(date_to + " 23:59:59")

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_reclamation_count() -> int:
    conn = get_connection()
    return conn.execute("SELECT COUNT(*) FROM reclamations").fetchone()[0]


# ========== PROCESSING RUNS ==========

def start_run(run_date: str) -> int:
    """Начать запуск обработки, вернуть ID."""
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO processing_runs (run_date) VALUES (?)", (run_date,))
        return cursor.lastrowid


def finish_run(run_id: int, total_emails: int, reclamations_found: int,
               blacklisted: int, errors: int, skipped: int, duration: float):
    """Завершить запуск обработки."""
    with get_db() as conn:
        conn.execute("""
            UPDATE processing_runs SET
                total_emails=?, reclamations_found=?, blacklisted=?,
                errors=?, skipped=?, duration=?,
                finished_at=datetime('now', 'localtime'), status='done'
            WHERE id=?
        """, (total_emails, reclamations_found, blacklisted, errors,
              skipped, duration, run_id))


def fail_run(run_id: int, error: str):
    """Пометить запуск как провалившийся."""
    with get_db() as conn:
        conn.execute("""
            UPDATE processing_runs SET
                finished_at=datetime('now', 'localtime'), status='error'
            WHERE id=?
        """, (run_id,))


def get_runs(limit: int = 20) -> List[Dict]:
    """Получить историю запусков."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM processing_runs ORDER BY started_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ========== TASKS ==========

def create_task(target_date: str) -> int:
    """Создать задачу на обработку."""
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO tasks (target_date) VALUES (?)", (target_date,))
        return cursor.lastrowid


def get_pending_task() -> Optional[Dict]:
    """Получить следующую задачу для выполнения."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def update_task(task_id: int, **kwargs):
    """Обновить задачу."""
    valid_fields = {'status', 'started_at', 'finished_at', 'total_emails',
                    'reclamations_found', 'error'}
    fields = {k: v for k, v in kwargs.items() if k in valid_fields}
    if not fields:
        return
    set_clause = ', '.join(f"{k}=?" for k in fields)
    with get_db() as conn:
        conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id=?",
            list(fields.values()) + [task_id]
        )


def get_tasks(limit: int = 20) -> List[Dict]:
    """Получить список задач."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ========== STATS ==========

def get_stats(days: int = 7) -> Dict[str, Any]:
    """Статистика за N дней."""
    conn = get_connection()

    total_emails = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE processed_at >= datetime('now', 'localtime', ?)",
        (f'-{days} days',)
    ).fetchone()[0]

    total_reclamations = conn.execute(
        "SELECT COUNT(*) FROM reclamations WHERE created_at >= datetime('now', 'localtime', ?)",
        (f'-{days} days',)
    ).fetchone()[0]

    total_blacklisted = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE is_blacklisted=1 AND processed_at >= datetime('now', 'localtime', ?)",
        (f'-{days} days',)
    ).fetchone()[0]

    total_errors = conn.execute(
        "SELECT COUNT(*) FROM emails WHERE error IS NOT NULL AND processed_at >= datetime('now', 'localtime', ?)",
        (f'-{days} days',)
    ).fetchone()[0]

    # По категориям
    categories = conn.execute("""
        SELECT category, COUNT(*) as cnt FROM reclamations
        WHERE created_at >= datetime('now', 'localtime', ?)
        AND category IS NOT NULL AND category != '' AND category != 'Неизвестно'
        GROUP BY category ORDER BY cnt DESC
    """, (f'-{days} days',)).fetchall()

    # По дням
    daily = conn.execute("""
        SELECT DATE(processed_at) as day, COUNT(*) as total,
               SUM(is_reclamation) as recl, SUM(is_blacklisted) as bl
        FROM emails
        WHERE processed_at >= datetime('now', 'localtime', ?)
        GROUP BY DATE(processed_at) ORDER BY day
    """, (f'-{days} days',)).fetchall()

    return {
        'days': days,
        'total_emails': total_emails,
        'total_reclamations': total_reclamations,
        'total_blacklisted': total_blacklisted,
        'total_errors': total_errors,
        'categories': [dict(r) for r in categories],
        'daily': [dict(r) for r in daily],
        'all_time_reclamations': conn.execute("SELECT COUNT(*) FROM reclamations").fetchone()[0],
        'all_time_emails': conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0],
    }


# Инициализация при импорте
init_db()
