"""
OCR-модуль для распознавания текста из PDF-сканов и изображений.

Используется как fallback когда PyMuPDF возвращает 0 символов
(PDF содержит изображения без текстового слоя).

Цепочка: PyMuPDF (0 символов) → OCR baseline → OCR + предобработка

v2.0 (2026-02-13): Параллельная обработка + поддержка изображений (JPEG/PNG)
"""

import os
import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger("OCRProcessor")

# Максимальное количество параллельных OCR потоков
# Ryzen 9 7900X имеет 24 потока, используем половину для OCR
MAX_OCR_WORKERS = 12

# Максимальный размер файла для OCR (100MB)
MAX_FILE_SIZE_MB = 100


@dataclass
class OCRResult:
    """Результат OCR для одного файла"""
    filename: str
    text: str
    chars_count: int
    ocr_quality: str  # "good", "partial", "failed"
    error: Optional[str] = None

# Путь к Tesseract (важно для Windows Service — PATH может отличаться)
TESSERACT_CMD = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


def extract_text_from_scanned_pdf(pdf_path: str) -> str:
    """
    Извлекает текст из PDF-скана через OCR.

    Уровень 1: Tesseract baseline (без предобработки)
    Уровень 2: Tesseract + предобработка (если baseline < 50 символов)

    Args:
        pdf_path: Путь к PDF файлу

    Returns:
        Распознанный текст или пустая строка при ошибке
    """
    try:
        import pytesseract
        import fitz
    except ImportError as e:
        logger.warning(f"OCR недоступен (не установлены библиотеки): {e}")
        return ""

    # Указываем путь к Tesseract
    if os.path.exists(TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    logger.info(f"PDF скан, используется OCR: {os.path.basename(pdf_path)}")

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        logger.error(f"Не удалось открыть PDF для OCR: {e}")
        return ""

    # Уровень 1: baseline OCR
    text = _ocr_all_pages(doc, preprocess=False)

    if len(text.strip()) < 50:
        # Уровень 2: OCR с предобработкой
        logger.info(f"Baseline OCR дал мало текста ({len(text.strip())} символов), пробуем с предобработкой")
        text_preprocessed = _ocr_all_pages(doc, preprocess=True)
        if len(text_preprocessed.strip()) > len(text.strip()):
            text = text_preprocessed

    doc.close()
    logger.info(f"OCR извлёк {len(text.strip())} символов из {os.path.basename(pdf_path)}")
    return text


def _ocr_all_pages(doc, preprocess: bool = False) -> str:
    """Распознаёт текст со всех страниц PDF через OCR.

    Метод 1: Рендерим всю страницу как изображение (get_pixmap) - надёжнее
    Метод 2: Извлекаем отдельные изображения (get_images) - fallback
    """
    import pytesseract
    from PIL import Image
    import fitz

    all_text = []

    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        page_text = ""

        # Метод 1: Рендерим всю страницу как изображение (более надёжный)
        try:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes('png')))

            if preprocess:
                img = _preprocess_image(img)

            page_text = pytesseract.image_to_string(img, lang='rus', config='--oem 3 --psm 6')
            logger.debug(f"Страница {page_num + 1}: get_pixmap OCR извлёк {len(page_text)} символов")
        except Exception as e:
            logger.warning(f"Ошибка get_pixmap OCR страница {page_num + 1}: {e}")

        # Метод 2: Если рендер страницы дал мало текста, пробуем извлечь отдельные изображения
        if len(page_text.strip()) < 100:
            images = page.get_images()
            images_text = []

            for img_info in images:
                try:
                    xref = img_info[0]
                    pix = fitz.Pixmap(doc, xref)
                    if pix.n > 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)

                    img = Image.open(io.BytesIO(pix.tobytes('png')))

                    if preprocess:
                        img = _preprocess_image(img)

                    text = pytesseract.image_to_string(img, lang='rus', config='--oem 3 --psm 6')
                    images_text.append(text)
                except Exception as e:
                    logger.warning(f"Ошибка OCR изображения страница {page_num + 1}: {e}")

            images_combined = "\n".join(images_text)
            if len(images_combined.strip()) > len(page_text.strip()):
                page_text = images_combined
                logger.debug(f"Страница {page_num + 1}: get_images OCR извлёк {len(page_text)} символов")

        all_text.append(page_text)

    return "\n".join(all_text)


def _preprocess_image(pil_image):
    """Предобработка для тяжёлых случаев (низкий контраст, цветной текст)."""
    try:
        import cv2
        import numpy as np
        from PIL import Image

        img_np = np.array(pil_image)

        # Проверяем формат изображения
        if len(img_np.shape) == 2:
            # Grayscale изображение - просто используем его
            gray = img_np
        elif len(img_np.shape) == 3 and img_np.shape[2] == 3:
            # RGB изображение
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
            b, g, r = cv2.split(img_np)
            gray = cv2.addWeighted(b, 0.7, g, 0.2, 0)
            gray = cv2.addWeighted(gray, 1.0, r, 0.1, 0)
        elif len(img_np.shape) == 3 and img_np.shape[2] == 4:
            # RGBA изображение
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2BGR)
            b, g, r = cv2.split(img_np)
            gray = cv2.addWeighted(b, 0.7, g, 0.2, 0)
            gray = cv2.addWeighted(gray, 1.0, r, 0.1, 0)
        else:
            # Неизвестный формат - возвращаем как есть
            return pil_image

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        return Image.fromarray(binary)
    except ImportError:
        logger.warning("opencv-python не установлен, предобработка пропущена")
        return pil_image
    except Exception as e:
        logger.warning(f"Ошибка предобработки: {e}, возвращаем оригинал")
        return pil_image
        gray = cv2.addWeighted(b, 0.7, g, 0.2, 0)
        gray = cv2.addWeighted(gray, 1.0, r, 0.1, 0)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        return Image.fromarray(binary)
    except ImportError:
        logger.warning("opencv-python не установлен, предобработка пропущена")
        return pil_image


def extract_text_from_image(image_path: str) -> OCRResult:
    """
    Извлекает текст из изображения (JPEG, PNG, BMP, TIFF) через OCR.

    Args:
        image_path: Путь к файлу изображения

    Returns:
        OCRResult с извлечённым текстом
    """
    filename = os.path.basename(image_path)

    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        logger.warning(f"OCR недоступен (не установлены библиотеки): {e}")
        return OCRResult(filename=filename, text="", chars_count=0,
                        ocr_quality="failed", error=str(e))

    # Указываем путь к Tesseract
    if os.path.exists(TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    # Проверяем размер файла
    file_size_mb = os.path.getsize(image_path) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        logger.warning(f"[OCR_SKIP] Файл слишком большой: {filename} ({file_size_mb:.1f}MB)")
        return OCRResult(filename=filename, text="[FILE TOO LARGE]", chars_count=0,
                        ocr_quality="failed", error="too_large")

    logger.info(f"OCR изображения: {filename}")

    try:
        image = Image.open(image_path)

        # Конвертируем в RGB если нужно (для RGBA, P и др.)
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')

        # Уровень 1: baseline OCR
        text = pytesseract.image_to_string(image, lang='rus', config='--oem 3 --psm 6')

        if len(text.strip()) < 50:
            # Уровень 2: OCR с предобработкой
            logger.info(f"Baseline OCR дал мало текста ({len(text.strip())} символов), пробуем с предобработкой")
            preprocessed = _preprocess_image(image)
            text_preprocessed = pytesseract.image_to_string(preprocessed, lang='rus', config='--oem 3 --psm 6')
            if len(text_preprocessed.strip()) > len(text.strip()):
                text = text_preprocessed

        chars = len(text.strip())
        quality = "good" if chars > 100 else ("partial" if chars > 0 else "failed")

        logger.info(f"OCR извлёк {chars} символов из {filename}")
        return OCRResult(filename=filename, text=text, chars_count=chars, ocr_quality=quality)

    except Exception as e:
        logger.error(f"Ошибка OCR изображения {filename}: {e}")
        return OCRResult(filename=filename, text="", chars_count=0,
                        ocr_quality="failed", error=str(e))


def process_attachment_ocr(attachment: Dict[str, Any]) -> OCRResult:
    """
    Обрабатывает одно вложение: извлекает текст из PDF или изображения.
    Используется в ThreadPoolExecutor для параллельной обработки.

    Args:
        attachment: Словарь с данными вложения {'path': str, 'content_type': str, ...}

    Returns:
        OCRResult с извлечённым текстом
    """
    path = attachment.get('path', '')
    filename = attachment.get('filename', os.path.basename(path))
    content_type = attachment.get('content_type', '').lower()

    if not path or not os.path.exists(path):
        return OCRResult(filename=filename, text="", chars_count=0,
                        ocr_quality="failed", error="file_not_found")

    # Определяем тип файла
    ext = os.path.splitext(path)[1].lower()

    # PDF файлы
    if ext == '.pdf' or 'pdf' in content_type:
        try:
            import fitz
            doc = fitz.open(path)

            # Сначала пробуем извлечь текст напрямую
            text = ""
            for page in doc:
                text += page.get_text()

            if len(text.strip()) > 100:
                # Текстовый PDF, OCR не нужен
                doc.close()
                return OCRResult(filename=filename, text=text, chars_count=len(text.strip()),
                                ocr_quality="good")

            # PDF-скан, нужен OCR
            doc.close()
            text = extract_text_from_scanned_pdf(path)
            chars = len(text.strip())
            quality = "good" if chars > 100 else ("partial" if chars > 0 else "failed")
            return OCRResult(filename=filename, text=text, chars_count=chars, ocr_quality=quality)

        except Exception as e:
            logger.error(f"Ошибка обработки PDF {filename}: {e}")
            return OCRResult(filename=filename, text="", chars_count=0,
                            ocr_quality="failed", error=str(e))

    # Изображения
    elif ext in ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.gif') or \
         any(t in content_type for t in ('image/', 'jpeg', 'png', 'bmp', 'tiff', 'gif')):
        return extract_text_from_image(path)

    # DOCX, XLSX и другие - не нужен OCR, возвращаем пустой результат
    # (текст извлекается в reclamation_classifier.py)
    else:
        return OCRResult(filename=filename, text="", chars_count=0,
                        ocr_quality="skipped", error="not_ocr_type")


def process_attachments_parallel(attachments: List[Dict[str, Any]],
                                  max_workers: int = MAX_OCR_WORKERS) -> List[OCRResult]:
    """
    Параллельно обрабатывает все вложения через OCR.

    Использует ThreadPoolExecutor с max_workers потоками.

    Args:
        attachments: Список вложений с путями к файлам
        max_workers: Максимальное количество параллельных потоков

    Returns:
        Список OCRResult для каждого вложения
    """
    if not attachments:
        return []

    results = []
    total = len(attachments)

    logger.info(f"[PARALLEL_OCR] Начинаем обработку {total} вложений в {max_workers} потоках")

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Запускаем все задачи
            future_to_attachment = {
                executor.submit(process_attachment_ocr, att): att
                for att in attachments
            }

            # Собираем результаты по мере готовности
            for future in as_completed(future_to_attachment, timeout=120):
                attachment = future_to_attachment[future]
                filename = attachment.get('filename', 'unknown')

                try:
                    result = future.result(timeout=60)
                    results.append(result)
                    logger.debug(f"[PARALLEL_OCR] Готово: {filename} ({result.chars_count} символов)")
                except Exception as e:
                    logger.error(f"[PARALLEL_OCR] Ошибка для {filename}: {e}")
                    results.append(OCRResult(
                        filename=filename, text="", chars_count=0,
                        ocr_quality="failed", error=str(e)
                    ))

    except Exception as e:
        logger.error(f"[PARALLEL_OCR] Критическая ошибка: {e}")
        # Возвращаем partial results
        logger.warning(f"[PARALLEL_OCR] Получено {len(results)} из {total} результатов")

    logger.info(f"[PARALLEL_OCR] Завершено: {len(results)}/{total} вложений обработано")

    # Статистика
    good = sum(1 for r in results if r.ocr_quality == "good")
    partial = sum(1 for r in results if r.ocr_quality == "partial")
    failed = sum(1 for r in results if r.ocr_quality == "failed")
    skipped = sum(1 for r in results if r.ocr_quality == "skipped")
    logger.info(f"[PARALLEL_OCR] Качество: good={good}, partial={partial}, failed={failed}, skipped={skipped}")

    return results


def merge_ocr_results_to_attachments(attachments: List[Dict[str, Any]],
                                      ocr_results: List[OCRResult]) -> List[Dict[str, Any]]:
    """
    Объединяет OCR результаты с данными вложений.
    Добавляет extracted_text к каждому вложению.

    Args:
        attachments: Оригинальный список вложений
        ocr_results: Результаты OCR

    Returns:
        Обновлённый список вложений с extracted_text
    """
    # Создаём словарь результатов по имени файла
    results_map = {r.filename: r for r in ocr_results}

    updated = []
    for att in attachments:
        att_copy = att.copy()
        filename = att.get('filename', os.path.basename(att.get('path', '')))

        if filename in results_map:
            result = results_map[filename]
            att_copy['extracted_text'] = result.text
            att_copy['ocr_quality'] = result.ocr_quality
            att_copy['ocr_chars'] = result.chars_count
            if result.error:
                att_copy['ocr_error'] = result.error

        updated.append(att_copy)

    return updated
