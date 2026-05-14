# core/photo_manager.py
import logging
from pathlib import Path
from typing import Optional
from core.config import PHOTOS_DIR
from core.json_config import json_config

logger = logging.getLogger(__name__)

def get_product_photo_by_id(product_id: str) -> Optional[Path]:
    """
    Находит фото товара по его ID из JSON, используя прямое поле ImagePath
    """
    try:
        # 1. Получаем товар из JSON
        product = json_config.get_product_by_id(product_id)
        if not product:
            logger.warning(f"Товар не найден в JSON: {product_id}")
            return None
        
        # 2. Получаем прямой путь к фото из JSON
        image_path = product.get('ImagePath', '')
        if not image_path:
            logger.warning(f"У товара {product_id} не указан ImagePath")
            return None
        
        logger.info(f"ImagePath из JSON для {product_id}: {image_path}")
        
        # 3. Пробуем разные варианты путей
        possible_paths = []
        
        # 3.1. Прямой путь из JSON (относительно корня проекта)
        possible_paths.append(Path(image_path))
        
        # 3.2. Относительно PHOTOS_DIR
        possible_paths.append(PHOTOS_DIR / image_path)
        
        # 3.3. Если путь уже содержит photos/, убираем дублирование
        if image_path.startswith('photos/'):
            clean_path = image_path.replace('photos/', '', 1)
            possible_paths.append(PHOTOS_DIR / clean_path)
        
        # 3.4. Ищем по имени файла во всех поддиректориях PHOTOS_DIR
        filename = Path(image_path).name
        if PHOTOS_DIR.exists():
            for file_path in PHOTOS_DIR.rglob(filename):
                possible_paths.append(file_path)
        
        # 4. Проверяем все возможные пути
        for path in possible_paths:
            logger.info(f"Проверяем путь: {path}")
            if path.exists() and path.is_file():
                logger.info(f"Найдено фото: {path}")
                return path
        
        # 5. Если ничего не нашли, логируем диагностику
        logger.error(f"Фото для {product_id} не найдено ни по одному из путей")
        logger.error(f"Искали файл: {image_path}")
        
        # Диагностика: выводим структуру папки photos
        if PHOTOS_DIR.exists():
            logger.info(f"Содержимое {PHOTOS_DIR}:")
            for item in PHOTOS_DIR.rglob('*'):
                if item.is_file():
                    logger.info(f"  Файл: {item}")
                else:
                    logger.info(f"  Папка: {item}")
        else:
            logger.warning(f"Директория {PHOTOS_DIR} не существует")
        
        return None

    except Exception as e:
        logger.error(f"Ошибка при поиске фото для {product_id}: {e}")
        return None

# Старые функции для обратной совместимости (если используются)
def get_product_photo(category: str, variant: str) -> Optional[Path]:
    """Совместимость со старым кодом"""
    logger.warning(f"Используется устаревшая функция get_product_photo для {category}/{variant}")
    return None

def _map_category_id_to_name(category_id: str) -> str:
    """Маппинг ID категории из JSON в имя категории из config (для обратной совместимости)"""
    return ""