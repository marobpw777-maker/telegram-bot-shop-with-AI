# handlers/checkout_handlers.py
import asyncio
import logging
import re
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from core.checkout_flow import checkout_manager
from core.hybrid_cart import hybrid_cart as cart_manager
from core.database import db
from core.telegram_payments import telegram_payments
from core.notifications import notify_admins_about_order, notify_customer_about_order, send_customer_email_notification
from core.config import config

# Новый менеджер состояний (см. core/state_manager.py)
from core.state_manager import state_manager

from core.agreements import user_has_agreement
from handlers.legal_handlers import send_agreement_prompt, AGREEMENT_VERSION

logger = logging.getLogger(__name__)

# Backward-compatible alias: другие модули (navigation_handlers) могут читать user_input_states
user_input_states = state_manager.user_input_states


def esc_html(text):
    """Безопасное экранирование для HTML"""
    if text is None:
        return ""
    text = str(text)
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&#39;')
    return text

# ---------------------
# Breadcrumbs (in-memory simple store)
# ---------------------
# Note: this is an in-memory implementation (not persistent between restarts).
# It's safe and simple — if you want persistence later, move to db/state storage.
BREADCRUMBS = {}  # user_id -> list of breadcrumb labels (strings)

def push_breadcrumb(user_id: int, label: str, max_len: int = 4):
    try:
        bc = BREADCRUMBS.get(user_id, [])
        if not bc or bc[-1] != label:
            bc.append(label)
        BREADCRUMBS[user_id] = bc[-max_len:]
    except Exception as e:
        logger.debug(f"Ошибка push_breadcrumb: {e}")

def pop_breadcrumb(user_id: int):
    try:
        bc = BREADCRUMBS.get(user_id, [])
        if bc:
            bc.pop()
        BREADCRUMBS[user_id] = bc
    except Exception as e:
        logger.debug(f"Ошибка pop_breadcrumb: {e}")

def clear_breadcrumbs(user_id: int):
    try:
        BREADCRUMBS[user_id] = []
    except Exception as e:
        logger.debug(f"Ошибка clear_breadcrumbs: {e}")

def render_breadcrumbs(user_id: int) -> str:
    try:
        bc = BREADCRUMBS.get(user_id, [])
        if not bc:
            return "🏠 Главная"
        displayed = bc[-3:]
        return "  •  ".join(["🏠 Главная"] + displayed)
    except Exception as e:
        logger.debug(f"Ошибка render_breadcrumbs: {e}")
        return "🏠 Главная"

# ---------------------
# Вспомогательная функция проверки актуальности согласия
# ---------------------
def _check_agreement_valid(agreement) -> bool:
    """Проверяет, актуально ли соглашение пользователя (не устарела ли версия)"""
    if not agreement:
        return False
    stored_version = agreement.get('agreement_version')
    if not stored_version:
        return False
    return stored_version == AGREEMENT_VERSION

# ---------------------
# Кастомные фильтры - ПЕРЕПИСАНЫ
# ---------------------
class ContactInputFilter(filters.MessageFilter):
    """Фильтр для состояний ввода контактов"""
    def filter(self, message):
        try:
            if not message or not message.from_user:
                return False
            user_id = message.from_user.id
            state = state_manager.get_input_state(user_id)
            return state in ['waiting_phone', 'waiting_phone_manual', 'waiting_email']
        except Exception:
            return False

class AddressInputFilter(filters.MessageFilter):
    """Фильтр для состояния ввода адреса доставки"""
    def filter(self, message):
        try:
            if not message or not message.from_user:
                return False
            user_id = message.from_user.id
            return state_manager.get_input_state(user_id) == 'waiting_address'
        except Exception:
            return False

class PvzAddressInputFilter(filters.MessageFilter):
    """Фильтр для состояния ввода адреса ПВЗ"""
    def filter(self, message):
        try:
            if not message or not message.from_user:
                return False
            user_id = message.from_user.id
            return state_manager.get_input_state(user_id) == 'waiting_pvz_address'
        except Exception:
            return False

# Создаем экземпляры фильтров
contact_input_filter = ContactInputFilter()
address_input_filter = AddressInputFilter()
pvz_address_input_filter = PvzAddressInputFilter()


# ---------------------
# Вспомогательные функции для проверки контактов
# ---------------------
def check_required_contacts(user_id: int) -> tuple[bool, str, str]:
    """Проверяет наличие обязательных контактов и возвращает (все_есть, статус_email, статус_телефона)"""
    session = checkout_manager.get_session(user_id)
    if not session:
        return False, "❌ Нет данных", "❌ Нет данных"
    
    contact_info = session.get('contact_info', {})
    has_email = bool(contact_info.get('email'))
    has_phone = bool(contact_info.get('phone'))
    
    email_status = f"✅ {contact_info.get('email')}" if has_email else "❌ Не указан"
    phone_status = f"✅ {contact_info.get('phone')}" if has_phone else "❌ Не указан"
    
    return has_email and has_phone, email_status, phone_status

def get_missing_contacts_prompt(has_email: bool, has_phone: bool) -> str:
    """Возвращает текст подсказки о том, каких контактов не хватает"""
    if not has_email and not has_phone:
        return "❌ Укажите email И телефон"
    elif not has_email:
        return "❌ Укажите email (обязательно для ПВЗ)"
    elif not has_phone:
        return "❌ Укажите телефон (обязательно для ПВЗ)"
    return "✅ Все контакты указаны"

# ---------------------
# Основная логика checkout
# ---------------------
async def checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # проверка согласия с учётом версии
    agreement = user_has_agreement(user_id)
    if not _check_agreement_valid(agreement):
        # запомним, что пользователь хотел оформить заказ
        context.user_data['post_agreement_action'] = {'type': 'checkout_start'}
        await send_agreement_prompt(update, context, post_action=context.user_data['post_agreement_action'])
        return
        
    """Первый шаг - подтверждение корзины (универсальная версия)"""
    try:
        if update.callback_query:
            query = update.callback_query
            await query.answer()
            user_id = query.from_user.id
            message_method = query.edit_message_text
        else:
            user_id = update.effective_user.id
            message_method = update.message.reply_text

        logger.info(f"🔄 checkout_start вызван для пользователя {user_id}")

        # 🔥 ИСПРАВЛЕНИЕ: используем cart_manager вместо hybrid_cart
        cart_data = cart_manager.get_cart_items(user_id)

        if not cart_data['items']:
            await message_method("❌ Корзина пуста! Добавьте товары перед оформлением.")
            return

        # Запускаем процесс оформления
        if not checkout_manager.start_checkout(user_id, cart_data):
            await message_method("❌ Ошибка начала оформления. Попробуйте позже.")
            return

        # Формируем текст заказа
        cart_summary = "\n".join([
            f"• {item['title']}\n  {item['quantity']} шт. × {item['price']} руб. = {item['total']} руб."
            for item in cart_data['items']
        ])
        
        total_cart = cart_data.get('total', 0)
        
        text = f"""
🎉 *Превосходный выбор!* 

Давайте превратим эти товары в ваш заказ. Это займет всего *2 минуты*! 

📦 *Ваша корзина:*
{cart_summary}

💎 *Сумма заказа: {total_cart} руб.*

💫 *Что вас ждет:*
• 📧 Один email для всех уведомлений
• 🚚 Выбор удобной доставки  
• 💳 Быстрая оплата в пару кликов
• 🎁 Заказ сразу начнем собирать!

*Готовы получить ваши товары?*
        """

        keyboard = [
            [InlineKeyboardButton("🚀 Да, продолжить!", callback_data="checkout:contacts")],
            [InlineKeyboardButton("✏️ Изменить корзину", callback_data="cart:view")],
            [InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]
        ]

        if update.callback_query:
            await query.edit_message_text(
                text=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                text=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"❌ Ошибка в checkout_start: {e}")
        try:
            if update.callback_query:
                await update.callback_query.message.reply_text("❌ Ошибка при начале оформления")
            else:
                await update.message.reply_text("❌ Ошибка при начале оформления")
        except Exception:
            pass


async def checkout_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг сбора контактных данных с прогресс-баром"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        user = query.from_user

        # Получаем сессию
        session = checkout_manager.get_session(user_id)
        if not session:
            await query.answer("❌ Сессия оформления не найдена. Начните заново.", show_alert=True)
            return

        # Автоматически сохраняем Telegram username
        telegram_username = f"@{user.username}" if user.username else user.first_name
        checkout_manager.save_contact_info(user_id, 'telegram', telegram_username)

        # 🔥 ОБНОВЛЕНО: Проверяем наличие ОБОИХ контактов
        has_all_contacts, email_status, phone_status = check_required_contacts(user_id)
        
        # 🔥 ДОБАВЛЯЕМ ПРОГРЕСС-БАР - ИСПРАВЛЕННЫЙ ВЫЗОВ
        from core.animations import animated_progress_bar
        progress_text = await animated_progress_bar(current_step=1)
        
        # Формируем текст
        text = f"""
{progress_text}

📧 *Шаг 1 из 3: Ваши контактные данные*

📧 *Email для электронного чека:*
{email_status}

📞 *Телефон для SMS-уведомлений:*
{phone_status}

🛡️ *Ваши данные в безопасности:*
Используем только для связи по заказу

*Выберите способ:*
        """

        keyboard = [
            [InlineKeyboardButton("📧 Ввести email", callback_data="checkout:input_email")],
            [InlineKeyboardButton("📱 Поделиться номером", callback_data="checkout:share_phone")],
        ]
        
        # 🔥 ОБНОВЛЕНО: Кнопка продолжения только если есть ОБА контакта
        if has_all_contacts:
            keyboard.append([InlineKeyboardButton("✅ Продолжить оформление", callback_data="checkout:delivery")])
        else:
            missing_prompt = get_missing_contacts_prompt(
                bool(session.get('contact_info', {}).get('email')),
                bool(session.get('contact_info', {}).get('phone'))
            )
            keyboard.append([InlineKeyboardButton(missing_prompt, callback_data="checkout:contacts")])
            
        keyboard.append([InlineKeyboardButton("⬅️ Назад к заказу", callback_data="checkout:start")])

        await query.edit_message_text(
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в checkout_contacts: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при вводе контактов", show_alert=True)
        except Exception:
            pass


async def handle_phone_sharing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия кнопки 'Поделиться номером'"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id

        # Сохраняем состояние ожидания номера телефона
        state_manager.set_input_state(user_id, 'waiting_phone')
        user_input_states[user_id] = 'waiting_phone'

        text = """
📱 *Укажите номер телефона*

Выберите удобный способ:

1. Нажмите кнопку «Отправить мой номер» — Telegram отправит ваш контакт автоматически
2. Введите номер вручную в поле чата в формате:
   • +7XXXYYYZZZZ
   • 8XXXYYYZZZZ

Для отмены нажмите '⬅️ Назад'
        """

        try:
            await query.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📱 Отправить мой номер", request_contact=True)]],
                    one_time_keyboard=True,
                    resize_keyboard=True
                )
            )
        except Exception:
            await query.edit_message_text(
                text="Пожалуйста, пришлите ваш номер в чат (+7... или нажмите кнопку Отправить мой номер в клавиатуре)."
            )

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_phone_sharing: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при запросе номера", show_alert=True)
        except Exception:
            pass


async def handle_manual_phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка начала ручного ввода номера"""
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id

        state_manager.set_input_state(user_id, 'waiting_phone_manual')
        user_input_states[user_id] = 'waiting_phone_manual'

        text = """
📝 *Введите номер телефона вручную*

Пожалуйста, напишите ваш номер телефона в этом чате:
• +7XXXYYYZZZZ
• 8XXXYYYZZZZ

Для отмены нажмите '⬅️ Назад'
        """

        await query.edit_message_text(
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="checkout:share_phone")]])
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_manual_phone_input: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при запросе номера", show_alert=True)
        except Exception:
            pass


async def handle_email_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатия кнопки 'Ввести email'"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id

        # Сохраняем состояние ожидания email
        state_manager.set_input_state(user_id, 'waiting_email')
        user_input_states[user_id] = 'waiting_email'

        text = """
📧 *Введите ваш email*

Пожалуйста, напишите ваш email адрес в этом чате и отправьте сообщение:

*Пример:* example@gmail.com
        """

        await query.edit_message_text(
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="checkout:contacts")]])
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_email_input: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при запросе email", show_alert=True)
        except Exception:
            pass


async def handle_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученных контактных данных"""
    try:
        if not update.message:
            return

        user_id = update.message.from_user.id
        input_state = state_manager.get_input_state(user_id)

        if not input_state:
            return

        logger.info(f"🔄 Обрабатываем контактные данные для пользователя {user_id}, состояние: {input_state}")

        if input_state in ['waiting_phone', 'waiting_phone_manual']:
            if update.message.contact:
                phone_number = update.message.contact.phone_number
            else:
                phone_number = (update.message.text or "").strip()

            cleaned_number = re.sub(r'[\s\-\(\)]', '', phone_number)

            if not re.match(r'^(\+7|8|7)[0-9]{10}$', cleaned_number):
                await update.message.reply_text(
                    "❌ Неверный формат номера. Пожалуйста, используйте формат:\n+7XXXYYYZZZZ или 8XXXYYYZZZZ",
                    reply_markup=ReplyKeyboardRemove()
                )
                return

            if cleaned_number.startswith('8'):
                cleaned_number = '+7' + cleaned_number[1:]
            elif cleaned_number.startswith('7') and not cleaned_number.startswith('+7'):
                cleaned_number = '+7' + cleaned_number[1:]

            checkout_manager.save_contact_info(user_id, 'phone', cleaned_number)
            state_manager.clear_input_state(user_id)
            if user_id in user_input_states:
                del user_input_states[user_id]

            # 🔥 ОБНОВЛЕНО: После сохранения телефона проверяем, все ли контакты есть
            has_all_contacts, _, _ = check_required_contacts(user_id)
            
            if has_all_contacts:
                # Все контакты есть, показываем кнопку продолжения
                await update.message.reply_text(
                    f"✅ Номер телефона сохранен: {cleaned_number}\n📧 Email также сохранен",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Продолжить оформление", callback_data="checkout:delivery")]])
                )
            else:
                # Нужен еще email
                await update.message.reply_text(
                    f"✅ Номер телефона сохранен: {cleaned_number}\n\n📧 Теперь укажите email для завершения ввода контактов",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📧 Ввести email", callback_data="checkout:input_email")]])
                )
                
        elif input_state == 'waiting_email':
            email = (update.message.text or "").strip()

            if not re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[A-Za-z]{2,}$', email):
                await update.message.reply_text(
                    "❌ Неверный формат email. Пожалуйста, проверьте правильность ввода.\n\n*Пример:* example@gmail.com",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="checkout:contacts")]])
                )
                return

            checkout_manager.save_contact_info(user_id, 'email', email)
            state_manager.clear_input_state(user_id)
            if user_id in user_input_states:
                del user_input_states[user_id]

            # 🔥 ОБНОВЛЕНО: После сохранения email проверяем, все ли контакты есть
            has_all_contacts, _, _ = check_required_contacts(user_id)
            
            if has_all_contacts:
                # Все контакты есть, показываем кнопку продолжения
                await update.message.reply_text(
                    f"✅ Email сохранен: {email}\n📱 Телефон также сохранен",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Продолжить оформление", callback_data="checkout:delivery")]])
                )
            else:
                # Нужен еще телефон
                await update.message.reply_text(
                    f"✅ Email сохранен: {email}\n\n📱 Теперь укажите номер телефона для завершения ввода контактов",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Поделиться номером", callback_data="checkout:share_phone")]])
                )

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_contact_message: {e}")
        try:
            await update.message.reply_text("❌ Произошла ошибка при сохранении контакта", reply_markup=ReplyKeyboardRemove())
        except Exception:
            pass


async def checkout_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг выбора способа доставки с прогресс-баром"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        
        # 🔥 ОБНОВЛЕНО: Проверяем наличие ОБОИХ контактов для чека
        has_all_contacts, email_status, phone_status = check_required_contacts(user_id)
        if not has_all_contacts:
            await query.answer(
                f"❌ Для оформления заказа укажите И email, И телефон!\n\n"
                f"Email: {email_status}\n"
                f"Телефон: {phone_status}",
                show_alert=True
            )
            await checkout_contacts(update, context)
            return

        # 🔥 ДОБАВЛЯЕМ ПРОГРЕСС-БАР - ИСПРАВЛЕННЫЙ ВЫЗОВ
        from core.animations import animated_progress_bar
        progress_text = await animated_progress_bar(current_step=2)
        
        delivery_methods = config.DELIVERY_METHODS
        
        text = f"""
{progress_text}

🚚 *Шаг 2 из 3: Выберите способ получения*

*{delivery_methods['self_pickup']['emoji']} {delivery_methods['self_pickup']['name']} · {delivery_methods['self_pickup']['cost']} ₽*
{delivery_methods['self_pickup']['description']}
⏱ {delivery_methods['self_pickup']['time']}
💫 Идеально, если вы рядом!

*{delivery_methods['yandex_pickup']['emoji']} {delivery_methods['yandex_pickup']['name']} · {delivery_methods['yandex_pickup']['cost']} ₽ · {delivery_methods['yandex_pickup']['time']}*
{delivery_methods['yandex_pickup']['description']}  
📦 Удобно забрать в любое время
🎯 Самый популярный вариант!

*{delivery_methods['courier']['emoji']} {delivery_methods['courier']['name']} · {delivery_methods['courier']['cost']} ₽ · {delivery_methods['courier']['time']}*  
{delivery_methods['courier']['description']}
⏰ В удобное для вас время
💎 Максимальный комфорт

💡 *Совет:* Большинство выбирают {delivery_methods['yandex_pickup']['name']}

📌 *Ваши контакты:*
📧 Email: {email_status}
📱 Телефон: {phone_status}
        """

        keyboard = [
            [InlineKeyboardButton(f"{delivery_methods['self_pickup']['emoji']} {delivery_methods['self_pickup']['name']} · {delivery_methods['self_pickup']['cost']} ₽", callback_data="checkout:delivery_self_pickup")],
            [InlineKeyboardButton(f"{delivery_methods['yandex_pickup']['emoji']} {delivery_methods['yandex_pickup']['name']} · {delivery_methods['yandex_pickup']['cost']} ₽", callback_data="checkout:delivery_yandex_pickup")],
            [InlineKeyboardButton(f"{delivery_methods['courier']['emoji']} {delivery_methods['courier']['name']} · {delivery_methods['courier']['cost']} ₽", callback_data="checkout:delivery_courier")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="checkout:contacts")]
        ]

        await query.edit_message_text(
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в checkout_delivery: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при выборе доставки", show_alert=True)
        except Exception:
            pass


async def handle_delivery_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора способа доставки"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        
        # Извлекаем тип доставки из callback_data
        callback_data = query.data
        logger.info(f"🔄 Обрабатываем выбор доставки: {callback_data}")
        
        # Разбираем callback_data: "checkout:delivery_self_pickup" -> "self_pickup"
        if callback_data.startswith("checkout:delivery_"):
            delivery_type = callback_data.replace("checkout:delivery_", "")
            logger.info(f"🔄 Извлечен тип доставки: {delivery_type}")
        else:
            logger.error(f"❌ Неизвестный формат callback_data: {callback_data}")
            await query.answer("❌ Ошибка выбора доставки", show_alert=True)
            return

        delivery_methods = config.DELIVERY_METHODS
        
        if delivery_type not in delivery_methods:
            logger.error(f"❌ Неизвестный способ доставки: {delivery_type}")
            await query.answer("❌ Неизвестный способ доставки", show_alert=True)
            return

        # 🔥 ОБНОВЛЕНО: Для ПВЗ проверяем наличие ОБОИХ контактов
        if delivery_type == 'yandex_pickup':
            has_all_contacts, email_status, phone_status = check_required_contacts(user_id)
            if not has_all_contacts:
                await query.answer(
                    f"❌ Для доставки в ПВЗ требуются ОБА контакта!\n\n"
                    f"Email: {email_status}\n"
                    f"Телефон: {phone_status}",
                    show_alert=True
                )
                await checkout_contacts(update, context)
                return

        # Сохраняем способ доставки
        logger.info(f"🔄 Сохраняем способ доставки: {delivery_type}")
        checkout_manager.set_delivery_method(user_id, delivery_type)

        # 🔥 ИСПРАВЛЕНИЕ: Для самовывоза тоже переходим к шагу оплаты, а не сразу к оплате
        if delivery_type == 'self_pickup':
            logger.info(f"🔄 Выбран самовывоз, переходим к шагу оплаты для пользователя {user_id}")
            await checkout_payment(update, context)  # ← Переходим к шагу выбора оплаты
            
        # Для ПВЗ Яндекс запрашиваем адрес ПВЗ
        elif delivery_type == 'yandex_pickup':
            logger.info(f"🔄 Устанавливаем состояние waiting_pvz_address для пользователя {user_id}")
            state_manager.set_input_state(user_id, 'waiting_pvz_address')
            user_input_states[user_id] = 'waiting_pvz_address'

            text = config.CHECKOUT_MESSAGES.get('pvz_address', "Укажите адрес ПВЗ")

            keyboard = [[InlineKeyboardButton("⬅️ Выбрать другой способ доставки", callback_data="checkout:delivery")]]

            await query.edit_message_text(
                text=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif delivery_type == 'courier':
            logger.info(f"🔄 Устанавливаем состояние waiting_address для пользователя {user_id}")
            state_manager.set_input_state(user_id, 'waiting_address')
            user_input_states[user_id] = 'waiting_address'

            text = """
🏠 *Укажите адрес доставки*

Пожалуйста, отправьте адрес для курьерской доставки:

• Город, улица, дом, квартира
• Например: Москва, ул. Примерная, д. 123, кв. 45

📦 *Курьер доставит заказ прямо к вашей двери*
            """

            keyboard = [[InlineKeyboardButton("⬅️ Выбрать другой способ доставки", callback_data="checkout:delivery")]]

            await query.edit_message_text(
                text=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        # 🔥 УДАЛЕН else блок, который сразу переходил к оплате

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_delivery_method: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при сохранении доставки", show_alert=True)
        except Exception:
            pass


async def handle_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода адреса доставки"""
    try:
        if not update.message:
            return

        user_id = update.message.from_user.id
        current_state = state_manager.get_input_state(user_id)

        logger.info(f"🔄 Обрабатываем ввод адреса для пользователя {user_id}, состояние: {current_state}")

        if current_state == 'waiting_address':
            address = update.message.text.strip()
            logger.info(f"🔄 Сохраняем адрес доставки: {address}")
            
            # Сохраняем адрес
            checkout_manager.update_session(user_id, {'delivery_address': address})
            state_manager.clear_input_state(user_id)
            if user_id in user_input_states:
                del user_input_states[user_id]

            logger.info(f"✅ Адрес доставки сохранен для пользователя {user_id}")

            await update.message.reply_text(
                f"✅ Адрес доставки сохранен:\n{address}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Продолжить", callback_data="checkout:payment")]])
            )

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_address_input: {e}")
        try:
            await update.message.reply_text("❌ Ошибка при сохранении адреса")
        except Exception:
            pass


async def handle_pvz_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода адреса ПВЗ Яндекс"""
    try:
        if not update.message:
            return

        user_id = update.message.from_user.id
        current_state = state_manager.get_input_state(user_id)

        logger.info(f"🔄 Обрабатываем ввод адреса ПВЗ для пользователя {user_id}, состояние: {current_state}")

        if current_state == 'waiting_pvz_address':
            pvz_address = update.message.text.strip()
            logger.info(f"🔄 Сохраняем адрес ПВЗ: {pvz_address}")
            
            # Сохраняем адрес ПВЗ
            checkout_manager.set_pvz_address(user_id, pvz_address)
            state_manager.clear_input_state(user_id)
            if user_id in user_input_states:
                del user_input_states[user_id]

            logger.info(f"✅ Адрес ПВЗ сохранен для пользователя {user_id}")

            await update.message.reply_text(
                f"✅ Адрес ПВЗ сохранен:\n{pvz_address}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Продолжить", callback_data="checkout:payment")]])
            )

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_pvz_address_input: {e}")
        try:
            await update.message.reply_text("❌ Ошибка при сохранении адреса ПВЗ")
        except Exception:
            pass


async def checkout_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг выбора способа оплаты с прогресс-баром"""
    try:
        logger.info("🔄 checkout_payment вызван")
        
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        session = checkout_manager.get_session(user_id)

        if not session:
            logger.error(f"❌ Сессия не найдена для пользователя {user_id}")
            await query.answer("❌ Данные заказа не найдены. Начните заново.", show_alert=True)
            return

        # 🔥 ДОБАВЛЯЕМ ПРОГРЕСС-БАР - ИСПРАВЛЕННЫЙ ВЫЗОВ
        from core.animations import animated_progress_bar
        progress_text = await animated_progress_bar(current_step=3)

        # Рассчитываем итоговую сумму
        total = checkout_manager.calculate_total(user_id)
        logger.info(f"🔄 Рассчитана итоговая сумма: {total} руб.")

        # Форматируем товары для красивого отображения
        cart_items_text = "\n".join([
            f"• {item['title']} × {item['quantity']} = {item['total']} ₽"
            for item in session['cart_data']['items']
        ])

        # Получаем информацию о доставке из конфигурации
        delivery_method = session.get('delivery_method', 'self_pickup')
        delivery_info = config.DELIVERY_METHODS.get(delivery_method, config.DELIVERY_METHODS['self_pickup'])
        
        delivery_name = delivery_info['name']
        delivery_price = checkout_manager.get_delivery_price(user_id)
        
        # Определяем адрес для отображения
        if delivery_method == 'yandex_pickup':
            address_display = session.get('pvz_address', 'Не указан')
            logger.info(f"🔄 Для ПВЗ Яндекс отображаем адрес: {address_display}")
        elif delivery_method == 'courier':
            address_display = session.get('delivery_address', 'Не указан')
            logger.info(f"🔄 Для курьера отображаем адрес: {address_display}")
        else:
            address_display = config.CONTACT_INFO.get('address', 'Не указан')
            logger.info(f"🔄 Для самовывоза отображаем адрес: {address_display}")

        # 🔥 ОБНОВЛЕНО: Показываем контактную информацию в итоговом заказе
        contact_info = session.get('contact_info', {}) or {}
        email_display = contact_info.get('email', '❌ Не указан')
        phone_display = contact_info.get('phone', '❌ Не указан')
        
        text = f"""
{progress_text}

✨ *Проверьте ваш заказ*

📦 *Состав заказа:*
{cart_items_text}

📧 *Контактная информация:*
• Email: {email_display}
• Телефон: {phone_display}

🚚 *Доставка:*
{delivery_name} · {delivery_price} ₽

📍 *Адрес получения:*
{address_display}

💰 *Итого к оплате:*
• Товары: {session['cart_data']['total']} ₽
• Доставка: {delivery_price} ₽  
• *Всего: {total} ₽*

✅ *После оплаты:*
• Сразу начнем собирать заказ
• Отправим чек на email
• Пришлем трек-номер для отслеживания

💫 *Спасибо, что выбираете нас!*
        """

        full_text = f"{render_breadcrumbs(user_id)}\n\n{text}"

        # Build keyboard: hide "Оплата при получении" for PVZ (yandex_pickup)
        pvz_aliases = {"pvz", "yandex_pickup", "pickup_point", "pvz_yandex"}
        keyboard = []
        keyboard.append([InlineKeyboardButton("💳 Оплатить заказ", callback_data="checkout:payment_online")])
        if (delivery_method or "").lower() not in pvz_aliases:
            keyboard.append([InlineKeyboardButton("💰 Оплата при получении", callback_data="checkout:payment_cash")])
        else:
            # show informative button (non-actionable) for PVZ informing user
            keyboard.append([InlineKeyboardButton("ℹ️ Для ПВЗ доступна только онлайн-оплата", callback_data="noop")])

        keyboard.append([InlineKeyboardButton("✏️ Исправить данные", callback_data="checkout:edit")])

        await query.edit_message_text(
            text=full_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в checkout_payment: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при выборе оплаты", show_alert=True)
        except Exception:
            pass


async def handle_payment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора способа оплаты"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        payment_type = query.data

        # 🔥 ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА СОГЛАСИЯ ПЕРЕД ПЛАТЕЖОМ
        agreement = user_has_agreement(user_id)
        if not _check_agreement_valid(agreement):
            context.user_data['post_agreement_action'] = {'type': 'callback', 'data': payment_type}
            await send_agreement_prompt(update, context, post_action=context.user_data['post_agreement_action'])
            return

        logger.info(f"🔄 Обрабатываем выбор оплаты: {payment_type}")

        if "online" in payment_type:
            checkout_manager.set_payment_method(user_id, 'online_telegram')
            session = checkout_manager.get_session(user_id)
            if not session:
                await query.answer("❌ Сессия не найдена. Начните заново.", show_alert=True)
                return

            success = await telegram_payments.send_invoice(update, context, user_id, session)

            if success:
                await query.edit_message_text(
                    "💳 *Оплата заказа*\n\n"
                    "🛡️ *Безопасная оплата через Telegram*\n"
                    "• Ваши данные защищены\n"
                    "• Можно оплатить картой любого банка\n"
                    "• Мгновенное подтверждение\n\n"
                    "👇 *Нажмите кнопку ниже для оплаты:*\n"
                    "Откроется безопасное окно оплаты",
                    parse_mode='Markdown'
                )
            else:
                await query.answer("❌ Ошибка при создании платежа", show_alert=True)

        elif "cash" in payment_type:
            # 🔥 ИСПРАВЛЕНИЕ: Устанавливаем метод оплаты и завершаем заказ
            checkout_manager.set_payment_method(user_id, 'cash')
            await checkout_complete(update, context)  # ← Завершаем заказ

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_payment_method: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при сохранении оплаты", show_alert=True)
        except Exception:
            pass

async def checkout_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение оформления заказа с современным текстом в стиле 2026 года"""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        session = checkout_manager.get_session(user_id)

        if not session:
            await query.answer("❌ Данные заказа не найдены. Начните заново.", show_alert=True)
            return

        # Рассчитываем итоговую сумму
        total = checkout_manager.calculate_total(user_id)

        # Логируем данные перед созданием заказа
        logger.info(f"🔄 Создаем заказ для пользователя {user_id}")
        logger.info(f"🔄 Метод доставки: {session.get('delivery_method')}")
        logger.info(f"🔄 Адрес доставки: {session.get('delivery_address')}")
        logger.info(f"🔄 Адрес ПВЗ: {session.get('pvz_address')}")
        logger.info(f"🔄 Итоговая сумма: {total}")

        # --- Сохраняем telegram handle в contact_info (если есть) ---
        contact_info = session.get('contact_info', {}) or {}
        # prefer explicit stored
        tg = contact_info.get('telegram') or contact_info.get('tg') or contact_info.get('username')
        if not tg:
            # try from Telegram user object
            try:
                user = query.from_user if query else (update.effective_user if update else None)
                if user and getattr(user, 'username', None):
                    tg = f"@{user.username}"
            except Exception:
                tg = None
        if tg:
            # normalize
            tg = tg.strip()
            if tg and not tg.startswith('@'):
                tg = '@' + tg
            contact_info['telegram'] = tg

        # Создаем заказ в базе данных
        order_id = db.create_order(
            user_id=user_id,
            cart_data=session['cart_data'],
            total=total,
            contact_info=contact_info,
            delivery_method=session['delivery_method'],
            delivery_address=session.get('delivery_address'),
            pvz_address=session.get('pvz_address'),
            payment_method=session['payment_method']
        )

        logger.info(f"🔄 Создан заказ с ID: {order_id}")

        if order_id > 0:
            cart_manager.clear_cart(user_id)
            
            # Формируем order_data для уведомлений
            order_data = {
                'id': order_id,
                'total': total,
                'delivery_method': session['delivery_method'],
                'items': session['cart_data']['items'],
                'contact_info': contact_info,
                'delivery_address': session.get('delivery_address'),
                'pvz_address': session.get('pvz_address'),
                'payment_method': session['payment_method']
            }

            # 🔥 ОТПРАВЛЯЕМ СОВРЕМЕННОЕ УВЕДОМЛЕНИЕ ПОКУПАТЕЛЮ
            try:
                await notify_customer_about_order(context.bot, user_id, order_data)
                logger.info(f"✅ Отправлено современное уведомление покупателю {user_id} о заказе {order_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки уведомления покупателю: {e}")
                # Fallback на старое уведомление
                try:
                    text = f"""
🎉 Заказ успешно оформлен!

📋 Номер заказа: #{order_id}
💎 Сумма заказа: {total} руб.

Спасибо за заказ! ❤️

Мы свяжемся с вами для подтверждения деталей доставки.
                    """
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode='Markdown'
                    )
                except Exception as fallback_error:
                    logger.error(f"❌ Ошибка fallback уведомления: {fallback_error}")

            # 🔥 ОТПРАВЛЯЕМ EMAIL-УВЕДОМЛЕНИЕ если есть email
            try:
                email = contact_info.get('email')
                if email:
                    send_customer_email_notification(order_data, email)
                    logger.info(f"✅ Отправлено email-уведомление на {email} для заказа {order_id}")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки email-уведомления: {e}")

            # 🔥 УВЕДОМЛЯЕМ АДМИНОВ о новом заказе
            try:
                await notify_admins_about_order(context.bot, order_id, order_data, total)
                logger.info(f"✅ Отправлено уведомление админам о заказе {order_id}")
            except Exception as e:
                logger.exception(f"❌ Ошибка уведомления админов о новом заказе: {e}")

            checkout_manager.complete_checkout(user_id)
            clear_breadcrumbs(user_id)

            # 🔥 СОВРЕМЕННЫЙ ТЕКСТ ПОДТВЕРЖДЕНИЯ В ЧАТЕ
            # (дополнительно к уже отправленному уведомлению)
            delivery_method = session.get('delivery_method', 'pickup')
            delivery_name = config.DELIVERY_METHODS.get(delivery_method, {}).get('name', 'Самовывоз')
            
            text = f"""
✅ *ЗАКАЗ ПРИНЯТ В РАБОТУ!*

📦 *Заказ №{order_id}* передан в сборку
💎 *Сумма заказа:* {total} ₽
🚚 *Способ получения:* {delivery_name}

⏱️ *Сборка займет:* 15-30 минут
📱 *Статус заказа* вы можете отслеживать в этом чате

💫 *Спасибо за доверие! Мы уже начали собирать ваш заказ с особой заботой.*
            """

            keyboard = [
                [InlineKeyboardButton("🛍️ Продолжить покупки", callback_data="cat:catalog")],
                [InlineKeyboardButton("📦 Мои заказы", callback_data="orders:list")],
                [InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]
            ]

            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception:
                # Если не получается редактировать — отправим новое сообщение
                try:
                    await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception:
                    logger.debug("Не удалось отправить подтверждение заказа в чат")

        else:
            await query.edit_message_text(
                "❌ Произошла ошибка при создании заказа. Пожалуйста, попробуйте позже.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]])
            )

    except Exception as e:
        logger.error(f"❌ Ошибка в checkout_complete: {e}")
        try:
            await update.callback_query.answer("❌ Ошибка при завершении заказа", show_alert=True)
        except Exception:
            pass

# 🔥 ДОБАВЛЯЕМ ОТСУТСТВУЮЩУЮ ФУНКЦИЮ handle_checkout_callbacks
async def handle_checkout_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback'ов оформления заказа с вау-эффектами"""
    query = update.callback_query
    await query.answer()
    callback_data = query.data

    try:
        logger.info(f"🔔 Checkout callback: {callback_data}")
    except Exception:
        # Безопасное логирование без emoji на случай ошибки
        logger.info(f"Checkout callback: {callback_data}")

    # 🔥 ВАУ-ЭФФЕКТ: Волшебные переходы
    if callback_data == "checkout:delivery":
        await magical_step_transition(update, context, "contacts", "delivery", "Переходим к выбору доставки...")
        await asyncio.sleep(0.3)
        await checkout_delivery(update, context)

    elif callback_data == "checkout:payment":
        await magical_step_transition(update, context, "delivery", "payment", "Переходим к оплате заказа...")
        await asyncio.sleep(0.3)
        await checkout_payment(update, context)

    else:
        # Стандартная обработка для остальных callback'ов
        try:
            if callback_data == "checkout:start":
                await checkout_start(update, context)
            elif callback_data == "checkout:contacts":
                await checkout_contacts(update, context)
            elif callback_data == "checkout:input_email":
                await handle_email_input(update, context)
            elif callback_data == "checkout:share_phone":
                await handle_phone_sharing(update, context)
            elif callback_data == "checkout:manual_phone":
                await handle_manual_phone_input(update, context)
            elif callback_data.startswith("checkout:delivery_"):
                await handle_delivery_method(update, context)
            elif callback_data.startswith("checkout:payment_") or callback_data.startswith("checkout:payment"):
                # поддержка checkout:payment_online / checkout:payment_cash и т.п.
                await handle_payment_method(update, context)
            elif callback_data == "checkout:edit":
                # если есть логика редактирования — перенаправим на шаг контактов
                await checkout_contacts(update, context)
            elif callback_data == "noop":
                await query.answer()  # ничего не делаем
            else:
                logger.warning(f"❌ Неизвестный checkout callback: {callback_data}")
                await query.answer("Неизвестная команда")
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке callback: {e}")
            try:
                await query.answer("❌ Произошла ошибка при обработке команды", show_alert=True)
            except Exception:
                pass        


async def magical_step_transition(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                from_step: str, to_step: str, transition_text: str):
    """
    Волшебный переход между шагами оформления заказа
    """
    try:
        query = update.callback_query
        if not query:
            return

        # Магические фреймы перехода
        magic_frames = [
            f"✨ {transition_text}",
            f"🌟 {transition_text}", 
            f"⭐ {transition_text}",
            f"💫 {transition_text}"
        ]
        
        # Кратковременная анимация перехода
        for frame in magic_frames:
            try:
                await query.edit_message_text(
                    text=frame,
                    parse_mode='Markdown'
                )
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.debug(f"Магический переход: {e}")
                break
                
        logger.info(f"🔮 Магический переход: {from_step} → {to_step}")
        
    except Exception as e:
        logger.error(f"Ошибка магического перехода: {e}")       

def setup_checkout_handlers(application):
    """Регистрация обработчиков оформления заказа - ПОЛНОСТЬЮ ПЕРЕПИСАНА"""
    try:
        # 1) CallbackQuery для всех checkout callback'ов
        application.add_handler(CallbackQueryHandler(handle_checkout_callbacks, pattern="^checkout:.*"), group=0)

        # 2) Обработчики адресов - КОНКРЕТНЫЕ ФИЛЬТРЫ
        application.add_handler(
            MessageHandler(
                address_input_filter & filters.TEXT & ~filters.COMMAND, 
                handle_address_input
            ),
            group=0
        )
        
        application.add_handler(
            MessageHandler(
                pvz_address_input_filter & filters.TEXT & ~filters.COMMAND, 
                handle_pvz_address_input
            ),
            group=0
        )

        # 3) Обработчик контактов - ОТДЕЛЬНЫЙ ФИЛЬТР
        application.add_handler(
            MessageHandler(
                contact_input_filter & (filters.TEXT | filters.CONTACT) & ~filters.COMMAND, 
                handle_contact_message
            ),
            group=0
        )

        logger.info("✅ Checkout handlers успешно зарегистрированы (group=0)")
        logger.info("🔧 Используются новые специфичные фильтры")
        
    except Exception as e:
        logger.error(f"❌ Ошибка регистрации checkout handlers: {e}")
        raise