import os
import zipfile
import tempfile
import logging
from pathlib import Path
from typing import Optional

class ZipProcessor:
    """Класс для обработки ZIP-файлов и извлечения из них текста"""

    def __init__(self, attachment_processor=None):
        self.logger = logging.getLogger("ZipProcessor")
        self.attachment_processor = attachment_processor

    def process_zip(self, zip_path: str) -> str:
        """
        Обрабатывает ZIP-файл и извлекает из него текст

        Args:
            zip_path: Путь к ZIP-файлу

        Returns:
            Извлеченный текст из всех поддерживаемых файлов
        """
        try:
            if not os.path.exists(zip_path):
                self.logger.error(f"ZIP файл не существует: {zip_path}")
                return f"[Ошибка: ZIP файл не существует: {zip_path}]"

            # Создаем временную директорию для распаковки
            with tempfile.TemporaryDirectory() as temp_dir:
                # Распаковываем ZIP-файл
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    self.logger.info(f"Распаковка ZIP файла: {zip_path}")
                    # Список файлов в архиве
                    file_list = zip_ref.namelist()
                    self.logger.info(f"Файлы в архиве: {file_list}")

                    # Распаковываем все файлы (с защитой от path traversal)
                    for member in zip_ref.namelist():
                        if member.startswith('/') or '..' in member.replace('\\', '/'):
                            self.logger.warning(f"Пропуск небезопасного пути в ZIP: {member}")
                            continue
                        zip_ref.extract(member, temp_dir)

                # Обрабатываем каждый извлеченный файл
                extracted_text = f"=== Содержимое ZIP-файла {os.path.basename(zip_path)} ===\n"
                extracted_text += f"Файлы в архиве: {', '.join(file_list)}\n\n"

                # Проходим по всем файлам и папкам рекурсивно
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Получаем относительный путь от временной директории
                        rel_path = os.path.relpath(file_path, temp_dir)
                        ext = os.path.splitext(file)[1].lower()

                        # Сначала пробуем через attachment_processor (PDF, DOCX, EML и т.д.)
                        if self.attachment_processor and ext in self.attachment_processor.supported_extensions:
                            try:
                                file_content = self.attachment_processor.supported_extensions[ext](file_path)
                                extracted_text += f"--- {rel_path} ---\n{file_content}\n\n"
                            except Exception as e:
                                extracted_text += f"--- {rel_path} ---\n[Ошибка обработки: {str(e)}]\n\n"
                        # Fallback на текстовое чтение
                        elif self.is_text_file(file):
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    file_content = f.read()
                                    extracted_text += f"--- {rel_path} ---\n{file_content}\n\n"
                            except UnicodeDecodeError:
                                try:
                                    with open(file_path, 'r', encoding='latin-1') as f:
                                        file_content = f.read()
                                        extracted_text += f"--- {rel_path} ---\n{file_content}\n\n"
                                except Exception as e:
                                    extracted_text += f"--- {rel_path} ---\n[Ошибка чтения файла: {str(e)}]\n\n"
                        else:
                            extracted_text += f"--- {rel_path} ---\n[Формат {ext} не поддерживается]\n\n"

                return extracted_text

        except Exception as e:
            self.logger.error(f"Ошибка при обработке ZIP-файла {zip_path}: {e}")
            return f"[Ошибка при обработке ZIP-файла: {str(e)}]"

    def is_text_file(self, filename: str) -> bool:
        """
        Проверяет, является ли файл текстовым

        Args:
            filename: Имя файла

        Returns:
            True, если файл текстовый, иначе False
        """
        # Расширения текстовых файлов
        text_extensions = ['.txt', '.csv', '.xml', '.json', '.html', '.htm', '.md', '.log', '.ini', '.cfg', '.conf']

        # Получаем расширение файла
        ext = os.path.splitext(filename)[1].lower()

        return ext in text_extensions


def integrate_zip_processor(attachment_processor):
    """
    Интегрирует обработчик ZIP-файлов в AttachmentProcessor

    Args:
        attachment_processor: Экземпляр AttachmentProcessor

    Returns:
        Обновленный AttachmentProcessor
    """
    # Создаем экземпляр ZipProcessor с доступом к attachment_processor
    zip_processor = ZipProcessor(attachment_processor=attachment_processor)

    # Добавляем обработчик ZIP-файлов в supported_extensions
    attachment_processor.supported_extensions['.zip'] = zip_processor.process_zip

    return attachment_processor
