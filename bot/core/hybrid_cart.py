# core/hybrid_cart.py
import logging
from typing import Dict, List
from core.json_config import json_config
from core.database import db

logger = logging.getLogger(__name__)

class HybridCartManager:
    def __init__(self):
        self.use_database = True  # Постепенно переключаем на БД
        self.memory_carts = {}  # Резервная память на время миграции
    
    def add_to_cart(self, user_id: int, product_id: str) -> Dict:
        """Добавить товар в корзину (с приоритетом БД) - ВОЗВРАЩАЕТ ДАННЫЕ ДЛЯ УВЕДОМЛЕНИЯ"""
        try:
            # Получаем информацию о товаре ДО добавления
            product = self.get_product_info(product_id)
            
            if not product:
                logger.error(f"Товар {product_id} не найден")
                return {"success": False, "message": "❌ Товар не найден"}
            
            if self.use_database:
                # Сохраняем в SQLite
                success = db.add_to_cart(user_id, product_id)
                if success:
                    logger.info(f"Товар {product_id} добавлен в БД корзину пользователя {user_id}")
                    # Получаем обновленные данные для уведомления
                    item_quantity = self.get_product_quantity(user_id, product_id)
                    total_quantity = self.get_total_quantity(user_id)
                    return {
                        "success": True,
                        "product": product,
                        "item_quantity": item_quantity,
                        "total_quantity": total_quantity,
                        "message": f"✅ {product.get('ProductTitle', 'Товар')} добавлен в корзину!"
                    }
                else:
                    logger.warning(f"Ошибка БД, используем память для пользователя {user_id}")
            
            # Резервное хранилище в памяти
            if user_id not in self.memory_carts:
                self.memory_carts[user_id] = {}
            
            if product_id in self.memory_carts[user_id]:
                self.memory_carts[user_id][product_id] += 1
            else:
                self.memory_carts[user_id][product_id] = 1
            
            logger.info(f"Товар {product_id} добавлен в память корзины пользователя {user_id}")
            
            # Данные для уведомления
            item_quantity = self.get_product_quantity(user_id, product_id)
            total_quantity = self.get_total_quantity(user_id)
            return {
                "success": True,
                "product": product,
                "item_quantity": item_quantity,
                "total_quantity": total_quantity,
                "message": f"✅ {product.get('ProductTitle', 'Товар')} добавлен в корзину!"
            }
            
        except Exception as e:
            logger.error(f"Ошибка добавления в корзину: {e}")
            return {"success": False, "message": "❌ Ошибка добавления в корзину"}
    
    def get_product_info(self, product_id: str) -> Dict:
        """Получить информацию о товаре для уведомления"""
        try:
            product = json_config.get_product_by_id(product_id)
            if product:
                return {
                    'ProductID': product_id,
                    'ProductTitle': product.get('ProductTitle', 'Товар'),
                    'Price': product.get('Price', '0'),
                    'ImagePath': product.get('ImagePath', ''),
                    'Volume': product.get('Volume', '')
                }
            return {}
        except Exception as e:
            logger.error(f"Ошибка получения информации о товаре: {e}")
            return {}
    
    def get_total_quantity(self, user_id: int) -> int:
        """Получить общее количество единиц товара в корзине (СУММА всех quantity)"""
        try:
            if self.use_database:
                cart_items = db.get_cart_items(user_id)
                if cart_items is not None:
                    return sum(item['quantity'] for item in cart_items)
            
            # Резервная проверка в памяти
            if user_id in self.memory_carts:
                return sum(self.memory_carts[user_id].values())
            
            return 0
        except Exception as e:
            logger.error(f"Ошибка получения общего количества товара: {e}")
            return 0

    def get_cart_count(self, user_id: int) -> int:
        """Получить количество позиций в корзине"""
        try:
            if self.use_database:
                cart_items = db.get_cart_items(user_id)
                if cart_items is not None:
                    return len(cart_items)
            
            # Резервная проверка в памяти
            if user_id in self.memory_carts:
                return len(self.memory_carts[user_id])
            
            return 0
        except Exception as e:
            logger.error(f"Ошибка получения количества корзины: {e}")
            return 0

    def remove_from_cart(self, user_id: int, product_id: str) -> bool:
        """Удалить товар из корзины"""
        try:
            if self.use_database:
                success = db.remove_from_cart(user_id, product_id)
                if success:
                    logger.info(f"Товар {product_id} удален из БД корзины пользователя {user_id}")
                    return True
            
            # Резервное удаление из памяти
            if user_id in self.memory_carts and product_id in self.memory_carts[user_id]:
                del self.memory_carts[user_id][product_id]
                logger.info(f"Товар {product_id} удален из памяти корзины пользователя {user_id}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Ошибка удаления из корзины: {e}")
            return False
    
    def clear_cart(self, user_id: int) -> bool:
        """Очистить корзину"""
        try:
            if self.use_database:
                success = db.clear_cart(user_id)
                if success:
                    logger.info(f"БД корзина пользователя {user_id} очищена")
            
            # Резервная очистка памяти
            if user_id in self.memory_carts:
                self.memory_carts[user_id] = {}
                logger.info(f"Память корзины пользователя {user_id} очищена")
                return True
            
            return True
        except Exception as e:
            logger.error(f"Ошибка очистки корзины: {e}")
            return False
    
    def get_product_quantity(self, user_id: int, product_id: str) -> int:
        """Получить количество конкретного товара в корзине"""
        try:
            if self.use_database:
                cart_items = db.get_cart_items(user_id)
                for item in cart_items:
                    if item['product_id'] == product_id:
                        return item['quantity']
            
            # Резервная проверка в памяти
            if user_id in self.memory_carts and product_id in self.memory_carts[user_id]:
                return self.memory_carts[user_id][product_id]
            
            return 0
        except Exception as e:
            logger.error(f"Ошибка получения количества товара: {e}")
            return 0
    
    def get_cart_items(self, user_id: int) -> List[Dict]:
        """Получить корзину (приоритет БД)"""
        try:
            if self.use_database:
                # Получаем из БД
                db_items = db.get_cart_items(user_id)
                if db_items is not None:
                    return self._format_db_cart(user_id, db_items)
            
            # Резерв из памяти
            return self._get_memory_cart(user_id)
            
        except Exception as e:
            logger.error(f"Ошибка получения корзины: {e}")
            return {'items': [], 'total': 0, 'count': 0}
    
    def _format_db_cart(self, user_id: int, db_items: List[Dict]) -> Dict:
        """Форматировать корзину из БД в старый формат"""
        cart_items = []
        total = 0
        
        for item in db_items:
            product_id = item['product_id']
            quantity = item['quantity']
            
            product = json_config.get_product_by_id(product_id)
            if product:
                price = int(product.get('Price', 0))
                item_total = price * quantity
                total += item_total
                
                cart_items.append({
                    'product_id': product_id,
                    'title': product.get('ProductTitle', 'Товар'),
                    'price': price,
                    'quantity': quantity,
                    'total': item_total
                })
        
        return {
            'items': cart_items,
            'total': total,
            'count': len(cart_items)
        }
    
    def _get_memory_cart(self, user_id: int) -> Dict:
        """Получить корзину из памяти"""
        if user_id not in self.memory_carts:
            return {'items': [], 'total': 0, 'count': 0}
        
        cart_items = []
        total = 0
        
        for product_id, quantity in self.memory_carts[user_id].items():
            product = json_config.get_product_by_id(product_id)
            if product:
                price = int(product.get('Price', 0))
                item_total = price * quantity
                total += item_total
                
                cart_items.append({
                    'product_id': product_id,
                    'title': product.get('ProductTitle', 'Товар'),
                    'price': price,
                    'quantity': quantity,
                    'total': item_total
                })
        
        return {
            'items': cart_items,
            'total': total,
            'count': len(cart_items)
        }
    
    def migrate_user_cart(self, user_id: int):
        """Мигрировать корзину пользователя из памяти в БД"""
        try:
            if user_id in self.memory_carts:
                for product_id, quantity in self.memory_carts[user_id].items():
                    for _ in range(quantity):
                        db.add_to_cart(user_id, product_id)
                del self.memory_carts[user_id]
                logger.info(f"Корзина пользователя {user_id} мигрирована в БД")
        except Exception as e:
            logger.error(f"Ошибка миграции корзины пользователя {user_id}: {e}")
    
    def migrate_all_carts(self):
        """Мигрировать все корзины из памяти в БД при запуске"""
        try:
            user_ids = list(self.memory_carts.keys())
            for user_id in user_ids:
                self.migrate_user_cart(user_id)
            logger.info(f"✅ Мигрировано {len(user_ids)} корзин в БД")
        except Exception as e:
            logger.error(f"Ошибка миграции корзин: {e}")
            
    def update_cart_quantity(self, user_id: int, product_id: str, quantity: int, variant_id: str = None) -> bool:
        """Обновление количества товара в корзине с учетом варианта"""
        if quantity <= 0:
            return self.remove_from_cart(user_id, product_id, variant_id)
        
        try:
            if self.use_database:
                return db.update_cart_quantity(user_id, product_id, quantity, variant_id)
            else:
                if user_id in self.memory_carts:
                    cart_key = f"{product_id}:{variant_id}" if variant_id else product_id
                    self.memory_carts[user_id][cart_key] = quantity
                    return True
            return False
        except Exception as e:
            logger.error(f"Ошибка обновления количества: {e}")
            return False
# Глобальный экземпляр
hybrid_cart = HybridCartManager()