import logging
from typing import Dict, Any, Optional
from core.config import config

logger = logging.getLogger(__name__)

class CheckoutFlow:
    """Управление процессом оформления заказа"""
    
    def __init__(self):
        self.user_sessions = {}
    
    def start_checkout(self, user_id: int, cart_data: Dict[str, Any]) -> bool:
        """Начало процесса оформления заказа"""
        try:
            self.user_sessions[user_id] = {
                'step': 'start',
                'cart_data': cart_data,
                'contact_info': {
                    'telegram': None,
                    'phone': None,
                    'email': None
                },
                'delivery_method': None,
                'delivery_address': None,
                'pvz_address': None,  # ← НОВОЕ: для хранения адреса ПВЗ
                'payment_method': 'cash',  # По умолчанию наличные
                'comments': None,
                'order_id': None
            }
            logger.info(f"🚀 Начало оформления заказа для пользователя {user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Ошибка начала оформления: {e}")
            return False
    
    def get_session(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получить сессию оформления"""
        return self.user_sessions.get(user_id)
    
    def update_session(self, user_id: int, updates: Dict[str, Any]) -> bool:
        """Обновить данные сессии"""
        try:
            if user_id in self.user_sessions:
                self.user_sessions[user_id].update(updates)
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка обновления сессии: {e}")
            return False
    
    def save_contact_info(self, user_id: int, contact_type: str, value: str) -> bool:
        """Сохранение контактных данных"""
        try:
            if user_id in self.user_sessions:
                self.user_sessions[user_id]['contact_info'][contact_type] = value
                logger.info(f"💾 Сохранены контакты для {user_id}: {contact_type} = {value}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения контактов: {e}")
            return False
    
    def set_delivery_method(self, user_id: int, method: str, address: str = None) -> bool:
        """Установка способа доставки"""
        try:
            if user_id in self.user_sessions:
                self.user_sessions[user_id]['delivery_method'] = method
                if address:
                    self.user_sessions[user_id]['delivery_address'] = address
                logger.info(f"🚚 Установлена доставка для {user_id}: {method}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка установки доставки: {e}")
            return False
    
    def set_pvz_address(self, user_id: int, pvz_address: str) -> bool:
        """Установка адреса ПВЗ Яндекс"""
        try:
            if user_id in self.user_sessions:
                self.user_sessions[user_id]['pvz_address'] = pvz_address
                logger.info(f"🏪 Установлен адрес ПВЗ для {user_id}: {pvz_address}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка установки адреса ПВЗ: {e}")
            return False
    
    def set_payment_method(self, user_id: int, method: str) -> bool:
        """Установка способа оплаты"""
        try:
            if user_id in self.user_sessions:
                self.user_sessions[user_id]['payment_method'] = method
                logger.info(f"💰 Установлена оплата для {user_id}: {method}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка установки оплаты: {e}")
            return False
    
    def complete_checkout(self, user_id: int) -> Dict[str, Any]:
        """Завершение оформления и очистка сессии"""
        try:
            session_data = self.user_sessions.get(user_id, {})
            if user_id in self.user_sessions:
                del self.user_sessions[user_id]
            logger.info(f"✅ Завершено оформление для пользователя {user_id}")
            return session_data
        except Exception as e:
            logger.error(f"❌ Ошибка завершения оформления: {e}")
            return {}
    
    def calculate_total(self, user_id: int) -> float:
        """Расчет общей суммы с учетом доставки"""
        try:
            session = self.get_session(user_id)
            if not session:
                return 0
            
            total = session['cart_data']['total']
            delivery_method = session.get('delivery_method')
            
            # ОБНОВЛЕННАЯ ЛОГИКА РАСЧЕТА ДОСТАВКИ - используем config.DELIVERY_METHODS
            if delivery_method in config.DELIVERY_METHODS:
                delivery_cost = config.DELIVERY_METHODS[delivery_method]['cost']
                total += delivery_cost
                logger.info(f"💰 Добавлена стоимость доставки {delivery_method}: {delivery_cost} руб. Итоговая сумма: {total} руб.")
            else:
                logger.warning(f"⚠️ Неизвестный метод доставки: {delivery_method}, доставка не добавлена")
            
            return total
            
        except Exception as e:
            logger.error(f"❌ Ошибка расчета суммы: {e}")
            return 0
    
    def get_delivery_time(self, user_id: int) -> str:
        """Получить время доставки для отображения"""
        try:
            session = self.get_session(user_id)
            if not session:
                return "1-3 дня"
            
            delivery_method = session.get('delivery_method')
            
            # ОБНОВЛЕННАЯ ЛОГИКА ВРЕМЕНИ ДОСТАВКИ - используем config.DELIVERY_METHODS
            if delivery_method in config.DELIVERY_METHODS:
                delivery_time = config.DELIVERY_METHODS[delivery_method]['time']
                return delivery_time
            else:
                return "1-3 дня"
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения времени доставки: {e}")
            return "1-3 дня"
    
    def get_delivery_price(self, user_id: int) -> int:
        """Получить стоимость доставки"""
        try:
            session = self.get_session(user_id)
            if not session:
                return 0
            
            delivery_method = session.get('delivery_method')
            
            # ОБНОВЛЕННАЯ ЛОГИКА СТОИМОСТИ ДОСТАВКИ - используем config.DELIVERY_METHODS
            if delivery_method in config.DELIVERY_METHODS:
                delivery_cost = config.DELIVERY_METHODS[delivery_method]['cost']
                return delivery_cost
            else:
                return 0
                
        except Exception as e:
            logger.error(f"❌ Ошибка получения стоимости доставки: {e}")
            return 0
    
    def has_required_contacts(self, user_id: int) -> bool:
        """Проверяет, есть ли хотя бы email или телефон для отправки чека"""
        try:
            session = self.get_session(user_id)
            if not session:
                return False
            
            contact_info = session.get('contact_info', {})
            email = contact_info.get('email')
            phone = contact_info.get('phone')
            
            # Хотя бы один контакт должен быть указан
            has_contact = bool(email or phone)
            logger.info(f"🔍 Проверка контактов для {user_id}: email={bool(email)}, phone={bool(phone)}, результат={has_contact}")
            
            return has_contact
            
        except Exception as e:
            logger.error(f"❌ Ошибка проверки контактов: {e}")
            return False
    
    def requires_pvz_address(self, user_id: int) -> bool:
        """Проверяет, требует ли выбранный способ доставки ввода адреса ПВЗ"""
        try:
            session = self.get_session(user_id)
            if not session:
                return False
            
            delivery_method = session.get('delivery_method')
            
            # Проверяем, требует ли метод доставки адрес ПВЗ
            if (delivery_method in config.DELIVERY_METHODS and 
                config.DELIVERY_METHODS[delivery_method].get('requires_address', False)):
                return True
            
            return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка проверки необходимости адреса ПВЗ: {e}")
            return False

# Глобальный экземпляр
checkout_manager = CheckoutFlow()