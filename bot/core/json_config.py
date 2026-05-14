# core/json_config.py
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

class JSONConfig:
    def __init__(self, json_path: Path):
        self.json_path = json_path
        self.data = self._load_json()
        self._photo_mapping = self._create_photo_mapping()
    
    def _load_json(self) -> Dict[str, Any]:
        """Загрузка JSON данных магазина"""
        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            logger.info(f"✅ JSON загружен: {len(data.get('Slides', []))} слайдов, "
                       f"{len(data.get('Products', []))} товаров")
            return data
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки JSON: {e}")
            return {"Slides": [], "Products": [], "ProductVariants": []}
    
    def _create_photo_mapping(self) -> Dict[str, str]:
        """Создание маппинга ProductID → имя файла фото"""
        # ПОЛНЫЙ маппинг всех товаров из вашей структуры
        mapping = {
            # Арома-роллеры
            "AR01": "ОСНОВА", "AR02": "ТЕПЛО", "AR03": "ЖЕЛАНИЕ", 
            "AR04": "ДУША", "AR05": "ВДОХ", "AR06": "СВЕТ", "AR07": "ЛУЧ",
            
            # Твердые духи  
            "SP01": "ТИШИНА", "SP02": "ПРОБУЖДЕНИЕ", "SP03": "ЭНЕРГИЯ",
            "SP04": "ГАРМОНИЯ", "SP05": "ЛЕГКОСТЬ", "SP06": "ЭФИР",
            "SP07": "САД", "SP08": "ВОЗДУХ",
            
            # Твердые шампуни
            "D01": "РОЗА ГРЕЙПФРУТ", "D02": "МАТТЯ ЧАЙ", "D03": "РОЗМАРИН",
            "D04": "ТВЕРДЫЙ КОНДИЦИОНЕР ОБЛЕПИХА",
            
            # Сыворотки и масла
            "SR01": "СЫВОРОТКА ДЛЯ ЛИЦА АВОКАДО", "MO01": "МАССАЖНОЕ МАСЛО СОЛНЦЕ",
            
            # Баттеры
            "BUT01": "БАТТЕР ДЛЯ ТЕЛА СОЛНЦЕ", "BUT02": "БАТТЕР ДЛЯ ТЕЛА СОЛНЦЕ", 
            "BUT03": "БАТТЕР ДЛЯ ТЕЛА СОЛНЦЕ", "BUT04": "БАТТЕР ДЛЯ ТЕЛА СОЛНЦЕ",
            
            # Бальзамы для губ
            "LB01": "МЯТА", "LB02": "ШОКОЛАД", "LB03": "Универсальный",
            
            # Бомбочки для ванны
            "BB01": "Солнце", "BB02": "Луна", "BB03": "Тайга",
            
            # Соль для ванны
            "BS01": "ЛАВАНДА",
            
            # Саше
            "SACH01": "ПАРИЖ", "SACH02": "ХОУМ", "SACH03": "САНРАЙЗ", 
            "SACH04": "СКАНДИ", "SACH05": "ЛУНА",
            
            # Пчелиные свечи
            "BE01": "ЛАВАНДАЛИМОНМЕЛИССА", "BE02": "ВАНИЛЬКАЛЕНДУЛАРОМАШКА", 
            "BE03": "РОЗАЛАДАНАПЕЛЬСИН",
            
            # Свечи в бетоне
            "CC01": "ПАРИЖ", "CC02": "ХОУМ", "CC03": "СКАНДИ",
            
            # Автопарфюмы
            "CP01": "ХОУМ", "CP02": "СКАНДИ", "CP03": "ЛУНА", "CP04": "ТЫ", 
            "CP05": "ЛЮБОВЬ", "CP06": "ПУТЬ", "CP07": "ЧИСТОТА", "CP08": "ТЕПЛО",
            
            # Диффузоры
            "DI01": "ТЫ", "DI02": "ЛЮБОВЬ", "DI03": "ПУТЬ", "DI04": "ЧИСТОТА", 
            "DI05": "ТЕПЛО", "DI06": "ДЕТСТВО"
        }
        return mapping
    
    def get_slide_by_id(self, slide_id: str) -> Optional[Dict[str, Any]]:
        """Получить слайд по ID"""
        for slide in self.data.get('Slides', []):
            if slide.get('SlideID') == slide_id:
                return slide
        return None
    
    def get_product_by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        """Получить товар по ID"""
        for product in self.data.get('Products', []):
            if product.get('ProductID') == product_id:
                return product
        return None
    
    def get_product_photo_name(self, product_id: str) -> Optional[str]:
        """Получить имя файла фото для товара"""
        return self._photo_mapping.get(product_id)
    
    def get_slide_buttons(self, slide_id: str) -> List[Dict[str, Any]]:
        """Получить кнопки для слайда"""
        slide = self.get_slide_by_id(slide_id)
        return slide.get('Buttons', []) if slide else []

# Глобальный экземпляр
json_config = JSONConfig(Path('data/shop_data.json'))