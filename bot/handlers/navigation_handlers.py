# handlers/navigation_handlers.py
import logging
import json
import unicodedata
import asyncio
import re
from typing import Dict
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from core.navigation import nav_system
from core.hybrid_cart import hybrid_cart as cart_manager
from core.database import db
from core.config import ADMIN_IDS
from keyboards.user_keyboards import user_keyboards, reply_kb

from core.agreements import user_has_agreement
from handlers.legal_handlers import send_agreement_prompt, AGREEMENT_VERSION, _check_agreement_valid

# Для проверки активной сессии чекаута
from core.checkout_flow import checkout_manager

logger = logging.getLogger(__name__)

# Определяем точные тексты reply-кнопок
REPLY_BUTTONS = [
    "🏠 Главная", "⬅️ Назад", "🗑️ Очистить корзину",
    "🛍️ Продолжить покупки", "🔍 Поиск"
]

# Создаем regex для точного совпадения (fullmatch)
nav_regex = r'^\s*(?:' + '|'.join(re.escape(button) for button in REPLY_BUTTONS) + r')\s*$'

# Маппинг callback'ов на слайды
CATEGORY_MAPPING = {
    'catalog': 'S_CATALOG',
    'quick_buy': 'S_QUICK_BUY',
    'aroma': 'S_AR',
    'aroma_rollers': 'S_AR_ROLLERS',
    'solid_perfumes': 'S_SP',
    'hair': 'S_HAIR',
    'body_care': 'S_BODY_CARE',
    'butters': 'S_BUTTERS',
    'lip_balms': 'S_LIPBALMS',
    'bath_bombs': 'S_BATHBOMBS',
    'home_goods': 'S_HOME_GOODS',
    'diffusers': 'S_DIFFUSERS',
    'sachets': 'S_SACHES',
    'beecandles': 'S_BEECANDLES',
    'concretecandles': 'S_CONCRETECANDLES',
    'car_perfumes': 'S_CAR',
    'promo': 'S_PROMO',
    'about': 'S_ABOUT',
    'contacts': 'S_CONTACTS',
    'info': 'S_INFO'
}


def normalize_text(s: str) -> str:
    if not s:
        return ""
    return unicodedata.normalize("NFKC", s).strip()


def _clear_ai_mode_in_context(context: ContextTypes.DEFAULT_TYPE):
    """
    Удобная функция для выключения AI режима в контексте пользователя.
    """
    try:
        context.user_data['ai_mode'] = False
    except Exception:
        try:
            context.user_data = {}
            context.user_data['ai_mode'] = False
        except Exception:
            pass
    # Убираем лишние ключи
    try:
        context.user_data.pop('ai_thread_root', None)
        context.user_data.pop('ai_mode_started_at', None)
    except Exception:
        pass


async def safe_answer_callback(query, text=None, show_alert=False, url=None, cache_time=None):
    """Безопасный вызов answer_callback_query с игнорированием ошибок устаревшего запроса."""
    try:
        await query.answer(text=text, show_alert=show_alert, url=url, cache_time=cache_time)
    except Exception as e:
        logger.debug(f"Callback answer error (likely expired): {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start - показывает главный слайд"""
    user_id = update.effective_user.id

    # При входе в старт — отключаем AI (защита)
    _clear_ai_mode_in_context(context)

    # Проверка согласия с учётом версии
    try:
        agreement = user_has_agreement(user_id)
    except Exception as e:
        logger.debug(f"Error checking user agreement for {user_id}: {e}")
        agreement = None

    if not _check_agreement_valid(agreement):
        # Согласие отсутствует или устарело
        context.user_data['post_agreement_action'] = {'type': 'start'}
        await send_agreement_prompt(update, context, post_action=context.user_data['post_agreement_action'])
        return

    cart_count = cart_manager.get_cart_count(user_id)

    await nav_system.show_slide(update, context, 'S01')
    try:
        if update.message:
            await update.message.reply_text(
                "👇 Используйте кнопки ниже для навигации:",
                reply_markup=reply_kb.get_main_menu()
            )
    except Exception:
        pass


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений от reply-кнопок"""
    try:
        # ===== AI MODE CHECK =====
        if context.user_data.get("ai_mode"):
            from handlers.ai_handlers import handle_ai_message
            await handle_ai_message(update, context)
            return
        # ========================

        if not update.message or not update.message.text:
            return

        user_id = update.message.from_user.id
        raw_text = update.message.text
        text = normalize_text(raw_text)

        logger.info(f"🔔 Обработчик reply-кнопок: пользователь {user_id}, текст: '{text}'")

        # ДИНАМИЧЕСКИЙ ИМПОРТ: проверяем состояния ввода контактов ИЗ checkout_handlers
        try:
            from handlers.checkout_handlers import user_input_states
        except Exception:
            user_input_states = {}

        if user_id in user_input_states:
            input_state = user_input_states[user_id]
            logger.info(
                f"🔕 NAV ПРОПУСКАЕТ: пользователь {user_id} в состоянии ввода контактов: {input_state}"
            )
            return  # Пусть сообщение обработает checkout

        # Проверяем активную сессию оформления
        try:
            session = checkout_manager.get_session(user_id)
        except Exception:
            session = None

        if session:
            navigation_commands = [
                "🏠 Главная", "Главная",
                "⬅️ Назад", "Назад",
                "🗑️ Очистить корзину", "Очистить корзину", "Очистить"
            ]
            if text.lower() not in [cmd.lower() for cmd in navigation_commands]:
                logger.info(
                    f"🔕 Пропуск nav-handler: у пользователя {user_id} активная сессия чекаута"
                )
                return

        # 🔥 Любое явное navigation-действие выключает AI
        _clear_ai_mode_in_context(context)

        # ===== REPLY КНОПКИ =====

        if text == "🗑️ Очистить корзину" or text.lower().startswith("очист"):
            if cart_manager.clear_cart(user_id):
                await update.message.reply_text("✅ Корзина очищена!")
                await show_editable_cart(update, context)
            else:
                await update.message.reply_text("❌ Ошибка очистки корзины")

        elif text == "🏠 Главная" or text.lower() == "главная":
            await start_command(update, context)

        elif text == "⬅️ Назад" or text.lower() == "назад":
            try:
                await nav_system.go_back(update, context)
            except Exception as e:
                logger.error(f"Ошибка навигации назад: {e}")
                await nav_system.show_slide(update, context, "S_CATALOG")

        elif text == "🛍️ Продолжить покупки" or text.lower().startswith("продолж"):
            await nav_system.show_slide(update, context, "S_CATALOG")

        elif text == "🔍 Поиск" or text.lower().startswith("поиск"):
            await update.message.reply_text("🔍 Функция поиска скоро будет доступна!")

        # Все остальные кнопки — inline callback'и

    except Exception as e:
        logger.exception(f"❌ Ошибка в обработчике reply-кнопок: {e}")
        try:
            await update.message.reply_text("Произошла ошибка. Попробуйте снова.")
        except Exception:
            pass


handle_reply_messages = handle_text_message


async def update_reply_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE, context_type: str, additional_data=None):
    """Обновляет reply-кнопки в зависимости от контекста"""
    try:
        # При любом обновлении UI - выключаем AI режим
        _clear_ai_mode_in_context(context)

        if context_type == "main_menu":
            keyboard = reply_kb.get_main_menu()
            text = "👇 Используйте кнопки ниже для навигации:"
        elif context_type == "catalog":
            keyboard = reply_kb.get_catalog_context()
            text = "👇 Быстрые действия:"
        elif context_type == "product":
            keyboard = reply_kb.get_product_context()
            text = "👇 Навигация:"
        elif context_type == "cart":
            has_items = additional_data if additional_data is not None else True
            keyboard = reply_kb.get_cart_context(has_items=has_items)
            text = "👇 Действия с корзины:"
        elif context_type == "checkout":
            keyboard = reply_kb.get_checkout_context()
            text = "👇 Оформление заказа:"
        else:
            return  # Неизвестный контекст

        # Отправляем новые кнопки
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=keyboard)
        elif hasattr(update, 'message') and update.message:
            await update.message.reply_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.exception(f"❌ Ошибка обновления reply-кнопок: {e}")


async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик навигационных callback'ов"""
    # При любом navigation callback - выключаем AI режим
    _clear_ai_mode_in_context(context)

    query = update.callback_query
    if not query:
        return
    await safe_answer_callback(query)

    callback_data = query.data
    logger.info(f"Получен callback: {callback_data}")

    try:
        # Навигация: домашняя и назад
        if callback_data.startswith('nav:'):
            if callback_data == 'nav:home':
                await nav_system.show_slide(update, context, 'S01')
            elif callback_data == 'nav:back':
                await nav_system.go_back(update, context)
            elif callback_data == 'nav:back_to_category':
                # Возврат к категории из которой пришли
                await handle_back_to_category(update, context)
            elif callback_data == 'nav:close_notification':
                # Закрыть уведомление - возвращаемся назад
                await nav_system.go_back(update, context)
            else:
                await safe_answer_callback(query)  # fallback

        # Обработка товаров в разных режимах
        elif callback_data.startswith('product:detail:'):
            # Переход в детальный режим товара
            product_id = callback_data.replace('product:detail:', '', 1)
            from core.product_cards import product_system
            await product_system.show_product(update, context, product_id, detail_mode=True)
        elif callback_data.startswith('product:brief:'):
            # Возврат к краткому режиму товара
            product_id = callback_data.replace('product:brief:', '', 1)
            from core.product_cards import product_system
            await product_system.show_product(update, context, product_id, detail_mode=False)

        # Категории
        elif callback_data.startswith('cat:'):
            category_type = callback_data.replace('cat:', '', 1)
            slide_id = CATEGORY_MAPPING.get(category_type)
            if slide_id:
                await nav_system.show_slide(update, context, slide_id)
            else:
                await safe_answer_callback(query, text=f"Категория {category_type} в разработке")

        # Товары (переход на страницу товара в кратком режиме)
        elif callback_data.startswith('prod:'):
            product_id = callback_data.replace('prod:', '', 1)
            from core.product_cards import product_system
            await product_system.show_product(update, context, product_id, detail_mode=False)

        # Корзина и связанные действия
        elif callback_data.startswith('cart:'):
            if callback_data.startswith('cart:silent_add:'):
                await handle_silent_add_to_cart(update, context)
            elif callback_data == 'cart:view':
                await show_editable_cart(update, context)
            elif callback_data == 'cart:clear':
                await handle_clear_cart(update, context)
            else:
                await safe_answer_callback(query)

    except Exception as e:
        logger.exception(f"❌ Ошибка в handle_navigation: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка обработки действия", show_alert=True)
        except Exception:
            pass


async def show_editable_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать редактируемую корзину"""
    # При показе корзины - выключаем AI
    _clear_ai_mode_in_context(context)
    try:
        user_id = update.effective_user.id

        # Получаем данные корзины
        cart_data = cart_manager.get_cart_items(user_id)

        if not cart_data.get('items'):
            # Корзина пуста
            text = "🛒 *Ваша корзина пуста*\n\nДобавьте товары из каталога!"
            keyboard = [
                [InlineKeyboardButton("📦 В каталог", callback_data="cat:catalog")],
                [InlineKeyboardButton("🏠 Главная", callback_data="nav:home")]
            ]
        else:
            # Формируем текст корзины с товарами
            text = "🛒 *Ваша корзина*\n\n"
            total = 0

            for item in cart_data['items']:
                item_total = item['price'] * item['quantity']
                total += item_total

                text += f"• *{item['title']}*\n"
                text += f"  {item['price']} руб. × {item['quantity']} = {item_total} руб.\n\n"

            text += f"💎 *Итого: {total} руб.*"

            # Создаем клавиатуру с кнопками управления для каждого товара
            keyboard = []
            for item in cart_data['items']:
                product_id = item['product_id']
                quantity = item['quantity']

                # Ряд с управлением количеством
                control_row = [
                    InlineKeyboardButton("➖", callback_data=f"cart:dec:{product_id}"),
                    InlineKeyboardButton(f"{quantity} шт.", callback_data=f"noop"),
                    InlineKeyboardButton("➕", callback_data=f"cart:inc:{product_id}"),
                    InlineKeyboardButton("🗑️ Удалить", callback_data=f"cart:remove:{product_id}")
                ]
                keyboard.append(control_row)

            # Основные кнопки корзины
            keyboard.append([InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout:start")])
            keyboard.append([InlineKeyboardButton("🗑️ Очистить корзину", callback_data="cart:clear")])
            keyboard.append([
                InlineKeyboardButton("📦 Продолжить покупки", callback_data="cat:catalog"),
                InlineKeyboardButton("🏠 Главная", callback_data="nav:home")
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправляем или редактируем сообщение
        if update.callback_query:
            message = update.callback_query.message

            # Если сообщение имеет фото (caption), отправляем новое текстовое сообщение
            if message.photo:
                await message.reply_text(
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else:
                # Если текстовое сообщение - редактируем его
                try:
                    await update.callback_query.edit_message_text(
                        text=text,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                except Exception as edit_error:
                    logger.debug(f"Не удалось отредактировать сообщение: {edit_error}")
                    # Fallback: отправляем новое сообщение
                    await message.reply_text(
                        text=text,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
        else:
            # Если это не callback (например, команда /cart), отправляем новое сообщение
            await update.message.reply_text(
                text=text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"❌ Ошибка показа корзины: {e}")
        if update.callback_query:
            await safe_answer_callback(update.callback_query, text="❌ Ошибка загрузки корзины")


async def handle_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик перехода в детальный режим товара"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id
        callback_data = query.data

        logger.info(f"🔔 Переход в детальный режим товара: {callback_data}")

        # Извлекаем product_id из callback_data: product:detail:AR01
        if callback_data.startswith('product:detail:'):
            product_id = callback_data.replace('product:detail:', '', 1)

            # Показываем товар в детальном режиме
            from core.product_cards import product_system
            await product_system.show_product(update, context, product_id, detail_mode=True)

        else:
            await safe_answer_callback(query, text="❌ Неизвестный формат товара")

    except Exception as e:
        logger.error(f"❌ Ошибка перехода в детальный режим: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка загрузки товара")
        except Exception:
            pass


async def handle_product_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик возврата к краткому режиму товара"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id
        callback_data = query.data

        logger.info(f"🔔 Возврат к краткому режиму товара: {callback_data}")

        # Извлекаем product_id из callback_data: product:brief:AR01
        if callback_data.startswith('product:brief:'):
            product_id = callback_data.replace('product:brief:', '', 1)

            # Показываем товар в кратком режиме
            from core.product_cards import product_system
            await product_system.show_product(update, context, product_id, detail_mode=False)

        else:
            await safe_answer_callback(query, text="❌ Неизвестный формат товара")

    except Exception as e:
        logger.error(f"❌ Ошибка возврата к краткому режиму: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка загрузки товара")
        except Exception:
            pass


async def handle_cart_quantity_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик изменения количества товара в корзине"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id
        callback_data = query.data

        # Разбираем callback_data: cart:inc:AR01 или cart:dec:AR01
        parts = callback_data.split(":")
        action, product_id = parts[1], parts[2]

        current_quantity = cart_manager.get_product_quantity(user_id, product_id)

        if action == "inc":  # Увеличить количество
            new_quantity = current_quantity + 1
            success = cart_manager.update_cart_quantity(user_id, product_id, new_quantity)
            if success:
                await safe_answer_callback(query, text=f"✅ +1 (всего: {new_quantity})")
            else:
                await safe_answer_callback(query, text="❌ Ошибка увеличения количества")

        elif action == "dec":  # Уменьшить количество
            if current_quantity > 1:
                new_quantity = current_quantity - 1
                success = cart_manager.update_cart_quantity(user_id, product_id, new_quantity)
                if success:
                    await safe_answer_callback(query, text=f"➖ -1 (всего: {new_quantity})")
                else:
                    await safe_answer_callback(query, text="❌ Ошибка уменьшения количества")
            else:
                # Если количество 1, то удаляем товар
                success = cart_manager.remove_from_cart(user_id, product_id)
                if success:
                    await safe_answer_callback(query, text="🗑️ Товар удален из корзины")
                else:
                    await safe_answer_callback(query, text="❌ Ошибка удаления товара")

        # Всегда обновляем отображение корзины после изменения
        await show_editable_cart(update, context)

    except Exception as e:
        logger.error(f"❌ Ошибка изменения количества: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка изменения количества")
        except Exception:
            pass


async def handle_remove_from_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик удаления товара из корзины"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id
        product_id = query.data.split(":")[2]  # cart:remove:AR01

        success = cart_manager.remove_from_cart(user_id, product_id)
        if success:
            await safe_answer_callback(query, text="🗑️ Товар удален из корзины")
        else:
            await safe_answer_callback(query, text="❌ Ошибка удаления товара")

        # Обновляем корзину после удаления
        await show_editable_cart(update, context)

    except Exception as e:
        logger.error(f"❌ Ошибка удаления товара: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка удаления товара")
        except Exception:
            pass


async def handle_clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик полной очистки корзины"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id

        success = cart_manager.clear_cart(user_id)
        if success:
            await safe_answer_callback(query, text="🗑️ Корзина полностью очищена")
            await show_editable_cart(update, context)
        else:
            await safe_answer_callback(query, text="❌ Ошибка очистки корзины")

    except Exception as e:
        logger.error(f"❌ Ошибка очистки корзины: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка очистки корзины")
        except Exception:
            pass


async def handle_silent_add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик с ДВОЙНОЙ АНИМАЦИЕЙ: кнопка + полет"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id

        # Извлекаем product_id
        if query.data.startswith('cart:silent_add:'):
            product_id = query.data.replace('cart:silent_add:', '', 1)
        else:
            product_id = query.data.replace('cart:add:', '', 1)

        # Попытка анимации (не критично)
        try:
            from core.animations import simple_button_animation, send_typing

            animation_message = await query.message.reply_text("🛒")
            button_task = asyncio.create_task(simple_button_animation(update, context))
            await send_typing(context.bot, query.message.chat_id, "typing", 0.2)
            await button_task
            await asyncio.sleep(2)
            try:
                await animation_message.delete()
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"Анимация не сработала: {e}")

        # Основная операция
        result = cart_manager.add_to_cart(user_id, product_id)

        if result["success"]:
            item_quantity = cart_manager.get_product_quantity(user_id, product_id)

            # Обновляем интерфейс товара
            from core.product_cards import product_system
            from core.json_config import json_config

            product = json_config.get_product_by_id(product_id)
            if not product:
                await safe_answer_callback(query, text="❌ Товар не найден")
                return

            current_text = query.message.text or query.message.caption or ""
            is_detail_mode = "ТЕХНИЧЕСКИЕ ДЕТАЛИ" in current_text

            if is_detail_mode:
                new_text = product_system._get_detail_text(product, item_quantity)
                new_keyboard = product_system._create_detail_keyboard(product_id, item_quantity)
            else:
                new_text = product_system._get_brief_text(product, item_quantity)
                new_keyboard = product_system._create_brief_keyboard(product_id, item_quantity)

            try:
                if query.message.photo:
                    await query.edit_message_caption(
                        caption=new_text,
                        parse_mode='Markdown',
                        reply_markup=new_keyboard
                    )
                else:
                    await query.edit_message_text(
                        text=new_text,
                        parse_mode='Markdown',
                        reply_markup=new_keyboard
                    )

                await safe_answer_callback(query, text=f"✅ ×{item_quantity}")

            except Exception as e:
                logger.error(f"Ошибка обновления: {e}")
                try:
                    await safe_answer_callback(query, text="✅ Добавлено!")
                except Exception:
                    pass

        else:
            await safe_answer_callback(query, text="❌ Ошибка добавления")

    except Exception as e:
        logger.error(f"Ошибка в handle_silent_add_to_cart: {e}")
        try:
            await safe_answer_callback(update.callback_query, text="❌ Ошибка добавления")
        except Exception:
            pass


async def handle_cart_view_with_animation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показ корзины с анимацией"""
    _clear_ai_mode_in_context(context)
    query = update.callback_query
    await safe_answer_callback(query, text="🔄 Загружаем корзину...")
    await asyncio.sleep(0.2)
    await show_editable_cart(update, context)


async def handle_smart_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Умный возврат - анализирует контекст и решает куда вернуться"""
    _clear_ai_mode_in_context(context)
    query = update.callback_query

    try:
        await nav_system.go_back(update, context)
    except Exception as e:
        logger.warning(f"Навигационная система не смогла вернуться назад: {e}")

        # Fallback логика
        try:
            current_text = query.message.text if query and query.message else ""

            if "корзина" in current_text.lower():
                await nav_system.show_slide(update, context, 'S_CATALOG')
            elif any(word in current_text.lower() for word in ['цена', 'руб.', 'описание']):
                await nav_system.show_slide(update, context, 'S_CATALOG')
            else:
                await nav_system.show_slide(update, context, 'S01')

        except Exception as fallback_error:
            logger.error(f"Fallback навигация также не сработала: {fallback_error}")
            await nav_system.show_slide(update, context, 'S01')


async def handle_category_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик клика по категории с МАГИЧЕСКОЙ АНИМАЦИЕЙ"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id
        callback_data = query.data

        logger.info(f"🔔 Обработка категории: {callback_data}")

        # Анимация перехода
        try:
            magic_frames = ["🔮", "✨", "⭐", "🌟"]
            for frame in magic_frames:
                try:
                    await query.edit_message_text(frame)
                    await asyncio.sleep(0.15)
                except:
                    break
        except Exception as e:
            logger.debug(f"Магическая анимация не сработала: {e}")

        if callback_data.startswith('cat:'):
            category_type = callback_data.replace('cat:', '', 1)
            slide_id = CATEGORY_MAPPING.get(category_type)

            if slide_id:
                nav_system.set_current_category(user_id, slide_id)
                logger.info(f"📁 Сохранена категория {slide_id} для пользователя {user_id}")
                await nav_system.show_slide(update, context, slide_id)
            else:
                await safe_answer_callback(query, text=f"Категория {category_type} в разработке")
        else:
            await safe_answer_callback(query, text="Неизвестная команда категории")

    except Exception as e:
        logger.error(f"❌ Ошибка обработки категории: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка загрузки категории")
        except Exception:
            pass


async def handle_back_to_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Умный возврат к категории из товара"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id

        current_category = nav_system.get_current_category(user_id)
        logger.info(f"🔄 Возврат к категории {current_category} для пользователя {user_id}")

        if current_category == 'S_CATALOG':
            user_state = db.get_user_state(user_id)
            if user_state and user_state.get('previous_slides'):
                previous_slides = user_state.get('previous_slides', [])
                logger.info(f"📚 История пользователя {user_id}: {previous_slides}")

                for slide_id in reversed(previous_slides):
                    if not slide_id.startswith('product:') and slide_id != 'S01' and slide_id in CATEGORY_MAPPING.values():
                        current_category = slide_id
                        logger.info(f"🎯 Найдена категория в истории: {current_category}")
                        break

        await nav_system.show_slide(update, context, current_category)

        try:
            await safe_answer_callback(query, text=f"Возврат в {current_category}", show_alert=False)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"❌ Ошибка возврата к категории: {e}")
        await nav_system.show_slide(update, context, 'S_CATALOG')
        try:
            await safe_answer_callback(update.callback_query, text="Возврат в каталог", show_alert=False)
        except Exception:
            pass


async def handle_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для кнопок-заглушек (ничего не делают)"""
    _clear_ai_mode_in_context(context)
    query = update.callback_query
    await safe_answer_callback(query)  # Просто закрываем уведомление


async def handle_product_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик клика по продукту из категории"""
    _clear_ai_mode_in_context(context)
    try:
        query = update.callback_query
        await safe_answer_callback(query)

        user_id = query.from_user.id
        callback_data = query.data

        logger.info(f"🔔 Обработка продукта: {callback_data}")

        # Извлекаем product_id из callback_data: prod:AR01
        if callback_data.startswith('prod:'):
            product_id = callback_data.replace('prod:', '', 1)

            # Анимация перехода к товару
            try:
                magic_frames = ["✨", "🌟", "⭐", "💫"]
                for frame in magic_frames:
                    try:
                        await query.edit_message_text(frame)
                        await asyncio.sleep(0.15)
                    except:
                        break
            except Exception as e:
                logger.debug(f"Анимация не сработала: {e}")

            # Показываем товар в кратком режиме
            from core.product_cards import product_system
            await product_system.show_product(update, context, product_id, detail_mode=False)

        else:
            await safe_answer_callback(query, text="❌ Неизвестный формат продукта")

    except Exception as e:
        logger.error(f"❌ Ошибка обработки продукта: {e}")
        try:
            await safe_answer_callback(query, text="❌ Ошибка загрузки товара")
        except Exception:
            pass


def setup_navigation_handlers(application):
    """Регистрация обработчиков навигации (регистрируем в group=1 чтобы AI short-circuit сработал раньше)"""

    # Callback handlers (group=1)
    application.add_handler(CallbackQueryHandler(handle_navigation, pattern="^nav:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_category_click, pattern="^cat:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_silent_add_to_cart, pattern="^cart:silent_add:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_smart_back, pattern="^nav:back"), group=1)

    # Product handlers
    application.add_handler(CallbackQueryHandler(handle_product_click, pattern="^prod:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_product_detail, pattern="^product:detail:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_product_brief, pattern="^product:brief:"), group=1)

    # Cart handlers
    application.add_handler(CallbackQueryHandler(handle_cart_quantity_change, pattern="^cart:(inc|dec):"), group=1)
    application.add_handler(CallbackQueryHandler(handle_remove_from_cart, pattern="^cart:remove:"), group=1)
    application.add_handler(CallbackQueryHandler(handle_clear_cart, pattern="^cart:clear$"), group=1)
    application.add_handler(CallbackQueryHandler(show_editable_cart, pattern="^cart:view$"), group=1)
    application.add_handler(CallbackQueryHandler(handle_noop, pattern="^noop$"), group=1)

    # Message handler for reply-buttons (use Regex nav_regex)
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(nav_regex), handle_reply_messages), group=1)

    # Command /start (group=1)
    application.add_handler(CommandHandler("start", start_command), group=1)

    logger.info("✅ Navigation handlers зарегистрированы")