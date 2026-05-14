# core/payment_system.py
import logging
from yookassa import Configuration, Payment
from core.config import YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY

logger = logging.getLogger(__name__)


class YooKassaPayment:
    """
    Класс для интеграции с ЮKassa.
    Реализует создание платежей, проверку статусов и формирование чеков (по 54-ФЗ).
    """

    def __init__(self):
        self.shop_id = YOOKASSA_SHOP_ID
        self.secret_key = YOOKASSA_SECRET_KEY

        if self.shop_id and self.secret_key:
            Configuration.account_id = self.shop_id
            Configuration.secret_key = self.secret_key
            logger.info("✅ ЮKassa успешно сконфигурирован")
        else:
            logger.warning("⚠️ ЮKassa не настроен — платежи работать не будут")

    def create_payment(self, amount, description, user_id, order_id, customer_email=None, items=None):
        """
        Создает платеж в ЮKassa с учетом требований 54-ФЗ.
        :param amount: сумма (float)
        :param description: описание платежа
        :param user_id: ID пользователя Telegram
        :param order_id: внутренний ID заказа
        :param customer_email: email покупателя (опционально)
        :param items: список товаров [{"description": "...", "quantity": 1, "amount": {"value": "...", "currency": "RUB"}, "vat_code": 1}]
        """
        try:
            if not self.shop_id or not self.secret_key:
                raise ValueError("ЮKassa не сконфигурирована")

            amount_value = f"{amount:.2f}"

            # Формируем чек по 54-ФЗ
            receipt = None
            if items:
                receipt = {
                    "customer": {},
                    "items": items,
                    "tax_system_code": 2  # УСН (доходы) — добавлено для соответствия требованиям ФНС/ЮKassa
                }
                if customer_email:
                    receipt["customer"]["email"] = customer_email
                else:
                    receipt["customer"]["full_name"] = f"Telegram user {user_id}"

            payment_params = {
                "amount": {"value": amount_value, "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": "https://t.me/SolidSimpleBot"  # Ссылка на Telegram-бота после оплаты
                },
                "capture": True,
                "description": description[:128],  # Ограничение API
                "metadata": {
                    "user_id": user_id,
                    "order_id": order_id
                }
            }

            if receipt:
                payment_params["receipt"] = receipt

            payment = Payment.create(payment_params)

            logger.info(f"✅ Создан платеж {payment.id} на сумму {amount_value} руб. для заказа #{order_id}")

            return {
                "id": payment.id,
                "status": payment.status,
                "confirmation_url": getattr(payment.confirmation, "confirmation_url", None),
            }

        except Exception as e:
            logger.exception(f"❌ Ошибка при создании платежа для заказа #{order_id}: {e}")
            return None

    def check_payment_status(self, payment_id):
        """
        Проверяет статус платежа по его ID.
        Возможные статусы: pending, waiting_for_capture, succeeded, canceled
        """
        try:
            payment = Payment.find_one(payment_id)
            logger.info(f"🔍 Статус платежа {payment_id}: {payment.status}")
            return payment.status
        except Exception as e:
            logger.exception(f"❌ Ошибка проверки статуса платежа {payment_id}: {e}")
            return None

    def is_configured(self):
        """Проверка корректности настройки ЮKassa"""
        valid = bool(
            self.shop_id and self.secret_key
            and self.shop_id != "test_shop_id"
            and self.secret_key != "test_secret_key"
        )
        if not valid:
            logger.warning("⚠️ ЮKassa не настроен корректно (используются тестовые данные)")
        return valid
