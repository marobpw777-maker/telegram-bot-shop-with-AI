# core/user_keyboards.py
import logging
from typing import List, Dict, Any, Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

logger = logging.getLogger(__name__)


def build_payment_buttons(delivery_method: Optional[str], total_amount: int) -> InlineKeyboardMarkup:
    """
    Построить клавиатуру оплаты. Если delivery_method указывает на PVZ (yandex_pickup),
    то кнопка 'Оплата при получении' не добавляется.
    """
    buttons: List[List[InlineKeyboardButton]] = []

    # основная кнопка онлайн-оплаты
    buttons.append([InlineKeyboardButton(f"💳 Оплатить онлайн — {total_amount} ₽", callback_data="pay:online")])

    # список алиасов PVZ — подкорректируй, если в сессии у тебя другой код
    pvz_aliases = {"pvz", "yandex_pickup", "pickup_point", "pvz_yandex"}

    if (delivery_method or "").lower() not in pvz_aliases:
        # показываем оплату при получении только если доставка НЕ PVZ
        buttons.append([InlineKeyboardButton("🧾 Оплата при получении", callback_data="pay:cod")])
    else:
        # информативная подсказка (необязательная) — callback noop можно игнорировать
        buttons.append([InlineKeyboardButton("ℹ️ Для ПВЗ доступна только онлайн-оплата", callback_data="noop")])

    # служебные кнопки
    buttons.append([InlineKeyboardButton("✏️ Исправить данные", callback_data="checkout:edit")])
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="cart:view")])

    return InlineKeyboardMarkup(buttons)


class UserKeyboards:
    """Класс для создания пользовательских inline-клавиатур"""

    @staticmethod
    def create_main_menu(cart_count: int = 0) -> InlineKeyboardMarkup:
        cart_text = f"🛒 Корзина ({cart_count})" if cart_count > 0 else "🛒 Корзина"
        keyboard = [
            [InlineKeyboardButton("📦 Каталог", callback_data="cat:catalog")],
            [InlineKeyboardButton("🔥 Хиты", callback_data="cat:quick_buy")],
            [InlineKeyboardButton(cart_text, callback_data="cart:view")],
            [InlineKeyboardButton("ℹ️ О нас", callback_data="info:about")],
            [InlineKeyboardButton("📞 Контакты", callback_data="info:contacts")],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_back_button(target: str = "nav:back") -> InlineKeyboardMarkup:
        keyboard = [[InlineKeyboardButton("⬅️ Назад", callback_data=target)]]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_back_home(cart_count: int = 0) -> InlineKeyboardMarkup:
        cart_text = f"🛒 ({cart_count})" if cart_count > 0 else "🛒"
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
            [InlineKeyboardButton("🏠 Главная", callback_data="nav:home", style="primary")],
            [InlineKeyboardButton(cart_text, callback_data="cart:view")],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_navigation_only(cart_count: int = 0) -> InlineKeyboardMarkup:
        cart_text = f"🛒 Корзина ({cart_count})" if cart_count > 0 else "🛒 Корзина"
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
            [InlineKeyboardButton("🏠 Главная", callback_data="nav:home", style="primary")],
            [InlineKeyboardButton(cart_text, callback_data="cart:view")],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_quick_actions(user_id: int, cart_items_count: int = 0) -> InlineKeyboardMarkup:
        cart_text = f"🛒 Корзина ({cart_items_count})" if cart_items_count > 0 else "🛒 Корзина"
        keyboard = [
            [InlineKeyboardButton("📦 Каталог", callback_data="cat:catalog")],
            [InlineKeyboardButton(cart_text, callback_data="cart:view")],
            [InlineKeyboardButton("🔍 Поиск", callback_data="search:start")],
            [InlineKeyboardButton("🏠 Главная", callback_data="nav:home", style="primary")],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_product_actions(product_id: str, cart_count: int = 0, is_in_cart: bool = False) -> InlineKeyboardMarkup:
        cart_text = f"🛒 В корзине ({cart_count})" if is_in_cart else "🛒 Добавить в корзину"
        add_button = InlineKeyboardButton(cart_text, callback_data=f"cart:add:{product_id}")
        keyboard = [
            [add_button],
            [InlineKeyboardButton("📦 Быстрая покупка", callback_data=f"buy:now:{product_id}", style="success")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
            [InlineKeyboardButton("🏠 Главная", callback_data="nav:home", style="primary")],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_product_actions_quiet(product_id: str, item_quantity: int) -> InlineKeyboardMarkup:
        """Тихая клавиатура для товара с отображением количества добавлений"""
        keyboard = [
            [
                InlineKeyboardButton("➖", callback_data=f"cart:dec:{product_id}"),
                InlineKeyboardButton(f"🛒 В корзине (×{item_quantity})", callback_data="cart:view"),
                InlineKeyboardButton("➕", callback_data=f"cart:inc:{product_id}")
            ],
            [
                InlineKeyboardButton("📖 Подробнее", callback_data=f"product:detail:{product_id}"),
                InlineKeyboardButton("⬅️ Назад", callback_data="nav:back"),
            ],
            [
                InlineKeyboardButton("🏠 Главная", callback_data="nav:home", style="primary"),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    def create_cart_notification_keyboard(product_id: str, cart_count: int) -> InlineKeyboardMarkup:
        """Клавиатура для уведомления о добавлении в корзину"""
        cart_text = f"📦 Перейти в корзину ({cart_count})"

        keyboard = [
            [InlineKeyboardButton(cart_text, callback_data="cart:view", style="primary")],
            [InlineKeyboardButton("➕ Добавить еще", callback_data=f"cart:add:{product_id}")],
            [InlineKeyboardButton("🛍️ Продолжить покупки", callback_data="cat:catalog")],
            [InlineKeyboardButton("✅ Понятно", callback_data="nav:close_notification")],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_variant_selection(variants: List[Dict[str, Any]], product_id: str, cart_count: int = 0) -> InlineKeyboardMarkup:
        """Выбор варианта товара с отображением корзины"""
        keyboard = []

        for variant in variants:
            variant_id = variant.get("VariantID")
            volume = variant.get("Volume", "")
            price = variant.get("Price", "{price}")

            button_text = f"📦 {volume} - {price}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"cart:add:{variant_id}")])

        # Навигационные кнопки с корзиной
        cart_text = f"🛒 Корзина ({cart_count})" if cart_count > 0 else "🛒 Корзина"
        keyboard.extend(
            [
                [InlineKeyboardButton("⬅️ Назад к товару", callback_data=f"prod:{product_id}")],
                [InlineKeyboardButton(cart_text, callback_data="cart:view")],
                [InlineKeyboardButton("🏠 Главная", callback_data="nav:home", style="primary")],
            ]
        )

        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_cart_actions(has_items: bool = False, cart_count: int = 0) -> InlineKeyboardMarkup:
        """Действия для корзины с отображением количества"""
        cart_text = f"🛒 Корзина ({cart_count})" if cart_count > 0 else "🛒 Корзина"

        if has_items:
            keyboard = [
                [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout:start", style="success")],
                [InlineKeyboardButton("🗑️ Очистить корзину", callback_data="cart:clear", style="danger")],
                [InlineKeyboardButton("🛍️ Продолжить покупки", callback_data="cat:catalog")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("🛍️ Продолжить покупки", callback_data="cat:catalog")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
            ]

        return InlineKeyboardMarkup(keyboard)

    def create_cart_actions_with_delivery(
        self,
        has_items: bool = False,
        cart_count: int = 0,
        delivery_options: Optional[List[Dict[str, Any]]] = None,
    ) -> InlineKeyboardMarkup:
        cart_text = f"🛒 Корзина ({cart_count})" if cart_count > 0 else "🛒 Корзина"
        if has_items:
            keyboard = [
                [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout:start", style="success")],
                [InlineKeyboardButton("🚚 Показать варианты доставки", callback_data="cart:view_delivery_options")],
                [InlineKeyboardButton("🗑️ Очистить корзину", callback_data="cart:clear", style="danger")],
                [InlineKeyboardButton("🛍️ Продолжить покупки", callback_data="cat:catalog")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("🛍️ Продолжить покупки", callback_data="cat:catalog")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="nav:back")],
            ]
        return InlineKeyboardMarkup(keyboard)


class ReplyKeyboards:
    """Класс для reply-кнопок (внизу экрана)"""

    @staticmethod
    def get_main_menu():
        """Главное меню - фиксированные кнопки"""
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("🏠 Главная", style="primary"), KeyboardButton("⬅️ Назад", style="primary")],
                [KeyboardButton("🗑️ Очистить корзину", style="danger"), KeyboardButton("🛍️ Продолжить покупки", style="success")]
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    @staticmethod
    def get_catalog_context():
        """Кнопки для просмотра каталога"""
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("🏠 Главная", style="primary"), KeyboardButton("⬅️ Назад", style="primary")],
                [KeyboardButton("🗑️ Очистить корзину", style="danger"), KeyboardButton("🛍️ Продолжить покупки", style="success")]
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    @staticmethod
    def get_product_context():
        """Кнопки в карточке товара - ТОЛЬКО основные навигационные"""
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("🏠 Главная", style="primary"), KeyboardButton("⬅️ Назад", style="primary")],
                [KeyboardButton("🗑️ Очистить корзину", style="danger"), KeyboardButton("🛍️ Продолжить покупки", style="success")]
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )

    @staticmethod
    def get_cart_context(has_items=True):
        """Кнопки в корзине"""
        if has_items:
            return ReplyKeyboardMarkup(
                [
                    [KeyboardButton("✅ Оформить заказ", style="success"), KeyboardButton("🗑️ Очистить корзину", style="danger")],
                    [KeyboardButton("🛍️ Продолжить покупки", style="success"), KeyboardButton("🏠 Главная", style="primary")]
                ],
                resize_keyboard=True,
                one_time_keyboard=False,
            )
        else:
            return ReplyKeyboardMarkup(
                [
                    [KeyboardButton("🛍️ Продолжить покупки", style="success"), KeyboardButton("🏠 Главная", style="primary")]
                ],
                resize_keyboard=True,
                one_time_keyboard=False,
            )

    @staticmethod
    def get_checkout_context():
        """Кнопки в процессе оформления заказа"""
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("🏠 Главная", style="primary"), KeyboardButton("⬅️ Назад", style="primary")],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )


# Глобальные экземпляры для использования
user_keyboards = UserKeyboards()
reply_kb = ReplyKeyboards()