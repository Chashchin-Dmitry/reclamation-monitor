"""
FastAPI дашборд для системы рекламаций ЭПОТОС.

Запуск:
    python dashboard_api.py
    → http://localhost:8080

Endpoints:
    GET  /                  → HTML дашборд
    GET  /api/stats         → статистика
    GET  /api/reclamations  → список рекламаций
    GET  /api/emails        → список писем (+ ?search=)
    GET  /api/email/{id}    → детальная карточка письма
    GET  /api/runs          → история запусков
    GET  /api/tasks         → задачи
    POST /api/process       → запустить обработку за дату
    POST /api/reprocess     → переобработать письмо
    GET  /api/settings      → все настройки
    POST /api/settings      → сохранить настройку
    GET  /api/status        → статус сервисов
    WS   /ws/logs           → WebSocket live логов
"""
import os
import sys
import json
import time
import asyncio
import logging
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import database as db

# Корневая папка проекта
PROJECT_DIR = Path(__file__).parent

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

app = FastAPI(title="ЭПОТОС Рекламации", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connections для live логов
ws_clients: list[WebSocket] = []


# ========== BROADCAST LOG ==========

def _decode_log_bytes(raw: bytes) -> str:
    """Декодирует строку лога: UTF-8 → cp1251 → replace."""
    try:
        return raw.decode('utf-8')
    except (UnicodeDecodeError, AttributeError):
        try:
            return raw.decode('cp1251')
        except (UnicodeDecodeError, AttributeError):
            return raw.decode('utf-8', errors='replace')


async def broadcast_log(message: str):
    """Отправить сообщение всем WebSocket клиентам."""
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ws_clients.remove(ws)


def broadcast_sync(message: str):
    """Синхронная обёртка для broadcast из другого потока."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(broadcast_log(message))
        else:
            loop.run_until_complete(broadcast_log(message))
    except RuntimeError:
        pass


# ========== HTML ==========

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Отдать HTML дашборд."""
    html_path = PROJECT_DIR / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding='utf-8'))
    return HTMLResponse("<h1>dashboard.html не найден</h1>")


# ========== API ==========

@app.get("/api/status")
async def api_status():
    """Статус сервисов."""
    import requests

    # Ollama
    ollama_ok = False
    ollama_model = None
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            ollama_ok = True
            models = r.json().get('models', [])
            ollama_model = models[0]['name'] if models else 'unknown'
    except Exception:
        pass

    # NSSM сервис
    service_status = "unknown"
    try:
        nssm_path = PROJECT_DIR / "service" / "nssm.exe"
        if nssm_path.exists():
            result = subprocess.run(
                [str(nssm_path), "status", "ReclamationMonitor"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                # NSSM отдаёт stdout в UTF-16LE (null bytes между символами)
                try:
                    raw = result.stdout.decode('utf-16-le').strip()
                except Exception:
                    raw = result.stdout.decode('utf-8', errors='replace').replace('\x00', '').strip()
                service_status = raw.split('\n')[0].strip()
            else:
                service_status = "NOT_INSTALLED"
        else:
            service_status = "NSSM_NOT_FOUND"
    except Exception as e:
        service_status = f"error: {e}"

    # Последний запуск
    runs = db.get_runs(limit=1)
    last_run = runs[0] if runs else None

    return {
        "ollama": {"ok": ollama_ok, "model": ollama_model},
        "service": {"status": service_status},
        "last_run": last_run,
        "db_path": str(db.DB_PATH),
        "db_exists": db.DB_PATH.exists(),
    }


@app.get("/api/stats")
async def api_stats(days: int = Query(default=7, ge=1, le=365)):
    """Статистика за N дней."""
    return db.get_stats(days)


@app.get("/api/reclamations")
async def api_reclamations(
    category: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0)
):
    """Список рекламаций с фильтрами."""
    items = db.get_reclamations(category, limit, offset, date_from, date_to)
    total = db.get_reclamation_count()
    return {"items": items, "total": total}


@app.get("/api/emails")
async def api_emails(
    run_date: Optional[str] = None,
    search: Optional[str] = None,
    is_reclamation: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0)
):
    """Список обработанных писем с поиском."""
    if search is not None or is_reclamation is not None:
        return db.search_emails(query=search, limit=limit, offset=offset,
                                is_reclamation=is_reclamation)
    items = db.get_emails(run_date, limit, offset)
    total = db.get_connection().execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    return {"items": items, "total": total}


@app.get("/api/email/{email_id}")
async def api_email_detail(email_id: str):
    """Детальная карточка письма."""
    detail = db.get_email_detail(email_id)
    if not detail:
        return JSONResponse({"error": "Email not found"}, status_code=404)

    # Парсим JSON-поля
    result = dict(detail)
    for json_field in ('attachments_json', 'llama_result_json'):
        raw = result.get(json_field)
        if raw:
            try:
                result[json_field + '_parsed'] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result[json_field + '_parsed'] = None

    return result


@app.get("/api/runs")
async def api_runs(limit: int = Query(default=20, ge=1, le=100)):
    """История запусков."""
    return db.get_runs(limit)


@app.get("/api/tasks")
async def api_tasks():
    """Список задач."""
    return db.get_tasks()


@app.post("/api/process")
async def api_process(body: dict):
    """Запустить обработку за дату."""
    target_date = body.get("date", "")
    if not target_date:
        return JSONResponse({"error": "date required"}, status_code=400)

    # Проверяем что нет running задач
    tasks = db.get_tasks(limit=5)
    for t in tasks:
        if t['status'] == 'running':
            return JSONResponse({"error": "Уже выполняется обработка"}, status_code=409)

    # Создаём задачу
    task_id = db.create_task(target_date)

    # Запускаем в фоновом потоке
    thread = threading.Thread(
        target=_run_processing,
        args=(task_id, target_date),
        daemon=True
    )
    thread.start()

    return {"task_id": task_id, "status": "started", "date": target_date}


def _run_processing(task_id: int, target_date: str):
    """Фоновая обработка почты за дату."""
    try:
        db.update_task(task_id, status='running',
                       started_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        broadcast_sync(json.dumps({
            "type": "task_start",
            "task_id": task_id,
            "date": target_date,
            "time": datetime.now().strftime('%H:%M:%S')
        }))

        # Импортируем процессор
        from email_processor_improved import EmailProcessor

        processor = EmailProcessor()
        processor.connect()

        try:
            # Запускаем обработку
            results = processor.run(date_str=target_date)

            total = results.get('total_emails', 0) if isinstance(results, dict) else 0
            recl = results.get('reclamations_found', 0) if isinstance(results, dict) else 0

            db.update_task(task_id, status='done',
                           finished_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                           total_emails=total,
                           reclamations_found=recl)

            broadcast_sync(json.dumps({
                "type": "task_done",
                "task_id": task_id,
                "total": total,
                "reclamations": recl,
                "time": datetime.now().strftime('%H:%M:%S')
            }))

        finally:
            try:
                processor.disconnect()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        db.update_task(task_id, status='error',
                       finished_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                       error=str(e))
        broadcast_sync(json.dumps({
            "type": "task_error",
            "task_id": task_id,
            "error": str(e),
            "time": datetime.now().strftime('%H:%M:%S')
        }))


# ========== SETTINGS ==========

@app.get("/api/settings")
async def api_get_settings():
    """Все настройки из БД + конфиг-файлы."""
    settings = db.get_all_settings()

    # Если промпт ещё не в БД — подгружаем из кода
    if 'llama_prompt' not in settings:
        try:
            from llama_api_module import SINGLE_DOCUMENT_PROMPT
            settings['llama_prompt'] = SINGLE_DOCUMENT_PROMPT
        except Exception:
            settings['llama_prompt'] = ''

    if 'llama_system_prompt' not in settings:
        settings['llama_system_prompt'] = "Ты - специалист по анализу документов. Правила:\n1. Один физический продукт = одна запись в products\n2. Если продукт упоминается несколько раз — объедини в одну запись\n3. Категория: определи по контексту (Наземка/Метро/Спецтехника/ЖДТ)"

    # Подгружаем classifier_config.json
    config_path = PROJECT_DIR / "classifier_config.json"
    classifier_config = {}
    if config_path.exists():
        try:
            classifier_config = json.loads(config_path.read_text(encoding='utf-8'))
        except Exception:
            pass

    # Подгружаем bitrix_field_mapping.json
    mapping_path = PROJECT_DIR / "bitrix_field_mapping.json"
    field_mapping = {}
    if mapping_path.exists():
        try:
            field_mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
        except Exception:
            pass

    return {
        "settings": settings,
        "classifier_config": classifier_config,
        "field_mapping": field_mapping,
    }


@app.post("/api/settings")
async def api_set_setting(body: dict):
    """Сохранить настройку {key, value}."""
    key = body.get("key", "")
    value = body.get("value", "")
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)

    # Специальная обработка: classifier_config пишем в файл
    if key == "classifier_config":
        try:
            config_path = PROJECT_DIR / "classifier_config.json"
            parsed = json.loads(value) if isinstance(value, str) else value
            config_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding='utf-8')
            return {"ok": True, "key": key, "target": "file"}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    db.set_setting(key, value)
    return {"ok": True, "key": key}


# ========== REPROCESS ==========

@app.post("/api/reprocess")
async def api_reprocess(body: dict):
    """Переобработать письмо по email_id."""
    email_id = body.get("email_id", "")
    if not email_id:
        return JSONResponse({"error": "email_id required"}, status_code=400)

    # Удаляем из processed_reclamations.json чтобы не скипнулось
    cache_path = PROJECT_DIR / "processed_reclamations.json"
    try:
        if cache_path.exists():
            cache = json.loads(cache_path.read_text(encoding='utf-8'))
            pids = cache.get("processed_ids", [])
            # Удаляем все вхождения этого email_id
            cache["processed_ids"] = [p for p in pids if not (p == str(email_id) or p.startswith(f"{email_id}_"))]
            if email_id in cache.get("processed_details", {}):
                del cache["processed_details"][email_id]
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        logger.warning(f"Не удалось очистить кэш для {email_id}: {e}")

    # Запускаем переобработку в фоновом потоке
    thread = threading.Thread(
        target=_run_reprocess,
        args=(email_id,),
        daemon=True
    )
    thread.start()

    return {"ok": True, "email_id": email_id, "status": "started"}


def _run_reprocess(email_id: str):
    """Фоновая переобработка одного письма."""
    try:
        broadcast_sync(json.dumps({
            "type": "reprocess_start",
            "email_id": email_id,
            "time": datetime.now().strftime('%H:%M:%S')
        }))

        from email_processor_improved import EmailProcessor
        processor = EmailProcessor()

        if not processor.connect():
            broadcast_sync(json.dumps({
                "type": "reprocess_error",
                "email_id": email_id,
                "error": "Не удалось подключиться к почтовому серверу"
            }))
            return

        try:
            result = processor.process_email_parallel(email_id)
            broadcast_sync(json.dumps({
                "type": "reprocess_done",
                "email_id": email_id,
                "is_reclamation": result.get("is_reclamation", False),
                "time": datetime.now().strftime('%H:%M:%S')
            }))
        finally:
            try:
                processor.disconnect()
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Ошибка переобработки {email_id}: {e}")
        broadcast_sync(json.dumps({
            "type": "reprocess_error",
            "email_id": email_id,
            "error": str(e)
        }))


# ========== WEBSOCKET ==========

@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    """WebSocket для live логов."""
    await websocket.accept()
    ws_clients.append(websocket)
    logger.info(f"[WS] Клиент подключен ({len(ws_clients)} всего)")

    try:
        # Отправляем последние строки лога при подключении
        log_file = PROJECT_DIR / "logs" / "service_stderr.log"
        if log_file.exists():
            try:
                with open(log_file, 'rb') as f:
                    raw_lines = f.readlines()
                    last_lines = raw_lines[-30:] if len(raw_lines) > 30 else raw_lines
                    for raw in last_lines:
                        text = _decode_log_bytes(raw).rstrip()
                        await websocket.send_text(json.dumps({
                            "type": "log",
                            "text": text,
                            "historical": True
                        }))
            except Exception:
                pass

        # Следим за новыми строками лога
        await _tail_log(websocket, log_file)

    except WebSocketDisconnect:
        pass
    finally:
        if websocket in ws_clients:
            ws_clients.remove(websocket)
        logger.info(f"[WS] Клиент отключен ({len(ws_clients)} осталось)")


async def _tail_log(websocket: WebSocket, log_file: Path):
    """Отслеживать новые строки в файле лога."""
    if not log_file.exists():
        # Ждём пока файл появится
        while not log_file.exists():
            await asyncio.sleep(2)
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                return

    with open(log_file, 'rb') as f:
        # Переходим в конец файла
        f.seek(0, 2)

        while True:
            raw = f.readline()
            if raw:
                text = _decode_log_bytes(raw).rstrip()
                await websocket.send_text(json.dumps({
                    "type": "log",
                    "text": text
                }))
            else:
                await asyncio.sleep(0.5)
                # Ping для проверки соединения
                try:
                    await websocket.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    return


# ========== MAIN ==========

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    logger.info(f"Запуск дашборда на http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
