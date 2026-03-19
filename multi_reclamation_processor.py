# multi_reclamation_processor.py
import re
import logging
from typing import Dict, List, Any, Optional

class MultiReclamationProcessor:
    """Класс для обработки нескольких рекламаций в одном письме"""
    
    def __init__(self):
        self.logger = logging.getLogger("MultiReclamationProcessor")
        
    def detect_multiple_products(self, email_data: Dict[str, Any], attachments: List[Dict[str, Any]], 
                                llama_result: Dict[str, Any]) -> Optional[List[str]]:
        """
        Определяет, содержит ли письмо несколько продуктов/рекламаций
        
        Args:
            email_data: Данные письма
            attachments: Список вложений
            llama_result: Результат анализа LLaMA
            
        Returns:
            Список продуктов или None, если продукт один или не определен
        """
        try:
            # Получаем текст для анализа
            subject = email_data.get('subject', '')
            body = email_data.get('body', '')
            
            # Объединяем тексты из вложений
            attachment_text = ""
            for att in attachments:
                if att.get('extracted_text'):
                    attachment_text += " " + att.get('extracted_text')
            
            all_text = f"{subject} {body} {attachment_text}".lower()
            
            # Список потенциальных продуктов ЭПОТОС (можно расширить)
            product_patterns = [
                r'мпп-[0-9]+', r'бип-[0-9]+', r'буип-м', r'асотп',
                r'огнетушител[а-я]+', r'пожаротушени[а-я]+'
            ]
            
            # Ищем все упоминания продуктов
            found_products = []
            for pattern in product_patterns:
                matches = re.findall(pattern, all_text)
                found_products.extend(matches)
            
            # Удаляем дубликаты и сортируем
            unique_products = sorted(set(found_products))
            
            if len(unique_products) > 1:
                self.logger.info(f"Обнаружено несколько продуктов: {unique_products}")
                return unique_products
            
            # Также проверяем результат LLaMA
            if llama_result and isinstance(llama_result, dict):
                product_name = llama_result.get('product_name')
                
                # Если LLaMA определила несколько продуктов
                if product_name and (',' in product_name or ';' in product_name):
                    products = re.split(r'[,;]\s*', product_name)
                    if len(products) > 1:
                        self.logger.info(f"LLaMA определила несколько продуктов: {products}")
                        return products
            
            return None
        
        except Exception as e:
            self.logger.error(f"Ошибка при определении нескольких продуктов: {e}")
            return None
    
    def split_reclamation_by_products(self, email_data: Dict[str, Any], attachments: List[Dict[str, Any]], 
                                     result: Dict[str, Any], products: List[str]) -> List[Dict[str, Any]]:
        """
        Разделяет рекламацию на несколько по продуктам
        
        Args:
            email_data: Данные письма
            attachments: Список вложений
            result: Результат обработки
            products: Список продуктов
            
        Returns:
            Список результатов обработки для каждого продукта
        """
        try:
            results = []
            
            for product in products:
                # Копируем основной результат
                product_result = result.copy()
                
                # Обновляем информацию о продукте
                product_result['product'] = product
                
                # Если есть llama_analysis, обновляем его
                if 'llama_analysis' in product_result:
                    llama_copy = product_result['llama_analysis'].copy()
                    llama_copy['product_name'] = product
                    product_result['llama_analysis'] = llama_copy
                
                # Добавляем информацию о том, что это часть множественной рекламации
                product_result['is_part_of_multiple'] = True
                product_result['all_products'] = products
                
                # Формируем новую тему для письма
                if 'subject' in product_result:
                    product_result['subject'] = f"{product_result['subject']} - Продукт: {product}"
                
                results.append(product_result)
            
            return results
        
        except Exception as e:
            self.logger.error(f"Ошибка при разделении рекламации: {e}")
            return [result]  # Возвращаем исходный результат в случае ошибки